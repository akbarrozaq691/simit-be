import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from ... import emailer
from ...deps import get_current_user, get_session, require_roles
from ...schemas import (
    ArticleAssignRequest,
    ArticleCreate,
    ArticleFullPaperRequest,
    ArticleOut,
    ArticleReviewRequest,
    ArticleUpdate,
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
    body: ArticleReviewRequest,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """SC reviews the abstract (or full paper) and sends the decision back to EIC."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_sc) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not the assigned reviewer")
    if article.status not in ("assigned_to_sc", "under_review", "full_paper_submitted"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot review in status {article.status}")

    article.status = "passed_review" if body.lolos else "revision_needed"
    if body.notes is not None:
        article.sc_notes = body.notes
    if body.id_recommended_journal is not None:
        article.id_recommended_journal = body.id_recommended_journal
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
    """EIC announces the SC decision back to the author."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if article.status not in ("passed_review", "revision_needed"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot announce in status {article.status}")

    if article.status == "revision_needed":
        article.status = "rejected"
    elif article.full_paper_file_path:
        article.status = "completed"
    else:
        article.status = "announced"
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
    """Author submits the full paper after being announced as passed."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if article.status != "announced":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot submit full paper in status {article.status}"
        )

    article.full_paper_file_path = body.full_paper_file_path
    article.status = "full_paper_submitted"
    await session.flush()

    sc_email = await repo.get_user_email(session, article.id_sc)
    background_tasks.add_task(
        emailer.send,
        sc_email,
        "Full paper submitted for review",
        f"Article '{article.title}' full paper is ready for your review.",
    )
    return repo.to_article_out(article, "author")
