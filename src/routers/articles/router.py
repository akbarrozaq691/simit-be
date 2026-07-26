import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import ValidationError

from ... import article_state, emailer, storage
from ...deps import get_current_user, get_session, require_roles
from ...schemas import (
    AbstractReviewRequest,
    ArticleAssignRequest,
    ArticleCreate,
    ArticleFullPaperRequest,
    ArticleOut,
    ArticleUpdate,
    ArticleVersionOut,
    FullPaperReviewRequest,
    UploadResponse,
    UserCtx,
)
from . import repository as repo

router = APIRouter(prefix="/articles", tags=["articles"])


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "article not found")


@router.get("", response_model=list[ArticleOut])
async def list_articles(
    user: UserCtx = Depends(get_current_user), session=Depends(get_session)
) -> list[ArticleOut]:
    articles = await repo.list_articles_for(session, user.role, user.id_user)
    return [repo.to_article_out(a, user.role) for a in articles]


@router.post(
    "",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("author"))],
)
async def create_article(
    body: ArticleCreate, user: UserCtx = Depends(get_current_user), session=Depends(get_session)
) -> ArticleOut:
    article = await repo.create_article(
        session,
        title=body.title,
        authors=body.authors,
        abstract=body.abstract,
        keywords=body.keywords,
        abstract_file_path=body.abstract_file_path,
        id_topic=body.id_topic,
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
    return repo.to_article_out(article, "author")


def _check_view_permission(article, user: UserCtx) -> None:
    if user.role == "author" and str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if user.role == "SC" and str(article.id_sc) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


@router.get("/{id_article}", response_model=ArticleOut)
async def get_article(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    _check_view_permission(article, user)
    return repo.to_article_out(article, user.role)


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
    return repo.to_article_out(article, "author")


@router.delete(
    "/{id_article}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_article(id_article: uuid.UUID, session=Depends(get_session)) -> None:
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    await session.delete(article)
    await session.flush()


@router.post(
    "/{id_article}/assign",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def assign_article(
    id_article: uuid.UUID,
    body: ArticleAssignRequest,
    background_tasks: BackgroundTasks,
    session=Depends(get_session),
) -> ArticleOut:
    """EIC delivers the abstract to a SC reviewer."""
    sc_role = await repo.get_user_role(session, body.id_sc)
    if sc_role != "SC":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "id_sc must belong to a SC user")

    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if article.status not in ("submitted", "revision_needed"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot assign in status {article.status}")

    article.id_sc = body.id_sc
    article.status = "assigned_to_sc"
    await session.flush()

    sc_email = await repo.get_user_email(session, article.id_sc)
    background_tasks.add_task(
        emailer.send,
        sc_email,
        "New abstract assigned for review",
        f"Article '{article.title}' has been assigned to you for review.",
    )
    return repo.to_article_out(article, "EIC")


@router.post(
    "/{id_article}/review",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("SC"))],
)
async def review_article(
    id_article: uuid.UUID,
    body: dict,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """SC reviews the abstract or full paper. Body shape depends on which
    phase the article is currently in — validated against the matching
    schema once we know the phase."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_sc) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not the assigned reviewer")

    if article.status in article_state.ABSTRACT_REVIEWABLE:
        try:
            payload = AbstractReviewRequest(**body)
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                exc.errors(include_context=False, include_url=False),
            )
        article.status = article_state.decide_abstract_review(payload.accept)
        if payload.notes is not None:
            article.sc_notes = payload.notes
    elif article.status in article_state.FULL_PAPER_REVIEWABLE:
        try:
            payload = FullPaperReviewRequest(**body)
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                exc.errors(include_context=False, include_url=False),
            )
        article.status = article_state.decide_full_paper_review(payload.decision)
        if payload.notes is not None:
            article.sc_notes = payload.notes
        if payload.id_recommended_journal is not None:
            article.id_recommended_journal = payload.id_recommended_journal
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot review in status {article.status}")

    await session.flush()
    return repo.to_article_out(article, "SC")


@router.post(
    "/{id_article}/announce",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def announce_article(
    id_article: uuid.UUID,
    background_tasks: BackgroundTasks,
    session=Depends(get_session),
) -> ArticleOut:
    """EIC announces the SC's decision (abstract or full paper) to the author."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    try:
        new_status = article_state.announce_result(article.status)
    except ValueError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot announce in status {article.status}")

    if new_status == "accepted" and article.id_recommended_journal is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "id_recommended_journal not set")

    article.status = new_status
    await session.flush()

    author_email = await repo.get_user_email(session, article.id_user)
    background_tasks.add_task(
        emailer.send,
        author_email,
        f"Update on your article '{article.title}'",
        f"Your article status is now: {article.status}.",
    )
    return repo.to_article_out(article, "EIC")


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

    sc_email = await repo.get_user_email(session, article.id_sc)
    background_tasks.add_task(
        emailer.send,
        sc_email,
        "Full paper submitted for review",
        f"Article '{article.title}' full paper is ready for your review.",
    )
    return repo.to_article_out(article, "author")


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

    sc_email = await repo.get_user_email(session, article.id_sc)
    background_tasks.add_task(
        emailer.send,
        sc_email,
        "Revised full paper submitted for review",
        f"Article '{article.title}' revised full paper is ready for your review.",
    )
    return repo.to_article_out(article, "author")


@router.post("/{id_article}/upload", response_model=UploadResponse)
async def upload_article_file(
    id_article: uuid.UUID,
    file: UploadFile = File(...),
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> UploadResponse:
    """Uploads a PDF and returns its storage path. Does not mutate the
    article — the client passes the returned file_path into create/full-paper/
    revision requests separately, same as the existing string-path fields."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if file.content_type != "application/pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only PDF files are accepted")

    content = await file.read()
    try:
        path = await storage.client.upload(file.filename or "upload.pdf", content, file.content_type)
    except storage.StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
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
    _check_view_permission(article, user)
    versions = await repo.list_versions(session, id_article)
    return [ArticleVersionOut.model_validate(v) for v in versions]
