import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status

from ... import article_state, audit, emailer, notifications, storage, uploads
from ...deps import get_current_user, get_session, require_roles
from ...schemas import (
    AbstractAnnounceRequest,
    AbstractReviewRequest,
    ArticleAssignRequest,
    ArticleCreate,
    ArticleFullPaperRequest,
    ArticleOut,
    ArticleReviewOut,
    ArticleUpdate,
    ArticleVersionOut,
    AssignReviewersRequest,
    DownloadUrlOut,
    FullPaperAnnounceRequest,
    FullPaperReviewRequest,
    UploadResponse,
    UserCtx,
)
from ..users import repository as users_repo
from . import repository as repo

router = APIRouter(prefix="/articles", tags=["articles"])

# Long enough to click through and open the PDF, short enough that a link
# pasted into a chat is dead before it can be shared onward.
DOWNLOAD_URL_TTL_SECONDS = 300


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "article not found")


def _current_review_phase(article_status: str) -> str | None:
    """Which phase is actively under review, or None if the article is not in
    a reviewable state."""
    if article_status in article_state.ABSTRACT_REVIEWABLE:
        return "abstract"
    if article_status in article_state.FULL_PAPER_REVIEWABLE:
        return "full_paper"
    return None


@router.get("", response_model=list[ArticleOut])
async def list_articles(
    include_deleted: bool = False,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> list[ArticleOut]:
    if include_deleted and user.role != "admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only admin may list archived articles"
        )
    articles = await repo.list_articles_for(
        session, user.role, user.id_user, include_deleted=include_deleted
    )
    out = []
    for a in articles:
        reviewers = await repo.list_reviewer_ids(session, a.id_article)
        out.append(repo.to_article_out(a, user.role, reviewers))
    return out


@router.post(
    "",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("author"))],
)
async def create_article(
    body: ArticleCreate,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    # A sub-theme must belong to the topic it was submitted under. Without this
    # an author could send any string, and the value is displayed to reviewers
    # and editors as if it were one of the published themes.
    sub_topic = (body.sub_topic or "").strip() or None
    if sub_topic is not None:
        if body.id_topic is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "a sub-theme requires a topic to be chosen as well",
            )
        allowed = await repo.sub_topic_names(session, body.id_topic)
        if sub_topic not in allowed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "that sub-theme does not belong to the chosen topic",
            )

    article = await repo.create_article(
        session,
        title=body.title,
        authors=body.authors,
        abstract=body.abstract,
        keywords=body.keywords,
        abstract_file_path=body.abstract_file_path,
        id_topic=body.id_topic,
        sub_topic=sub_topic,
        id_user=uuid.UUID(user.id_user),
    )
    await repo.add_article_version(
        session,
        id_article=article.id_article,
        phase="abstract",
        file_path=body.abstract_file_path,
        submitted_by=uuid.UUID(user.id_user),
    )
    await session.flush()
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.created",
        entity_type="article",
        entity_id=article.id_article,
        detail={"title": article.title},
    )
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.version_submitted",
        entity_type="article",
        entity_id=article.id_article,
        detail={"phase": "abstract"},
    )
    # A receipt for the author. Submitting into silence is the most common
    # complaint about systems like this, and it is also how a failed upload goes
    # unnoticed.
    author_email = await repo.get_user_email(session, article.id_user)
    subject, body_text = notifications.author_abstract_received(article.title)
    background_tasks.add_task(emailer.send, author_email, subject, body_text)

    reviewers = await repo.list_reviewer_ids(session, article.id_article)
    return repo.to_article_out(article, "author", reviewers)


