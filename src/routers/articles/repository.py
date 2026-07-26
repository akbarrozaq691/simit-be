import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...models import Article, User
from ...schemas import ArticleOut
from ...status import AUTHOR_STATUS_MAP


def to_article_out(article: Article, viewer_role: str) -> ArticleOut:
    out = ArticleOut.model_validate(article)
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
        stmt = stmt.where(Article.id_sc == uuid.UUID(id_user))
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
