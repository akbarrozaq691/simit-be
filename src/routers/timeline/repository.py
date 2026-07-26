import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Timeline


async def list_timeline(session: AsyncSession) -> list[Timeline]:
    result = await session.execute(select(Timeline).order_by(Timeline.start_date))
    return list(result.scalars().all())


async def create_timeline(
    session: AsyncSession, title: str, description: str | None, start_date, end_date
) -> Timeline:
    item = Timeline(title=title, description=description, start_date=start_date, end_date=end_date)
    session.add(item)
    await session.flush()
    return item


async def update_timeline(session: AsyncSession, id_timeline: uuid.UUID, updates: dict) -> Timeline | None:
    item = await session.get(Timeline, id_timeline)
    if item is None:
        return None
    for key, value in updates.items():
        setattr(item, key, value)
    await session.flush()
    return item


async def delete_timeline(session: AsyncSession, id_timeline: uuid.UUID) -> bool:
    item = await session.get(Timeline, id_timeline)
    if item is None:
        return False
    await session.delete(item)
    await session.flush()
    return True
