import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...models import Role, User
from ...schemas import UserOut


def to_user_out(user: User) -> UserOut:
    return UserOut(
        id_user=user.id_user,
        user_name=user.user_name,
        institution_name=user.institution_name,
        email=user.email,
        phone_number=user.phone_number,
        created_at=user.created_at,
        role=user.role.name_role,
        occupation_name=user.occupation.occupation_name if user.occupation else None,
        register_as=user.register_as,
    )


def _select_with_relations():
    return select(User).options(selectinload(User.role), selectinload(User.occupation))


async def list_users(session: AsyncSession, include_deleted: bool = False) -> list[User]:
    stmt = _select_with_relations().order_by(User.created_at.desc())
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_reviewers(session: AsyncSession) -> list[User]:
    """Active SC users, for the reviewer-assignment screen."""
    stmt = (
        _select_with_relations()
        .join(User.role)
        .where(Role.name_role == "SC", User.deleted_at.is_(None))
        .order_by(User.user_name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user(
    session: AsyncSession, id_user: uuid.UUID, include_deleted: bool = False
) -> User | None:
    stmt = _select_with_relations().where(User.id_user == id_user)
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def email_exists(session: AsyncSession, email: str) -> bool:
    result = await session.execute(select(User.id_user).where(User.email == email))
    return result.scalar_one_or_none() is not None


async def create_user(
    session: AsyncSession,
    *,
    user_name: str,
    institution_name: str | None,
    id_occupation: uuid.UUID | None,
    id_role: uuid.UUID,
    email: str,
    phone_number: str | None,
    password_hash: str,
    register_as: str | None = None,
) -> User:
    user = User(
        user_name=user_name,
        institution_name=institution_name,
        id_occupation=id_occupation,
        id_role=id_role,
        register_as=register_as,
        email=email,
        phone_number=phone_number,
        password_hash=password_hash,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user, attribute_names=["role", "occupation"])
    return user


async def update_user(session: AsyncSession, id_user: uuid.UUID, updates: dict) -> User | None:
    user = await get_user(session, id_user)
    if user is None:
        return None
    for key, value in updates.items():
        setattr(user, key, value)
    await session.flush()
    return user


async def soft_delete_user(session: AsyncSession, id_user: uuid.UUID) -> bool:
    """Archives instead of deleting. This is also what fixes the previous 500:
    hard-deleting a user referenced by an article (as author or reviewer) raised
    a ForeignKeyViolationError."""
    user = await get_user(session, id_user)
    if user is None:
        return False
    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()
    return True


async def restore_user(session: AsyncSession, id_user: uuid.UUID) -> User | None:
    user = await get_user(session, id_user, include_deleted=True)
    if user is None or user.deleted_at is None:
        return None
    user.deleted_at = None
    await session.flush()
    return user


async def is_live(session: AsyncSession, id_user: uuid.UUID) -> bool:
    """Used by get_current_user to revoke archived users' tokens immediately."""
    result = await session.execute(
        select(User.id_user).where(User.id_user == id_user, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none() is not None
