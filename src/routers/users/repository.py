import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...models import User
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
    )


def _select_with_relations():
    return select(User).options(selectinload(User.role), selectinload(User.occupation))


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(_select_with_relations().order_by(User.created_at.desc()))
    return list(result.scalars().all())


async def get_user(session: AsyncSession, id_user: uuid.UUID) -> User | None:
    result = await session.execute(_select_with_relations().where(User.id_user == id_user))
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
) -> User:
    user = User(
        user_name=user_name,
        institution_name=institution_name,
        id_occupation=id_occupation,
        id_role=id_role,
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


async def delete_user(session: AsyncSession, id_user: uuid.UUID) -> bool:
    user = await session.get(User, id_user)
    if user is None:
        return False
    await session.delete(user)
    await session.flush()
    return True
