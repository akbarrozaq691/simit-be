import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    FaqItem,
    GalleryImage,
    Journal,
    MainTopic,
    ScheduleItem,
    SiteContent,
    SubTopicHumanity,
    SubTopicInterdisciplinary,
    SubTopicStem,
)


# ---- site_content (key/value) ----


async def all_content(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(select(SiteContent))
    return {row.content_key: row.content_value for row in result.scalars().all()}


async def set_content(session: AsyncSession, values: dict[str, str]) -> dict[str, str]:
    """Upserts each key. Unknown keys are created rather than rejected.

    The key set lives in the seed, not in a constraint: organisers adding a line
    of copy should not need a migration. The admin UI only offers known keys, so
    a stray key can only arrive from a deliberate API call.
    """
    for key, value in values.items():
        row = await session.get(SiteContent, key)
        if row is None:
            session.add(SiteContent(content_key=key, content_value=value))
        else:
            row.content_value = value
    await session.flush()
    return await all_content(session)


# ---- schedule ----


async def list_schedule(session: AsyncSession) -> list[ScheduleItem]:
    result = await session.execute(
        select(ScheduleItem).order_by(ScheduleItem.sort_order, ScheduleItem.title)
    )
    return list(result.scalars().all())


async def create_schedule(session: AsyncSession, **fields) -> ScheduleItem:
    item = ScheduleItem(**fields)
    session.add(item)
    await session.flush()
    return item


async def update_schedule(
    session: AsyncSession, id_schedule: uuid.UUID, updates: dict
) -> ScheduleItem | None:
    item = await session.get(ScheduleItem, id_schedule)
    if item is None:
        return None
    for key, value in updates.items():
        setattr(item, key, value)
    await session.flush()
    return item


async def delete_schedule(session: AsyncSession, id_schedule: uuid.UUID) -> bool:
    item = await session.get(ScheduleItem, id_schedule)
    if item is None:
        return False
    await session.delete(item)
    await session.flush()
    return True


# ---- faq ----


async def list_faq(session: AsyncSession) -> list[FaqItem]:
    result = await session.execute(select(FaqItem).order_by(FaqItem.sort_order, FaqItem.question))
    return list(result.scalars().all())


async def create_faq(session: AsyncSession, **fields) -> FaqItem:
    item = FaqItem(**fields)
    session.add(item)
    await session.flush()
    return item


async def update_faq(session: AsyncSession, id_faq: uuid.UUID, updates: dict) -> FaqItem | None:
    item = await session.get(FaqItem, id_faq)
    if item is None:
        return None
    for key, value in updates.items():
        setattr(item, key, value)
    await session.flush()
    return item


async def delete_faq(session: AsyncSession, id_faq: uuid.UUID) -> bool:
    item = await session.get(FaqItem, id_faq)
    if item is None:
        return False
    await session.delete(item)
    await session.flush()
    return True


# ---- gallery ----


async def list_gallery(session: AsyncSession) -> list[GalleryImage]:
    result = await session.execute(
        select(GalleryImage).order_by(GalleryImage.sort_order, GalleryImage.file_path)
    )
    return list(result.scalars().all())


async def create_gallery(session: AsyncSession, **fields) -> GalleryImage:
    item = GalleryImage(**fields)
    session.add(item)
    await session.flush()
    return item


async def delete_gallery(session: AsyncSession, id_image: uuid.UUID) -> bool:
    item = await session.get(GalleryImage, id_image)
    if item is None:
        return False
    await session.delete(item)
    await session.flush()
    return True


# ---- read-only helpers the landing page needs ----


async def list_topics_ordered(session: AsyncSession) -> list[MainTopic]:
    result = await session.execute(
        select(MainTopic).order_by(MainTopic.sort_order, MainTopic.topic_name)
    )
    return list(result.scalars().all())


async def sub_topics_by_topic(session: AsyncSession) -> dict[uuid.UUID, list[str]]:
    """Every sub-theme name, grouped by the topic it belongs to.

    One query per table rather than three joins onto main_topic: the tables have
    different column names and no common parent, so there is nothing to union
    without spelling each one out anyway.
    """
    grouped: dict[uuid.UUID, list[str]] = {}
    for model, column in (
        (SubTopicStem, SubTopicStem.stem_topic),
        (SubTopicHumanity, SubTopicHumanity.humanity_topic),
        (SubTopicInterdisciplinary, SubTopicInterdisciplinary.interdisciplinary_topic),
    ):
        # Published order first: the list is numbered, so alphabetical output
        # puts the wrong item at number 1. Name breaks ties for rows added
        # before sort_order existed.
        result = await session.execute(
            select(model.id_topic, column).order_by(model.sort_order, column)
        )
        for id_topic, name in result.all():
            grouped.setdefault(id_topic, []).append(name)
    return grouped


async def list_journals(session: AsyncSession) -> list[Journal]:
    result = await session.execute(select(Journal).order_by(Journal.journal_name))
    return list(result.scalars().all())
