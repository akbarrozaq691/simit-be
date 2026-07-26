import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...models import Article, ArticleReview, ArticleReviewer, ArticleVersion, User
from ...schemas import ArticleOut
from ...status import AUTHOR_STATUS_MAP


def to_article_out(article: Article, viewer_role: str, reviewers: list[uuid.UUID]) -> ArticleOut:
    out = ArticleOut.model_validate(article)
    out.reviewers = reviewers
    if viewer_role == "author":
        out.status = AUTHOR_STATUS_MAP.get(out.status, out.status)
    return out


async def get_article(session: AsyncSession, id_article: uuid.UUID) -> Article | None:
    return await session.get(Article, id_article)


async def list_articles_for(session: AsyncSession, role: str, id_user: str) -> list[Article]:
    stmt = select(Article)
    if role == "author":
        stmt = stmt.where(Article.id_user == uuid.UUID(id_user))
    elif role == "SC":
        stmt = stmt.where(
            Article.id_article.in_(
                select(ArticleReviewer.id_article).where(
                    ArticleReviewer.id_reviewer == uuid.UUID(id_user)
                )
            )
        )
    # EIC/admin see everything
    stmt = stmt.order_by(Article.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_article(
    session: AsyncSession,
    *,
    title: str,
    authors: str,
    abstract: str,
    keywords: str | None,
    abstract_file_path: str,
    id_topic: uuid.UUID | None,
    id_user: uuid.UUID,
) -> Article:
    article = Article(
        title=title,
        authors=authors,
        abstract=abstract,
        keywords=keywords,
        abstract_file_path=abstract_file_path,
        id_topic=id_topic,
        id_user=id_user,
    )
    session.add(article)
    await session.flush()
    return article


async def get_user_role(session: AsyncSession, id_user: uuid.UUID) -> str | None:
    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.id_user == id_user)
    )
    user = result.scalar_one_or_none()
    return user.role.name_role if user else None


async def get_user_email(session: AsyncSession, id_user: uuid.UUID | None) -> str | None:
    if id_user is None:
        return None
    user = await session.get(User, id_user)
    return user.email if user else None


async def _next_version_number(session: AsyncSession, id_article: uuid.UUID, phase: str) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(ArticleVersion.version_number), 0)).where(
            ArticleVersion.id_article == id_article, ArticleVersion.phase == phase
        )
    )
    return result.scalar_one() + 1


_VERSION_INSERT_ATTEMPTS = 3


async def add_article_version(
    session: AsyncSession,
    *,
    id_article: uuid.UUID,
    phase: str,
    file_path: str,
    submitted_by: uuid.UUID,
) -> ArticleVersion:
    """Appends a version row, allocating the next per-(article, phase) number.

    Two concurrent submissions can read the same max and collide on the
    UNIQUE(id_article, phase, version_number) constraint. Each attempt runs in
    a SAVEPOINT so a violation can be rolled back without poisoning the caller's
    transaction, then the number is recomputed and the insert retried.
    """
    for attempt in range(1, _VERSION_INSERT_ATTEMPTS + 1):
        version_number = await _next_version_number(session, id_article, phase)
        version = ArticleVersion(
            id_article=id_article,
            phase=phase,
            version_number=version_number,
            file_path=file_path,
            submitted_by=submitted_by,
        )
        try:
            async with session.begin_nested():
                session.add(version)
                await session.flush()
        except IntegrityError:
            if attempt == _VERSION_INSERT_ATTEMPTS:
                raise
            continue
        return version

    # Unreachable: the loop either returns or raises on the final attempt.
    raise AssertionError("version insert loop exited without result")


async def list_versions(session: AsyncSession, id_article: uuid.UUID) -> list[ArticleVersion]:
    result = await session.execute(
        select(ArticleVersion)
        .where(ArticleVersion.id_article == id_article)
        .order_by(ArticleVersion.phase, ArticleVersion.version_number)
    )
    return list(result.scalars().all())


