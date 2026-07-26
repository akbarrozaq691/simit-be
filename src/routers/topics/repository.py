import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import MainTopic, SubTopicHumanity, SubTopicInterdisciplinary, SubTopicStem
from ...schemas import SubTopicOut

# kind -> (model, id column name, name column name)
SUBTOPIC_MODELS = {
    "stem": (SubTopicStem, "id_stem", "stem_topic"),
    "humanity": (SubTopicHumanity, "id_humanity", "humanity_topic"),
    "interdisciplinary": (SubTopicInterdisciplinary, "id_interdisciplinary", "interdisciplinary_topic"),
}


def to_sub_topic_out(instance, id_col: str, name_col: str) -> SubTopicOut:
    return SubTopicOut(
        id_sub_topic=getattr(instance, id_col),
        name=getattr(instance, name_col),
        id_topic=instance.id_topic,
    )


async def list_topics(session: AsyncSession) -> list[MainTopic]:
    result = await session.execute(select(MainTopic).order_by(MainTopic.topic_name))
    return list(result.scalars().all())


async def list_sub_topics(session: AsyncSession, kind: str, id_topic: uuid.UUID) -> list:
    model, _, name_col = SUBTOPIC_MODELS[kind]
    result = await session.execute(
        select(model).where(model.id_topic == id_topic).order_by(getattr(model, name_col))
    )
    return list(result.scalars().all())


async def create_topic(session: AsyncSession, topic_name: str) -> MainTopic:
    topic = MainTopic(topic_name=topic_name)
    session.add(topic)
    await session.flush()
    return topic


async def update_topic(session: AsyncSession, id_topic: uuid.UUID, topic_name: str) -> MainTopic | None:
    topic = await session.get(MainTopic, id_topic)
    if topic is None:
        return None
    topic.topic_name = topic_name
    await session.flush()
    return topic


async def delete_topic(session: AsyncSession, id_topic: uuid.UUID) -> bool:
    topic = await session.get(MainTopic, id_topic)
    if topic is None:
        return False
    await session.delete(topic)
    await session.flush()
    return True


async def create_sub_topic(session: AsyncSession, kind: str, id_topic: uuid.UUID, name: str):
    model, _, name_col = SUBTOPIC_MODELS[kind]
    instance = model(id_topic=id_topic, **{name_col: name})
    session.add(instance)
    await session.flush()
    return instance


async def delete_sub_topic(session: AsyncSession, kind: str, sub_id: uuid.UUID) -> bool:
    model, id_col, _ = SUBTOPIC_MODELS[kind]
    instance = await session.get(model, sub_id)
    if instance is None:
        return False
    await session.delete(instance)
    await session.flush()
    return True