async def _check_view_permission(session, article, user: UserCtx) -> None:
    if user.role == "author" and str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if user.role == "SC" and not await repo.is_assigned(
        session, article.id_article, uuid.UUID(user.id_user)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


@router.get("/{id_article}", response_model=ArticleOut)
async def get_article(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    # Admin can inspect an archive; everyone else sees a 404 for it.
    article = await repo.get_article(
        session, id_article, include_deleted=(user.role == "admin")
    )
    if article is None:
        raise _not_found()
    await _check_view_permission(session, article, user)
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, user.role, reviewers)


@router.put("/{id_article}", response_model=ArticleOut)
@router.patch("/{id_article}", response_model=ArticleOut)
async def update_article(
    id_article: uuid.UUID,
    body: ArticleUpdate,
    user: UserCtx = Depends(require_roles("author")),
    session=Depends(get_session),
) -> ArticleOut:
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if article.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, "article already in review, cannot be edited")

    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(article, key, value)
    await session.flush()
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "author", reviewers)


@router.delete(
    "/{id_article}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_article(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> None:
    """Archives the article. Versions, reviews and assignments are kept — the
    review record is the reason this is a soft delete."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    await repo.soft_delete_article(session, article)
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.deleted",
        entity_type="article",
        entity_id=id_article,
        detail={},
    )


@router.post(
    "/{id_article}/restore",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def restore_article(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    article = await repo.get_article(session, id_article, include_deleted=True)
    if article is None:
        raise _not_found()
    if article.deleted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "article is not archived")
    await repo.restore_article(session, article)
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.restored",
        entity_type="article",
        entity_id=id_article,
        detail={},
    )
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "admin", reviewers)


async def _assign_reviewers(
    session,
    article,
    id_reviewers: list[uuid.UUID],
    override_coi: bool,
    background_tasks: BackgroundTasks,
    id_actor: uuid.UUID,
) -> None:
    """Validates and assigns reviewers, emailing each newly assigned one.

    Shared by POST /reviewers and the single-reviewer POST /assign shortcut.
    Raises 400 for non-SC users and 409 for a COI the caller did not override.
    """
    author_institution = await repo.get_user_institution(session, article.id_user)

    for id_reviewer in id_reviewers:
        role = await repo.get_user_role(session, id_reviewer)
        if role != "SC":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{id_reviewer} does not belong to a SC user"
            )
        if not override_coi:
            reviewer_institution = await repo.get_user_institution(session, id_reviewer)
            if article_state.institutions_conflict(author_institution, reviewer_institution):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"conflict of interest: reviewer {id_reviewer} shares the author's "
                    f"institution ({reviewer_institution}); pass override_coi=true to assign anyway",
                )

    newly_assigned = []
    for id_reviewer in id_reviewers:
        if await repo.add_reviewer(session, article.id_article, id_reviewer):
            newly_assigned.append(id_reviewer)

    if article.status == "submitted":
        article.status = "assigned_to_sc"
    await session.flush()

    for id_reviewer in newly_assigned:
        await audit.record(
            session,
            id_actor=id_actor,
            action="reviewer.assigned",
            entity_type="article",
            entity_id=article.id_article,
            detail={"id_reviewer": str(id_reviewer), "coi_overridden": override_coi},
        )
        email = await repo.get_user_email(session, id_reviewer)
        subject, body = notifications.reviewer_assigned(article.title)
        background_tasks.add_task(emailer.send, email, subject, body)

    if newly_assigned:
        # The author is told their submission has entered review. Without this
        # they see nothing between submitting and the decision.
        author_email = await repo.get_user_email(session, article.id_user)
        subject, body = notifications.author_status_change(article.title, article.status)
        background_tasks.add_task(emailer.send, author_email, subject, body)


@router.post(
    "/{id_article}/assign",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def assign_article(
    id_article: uuid.UUID,
    body: ArticleAssignRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """Single-reviewer shortcut, kept for backward compatibility.
    Prefer POST /articles/{id}/reviewers for assigning several at once."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if article.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot assign in status {article.status}")

    await _assign_reviewers(
        session,
        article,
        [body.id_sc],
        body.override_coi,
        background_tasks,
        uuid.UUID(user.id_user),
    )
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)


