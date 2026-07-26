import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Journal, Occupation, Role

# ---- Role ----


async def list_roles(session: AsyncSession) -> list[Role]:
    result = await session.execute(select(Role).order_by(Role.name_role))
    return list(result.scalars().all())


async def get_role_by_name(session: AsyncSession, name_role: str) -> Role | None:
    result = await session.execute(select(Role).where(Role.name_role == name_role))
    return result.scalar_one_or_none()


async def create_role(session: AsyncSession, name_role: str) -> Role:
    role = Role(name_role=name_role)
    session.add(role)
    await session.flush()
    return role


# ---- Occupation ----


async def list_occupations(session: AsyncSession) -> list[Occupation]:
    result = await session.execute(select(Occupation).order_by(Occupation.occupation_name))
    return list(result.scalars().all())


async def get_occupation_by_name(
    session: AsyncSession, occupation_name: str
) -> Occupation | None:
    result = await session.execute(
        select(Occupation).where(Occupation.occupation_name == occupation_name)
    )
    return result.scalar_one_or_none()


async def get_or_create_occupation(
    session: AsyncSession, occupation_name: str | None
) -> uuid.UUID | None:
    if not occupation_name:
        return None
    result = await session.execute(
        select(Occupation).where(Occupation.occupation_name == occupation_name)
    )
    occupation = result.scalar_one_or_none()
    if occupation is None:
        occupation = Occupation(occupation_name=occupation_name)
        session.add(occupation)
        await session.flush()
    return occupation.id_occupation


async def create_occupation(session: AsyncSession, occupation_name: str) -> Occupation:
    occupation = Occupation(occupation_name=occupation_name)
    session.add(occupation)
    await session.flush()
    return occupation


async def update_occupation(
    session: AsyncSession, id_occupation: uuid.UUID, occupation_name: str
) -> Occupation | None:
    occupation = await session.get(Occupation, id_occupation)
    if occupation is None:
        return None
    occupation.occupation_name = occupation_name
    await session.flush()
    return occupation


async def delete_occupation(session: AsyncSession, id_occupation: uuid.UUID) -> bool:
    occupation = await session.get(Occupation, id_occupation)
    if occupation is None:
        return False
    await session.delete(occupation)
    await session.flush()
    return True


# ---- Journal ----


async def list_journals(session: AsyncSession) -> list[Journal]:
    result = await session.execute(select(Journal).order_by(Journal.journal_name))
    return list(result.scalars().all())


async def create_journal(session: AsyncSession, journal_name: str) -> Journal:
    journal = Journal(journal_name=journal_name)
    session.add(journal)
    await session.flush()
    return journal


async def update_journal(
    session: AsyncSession, id_journal: uuid.UUID, journal_name: str
) -> Journal | None:
    journal = await session.get(Journal, id_journal)
    if journal is None:
        return None
    journal.journal_name = journal_name
    await session.flush()
    return journal


async def delete_journal(session: AsyncSession, id_journal: uuid.UUID) -> bool:
    journal = await session.get(Journal, id_journal)
    if journal is None:
        return False
    await session.delete(journal)
    await session.flush()
    return True