# ---- Reviewer assignment ----


async def list_reviewer_ids(session: AsyncSession, id_article: uuid.UUID) -> list[uuid.UUID]:
    result = await session.execute(
        select(ArticleReviewer.id_reviewer)
        .where(ArticleReviewer.id_article == id_article)
        .order_by(ArticleReviewer.assigned_at)
    )
    return list(result.scalars().all())


async def is_assigned(
    session: AsyncSession, id_article: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    result = await session.execute(
        select(ArticleReviewer.id_assignment).where(
            ArticleReviewer.id_article == id_article,
            ArticleReviewer.id_reviewer == id_reviewer,
        )
    )
    return result.scalar_one_or_none() is not None


async def add_reviewer(
    session: AsyncSession, id_article: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    """Assigns a reviewer. Returns False if they were already assigned
    (idempotent by design — re-assigning is not an error)."""
    if await is_assigned(session, id_article, id_reviewer):
        return False
    session.add(ArticleReviewer(id_article=id_article, id_reviewer=id_reviewer))
    await session.flush()
    return True


async def remove_reviewer(
    session: AsyncSession, id_article: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    result = await session.execute(
        select(ArticleReviewer).where(
            ArticleReviewer.id_article == id_article,
            ArticleReviewer.id_reviewer == id_reviewer,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        return False
    await session.delete(assignment)
    await session.flush()
    return True


# ---- Reviews ----


async def latest_version(
    session: AsyncSession, id_article: uuid.UUID, phase: str
) -> ArticleVersion | None:
    result = await session.execute(
        select(ArticleVersion)
        .where(ArticleVersion.id_article == id_article, ArticleVersion.phase == phase)
        .order_by(ArticleVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def has_reviewed(
    session: AsyncSession, id_version: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    result = await session.execute(
        select(ArticleReview.id_review).where(
            ArticleReview.id_version == id_version,
            ArticleReview.id_reviewer == id_reviewer,
        )
    )
    return result.scalar_one_or_none() is not None


async def add_review(
    session: AsyncSession,
    *,
    id_version: uuid.UUID,
    id_reviewer: uuid.UUID,
    decision: str,
    notes: str | None,
) -> ArticleReview:
    review = ArticleReview(
        id_version=id_version,
        id_reviewer=id_reviewer,
        decision=decision,
        notes=notes,
    )
    session.add(review)
    await session.flush()
    return review


async def all_assigned_have_reviewed(
    session: AsyncSession, id_article: uuid.UUID, id_version: uuid.UUID
) -> bool:
    """True when every currently-assigned reviewer has a review on this version.

    False when no reviewers are assigned — an article with no reviewers has not
    completed review, it has not started it.
    """
    assigned = await list_reviewer_ids(session, id_article)
    if not assigned:
        return False
    result = await session.execute(
        select(ArticleReview.id_reviewer).where(ArticleReview.id_version == id_version)
    )
    reviewed = set(result.scalars().all())
    return all(r in reviewed for r in assigned)


async def list_reviews_for_article(
    session: AsyncSession,
    id_article: uuid.UUID,
    only_reviewer: uuid.UUID | None = None,
) -> list[ArticleReview]:
    """All reviews across every version of an article, newest version first.
    `only_reviewer` narrows to one reviewer's own reviews (blind-review view)."""
    stmt = (
        select(ArticleReview)
        .join(ArticleVersion, ArticleReview.id_version == ArticleVersion.id_version)
        .where(ArticleVersion.id_article == id_article)
        .order_by(ArticleVersion.phase, ArticleVersion.version_number.desc())
    )
    if only_reviewer is not None:
        stmt = stmt.where(ArticleReview.id_reviewer == only_reviewer)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_institution(session: AsyncSession, id_user: uuid.UUID) -> str | None:
    user = await session.get(User, id_user)
    return user.institution_name if user else None