@router.post(
    "/{id_article}/review",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("SC"))],
)
async def review_article(
    id_article: uuid.UUID,
    body: AbstractReviewRequest | FullPaperReviewRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """An assigned SC records their own review of the current file version.

    Body shape must match the phase: `{"accept": bool}` for an abstract,
    `{"decision": "accept"|"revision"}` for a full paper. When the last
    assigned reviewer submits, the article advances to *_review_complete and
    the EIC can announce.
    """
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    id_reviewer = uuid.UUID(user.id_user)
    if not await repo.is_assigned(session, id_article, id_reviewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not an assigned reviewer")

    phase = _current_review_phase(article.status)
    if phase is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot review in status {article.status}")

    if phase == "abstract":
        if not isinstance(body, AbstractReviewRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"article is in abstract review (status {article.status}); "
                'expected an abstract review body: {"accept": bool}',
            )
        decision = "accept" if body.accept else "reject"
    else:
        if not isinstance(body, FullPaperReviewRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"article is in full-paper review (status {article.status}); "
                'expected a full-paper review body: {"decision": "accept"|"revision"}',
            )
        decision = body.decision

    version = await repo.latest_version(session, id_article, phase)
    if version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"article has no {phase} version to review"
        )
    if await repo.has_reviewed(session, version.id_version, id_reviewer):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "you have already reviewed this version"
        )

    await repo.add_review(
        session,
        id_version=version.id_version,
        id_reviewer=id_reviewer,
        decision=decision,
        notes=body.notes,
    )
    await audit.record(
        session,
        id_actor=id_reviewer,
        action="review.submitted",
        entity_type="article",
        entity_id=id_article,
        detail={
            "id_reviewer": str(id_reviewer),
            "id_version": str(version.id_version),
            "decision": decision,
        },
    )

    previous_status = article.status
    if await repo.all_assigned_have_reviewed(session, id_article, version.id_version):
        article.status = article_state.review_complete_status_for_phase(phase)
    await session.flush()
    if article.status != previous_status:
        await audit.record(
            session,
            id_actor=id_reviewer,
            action="article.status_changed",
            entity_type="article",
            entity_id=id_article,
            detail={"from": previous_status, "to": article.status},
        )
        # The phase just closed, so tell whoever announces decisions. Otherwise
        # an editor has to keep reopening the dashboard to notice.
        subject, body_text = notifications.editor_reviews_complete(article.title, phase)
        for email in await users_repo.list_emails_by_role(session, "EIC"):
            background_tasks.add_task(emailer.send, email, subject, body_text)

    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "SC", reviewers)


@router.post(
    "/{id_article}/announce",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def announce_article(
    id_article: uuid.UUID,
    body: AbstractAnnounceRequest | FullPaperAnnounceRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """EIC weighs the reviews and announces the outcome to the author.

    Abstract phase: `{"decision": "accept"|"reject"}`.
    Full-paper phase: `{"decision": "accept"|"revision"}` plus
    `id_recommended_journal` when accepting.

    Requires every assigned reviewer to have submitted (status *_review_complete).
    """
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    if article.status in article_state.ABSTRACT_ANNOUNCEABLE:
        phase = "abstract"
        if not isinstance(body, AbstractAnnounceRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                'article is in abstract review; expected {"decision": "accept"|"reject"}',
            )
        decision = body.decision
    elif article.status in article_state.FULL_PAPER_ANNOUNCEABLE:
        phase = "full_paper"
        if not isinstance(body, FullPaperAnnounceRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                'article is in full-paper review; expected '
                '{"decision": "accept"|"revision", "id_recommended_journal": uuid}',
            )
        decision = body.decision
        if body.id_recommended_journal is not None:
            article.id_recommended_journal = body.id_recommended_journal
    else:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot announce in status {article.status}"
        )

    previous_status = article.status
    article.status = article_state.announced_status_for(phase, decision)
    await session.flush()
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.status_changed",
        entity_type="article",
        entity_id=id_article,
        detail={"from": previous_status, "to": article.status},
    )

    author_email = await repo.get_user_email(session, article.id_user)
    subject, body = notifications.author_status_change(article.title, article.status)
    background_tasks.add_task(emailer.send, author_email, subject, body)
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)


@router.post(
    "/{id_article}/full-paper",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("author"))],
)
async def submit_full_paper(
    id_article: uuid.UUID,
    body: ArticleFullPaperRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """Author submits the full paper after being announced as abstract_accepted."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if article.status != "abstract_accepted":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot submit full paper in status {article.status}"
        )

    previous_status = article.status
    article.full_paper_file_path = body.full_paper_file_path
    article.status = "full_paper_submitted"
    await repo.add_article_version(
        session,
        id_article=id_article,
        phase="full_paper",
        file_path=body.full_paper_file_path,
        submitted_by=uuid.UUID(user.id_user),
    )
    await session.flush()
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.status_changed",
        entity_type="article",
        entity_id=id_article,
        detail={"from": previous_status, "to": article.status},
    )
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.version_submitted",
        entity_type="article",
        entity_id=id_article,
        detail={"phase": "full_paper"},
    )

    author_email = await repo.get_user_email(session, article.id_user)
    subject, body = notifications.author_full_paper_received(article.title)
    background_tasks.add_task(emailer.send, author_email, subject, body)

    reviewers = await repo.list_reviewer_ids(session, id_article)
    subject, body = notifications.reviewer_full_paper_ready(article.title, revised=False)
    for id_reviewer in reviewers:
        reviewer_email = await repo.get_user_email(session, id_reviewer)
        background_tasks.add_task(emailer.send, reviewer_email, subject, body)
    return repo.to_article_out(article, "author", reviewers)


@router.post(
    "/{id_article}/revision",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("author"))],
)
async def submit_revision(
    id_article: uuid.UUID,
    body: ArticleFullPaperRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """Author resubmits the full paper after SC/EIC returned revision_needed."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if article.status != "revision_needed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot resubmit revision in status {article.status}"
        )

    previous_status = article.status
    article.full_paper_file_path = body.full_paper_file_path
    article.status = "full_paper_submitted"
    await repo.add_article_version(
        session,
        id_article=id_article,
        phase="full_paper",
        file_path=body.full_paper_file_path,
        submitted_by=uuid.UUID(user.id_user),
    )
    await session.flush()
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.status_changed",
        entity_type="article",
        entity_id=id_article,
        detail={"from": previous_status, "to": article.status},
    )
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.version_submitted",
        entity_type="article",
        entity_id=id_article,
        detail={"phase": "full_paper"},
    )

    author_email = await repo.get_user_email(session, article.id_user)
    subject, body = notifications.author_revision_received(article.title)
    background_tasks.add_task(emailer.send, author_email, subject, body)

    reviewers = await repo.list_reviewer_ids(session, id_article)
    subject, body = notifications.reviewer_full_paper_ready(article.title, revised=True)
    for id_reviewer in reviewers:
        reviewer_email = await repo.get_user_email(session, id_reviewer)
        background_tasks.add_task(emailer.send, reviewer_email, subject, body)
    return repo.to_article_out(article, "author", reviewers)


@router.post("/{id_article}/upload", response_model=UploadResponse)
async def upload_article_file(
    id_article: uuid.UUID,
    file: UploadFile = File(...),
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> UploadResponse:
    """Uploads a PDF against an existing article and returns its storage path.

    Superseded by `POST /uploads`, which does not require the article to exist
    first — kept for callers already using this path. Validation and storage
    are shared via `src/uploads.py` so the two cannot drift.
    """
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")

    path = await uploads.store_pdf(file)
    return UploadResponse(file_path=path)


@router.get("/{id_article}/versions", response_model=list[ArticleVersionOut])
async def list_article_versions(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> list[ArticleVersionOut]:
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    await _check_view_permission(session, article, user)
    versions = await repo.list_versions(session, id_article)
    return [ArticleVersionOut.model_validate(v) for v in versions]


@router.get("/{id_article}/versions/{id_version}/download", response_model=DownloadUrlOut)
async def download_article_version(
    id_article: uuid.UUID,
    id_version: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> DownloadUrlOut:
    """Mints a short-lived URL for one version's PDF.

    Papers are stored privately, so a stored path is useless on its own — this
    endpoint is the only way to read one. The permission check is the same one
    that guards viewing the article, applied before any URL exists: an author
    reaches only their own submissions, a reviewer only what they are assigned.

    The version must belong to the article in the path. Without that check, an
    id from someone else's submission would be signed for anyone holding one
    valid article id of their own.
    """
    article = await repo.get_article(
        session, id_article, include_deleted=(user.role == "admin")
    )
    if article is None:
        raise _not_found()
    await _check_view_permission(session, article, user)

    version = await repo.get_version(session, id_version)
    if version is None or version.id_article != id_article:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")

    try:
        url = await storage.client.presigned_url(version.file_path, DOWNLOAD_URL_TTL_SECONDS)
    except storage.StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    except storage.UnsignableFileError as exc:
        # A row pointing outside the configured bucket cannot be served. Name
        # the reason so an organiser can tell it is that one submission, not
        # the download feature, that is broken.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    # Who read a confidential paper, and when, is worth keeping.
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.file_downloaded",
        entity_type="article",
        entity_id=id_article,
        detail={
            "id_version": str(id_version),
            "phase": version.phase,
            "version_number": version.version_number,
        },
    )
    return DownloadUrlOut(download_url=url, expires_in=DOWNLOAD_URL_TTL_SECONDS)


@router.post(
    "/{id_article}/reviewers",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def assign_reviewers(
    id_article: uuid.UUID,
    body: AssignReviewersRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """EIC assigns one or more SC reviewers. Additive: call again to add more.
    Re-assigning an already-assigned reviewer is a no-op, not an error."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if article.status not in ("submitted", "assigned_to_sc", "full_paper_submitted"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot assign reviewers in status {article.status}"
        )

    await _assign_reviewers(
        session,
        article,
        body.id_reviewers,
        body.override_coi,
        background_tasks,
        uuid.UUID(user.id_user),
    )
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)


@router.delete(
    "/{id_article}/reviewers/{id_reviewer}",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def unassign_reviewer(
    id_article: uuid.UUID,
    id_reviewer: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """Unassigns a reviewer — the escape hatch for an unresponsive reviewer
    blocking the announce gate. Any review they already submitted is kept.

    If the remaining assigned reviewers have all submitted for the current
    version, the article advances to *_review_complete, exactly as it would
    have when the last review landed.
    """
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if not await repo.remove_reviewer(session, id_article, id_reviewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reviewer not assigned to this article")

    previous_status = article.status
    phase = _current_review_phase(article.status)
    if phase is not None:
        version = await repo.latest_version(session, id_article, phase)
        if version is not None and await repo.all_assigned_have_reviewed(
            session, id_article, version.id_version
        ):
            article.status = article_state.review_complete_status_for_phase(phase)
    await session.flush()
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="reviewer.unassigned",
        entity_type="article",
        entity_id=id_article,
        detail={"id_reviewer": str(id_reviewer)},
    )
    if article.status != previous_status:
        await audit.record(
            session,
            id_actor=uuid.UUID(user.id_user),
            action="article.status_changed",
            entity_type="article",
            entity_id=id_article,
            detail={"from": previous_status, "to": article.status},
        )

    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)


@router.get("/{id_article}/reviews", response_model=list[ArticleReviewOut])
async def list_article_reviews(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> list[ArticleReviewOut]:
    """Blind review: an SC sees only their own reviews, EIC/admin see all.
    Authors see none — reviewer feedback reaches them via the EIC's
    announcement, not directly."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    if user.role == "author":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "authors cannot read reviews directly")

    only_reviewer = uuid.UUID(user.id_user) if user.role == "SC" else None
    if only_reviewer is not None and not await repo.is_assigned(
        session, id_article, only_reviewer
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not an assigned reviewer")

    reviews = await repo.list_reviews_for_article(session, id_article, only_reviewer)
    return [ArticleReviewOut.model_validate(r) for r in reviews]
