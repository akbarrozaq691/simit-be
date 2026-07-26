import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status

from ...deps import get_session, require_roles
from ...schemas import SubTopicCreate, SubTopicOut, TopicCreate, TopicOut, TopicWithSubtopics
from . import repository as repo

router = APIRouter(tags=["topics"])

SubTopicKind = Literal["stem", "humanity", "interdisciplinary"]


@router.get("/topics", response_model=list[TopicWithSubtopics])
async def list_topics(session=Depends(get_session)) -> list[TopicWithSubtopics]:
    topics = await repo.list_topics(session)
    result = []
    for topic in topics:
        item = TopicWithSubtopics(id_topic=topic.id_topic, topic_name=topic.topic_name)
        for kind, (_, id_col, name_col) in repo.SUBTOPIC_MODELS.items():
            rows = await repo.list_sub_topics(session, kind, topic.id_topic)
            setattr(item, kind, [repo.to_sub_topic_out(r, id_col, name_col) for r in rows])
        result.append(item)
    return result


@router.post(
    "/topics",
    response_model=TopicOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin"))],
)
async def create_topic(body: TopicCreate, session=Depends(get_session)) -> TopicOut:
    return await repo.create_topic(session, body.topic_name)


@router.put(
    "/topics/{id_topic}", response_model=TopicOut, dependencies=[Depends(require_roles("admin"))]
)
async def update_topic(
    id_topic: uuid.UUID, body: TopicCreate, session=Depends(get_session)
) -> TopicOut:
    topic = await repo.update_topic(session, id_topic, body.topic_name)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "topic not found")
    return topic


@router.delete(
    "/topics/{id_topic}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_topic(id_topic: uuid.UUID, session=Depends(get_session)) -> None:
    deleted = await repo.delete_topic(session, id_topic)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "topic not found")


@router.post(
    "/sub-topics/{kind}",
    response_model=SubTopicOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin"))],
)
async def create_sub_topic(
    kind: SubTopicKind, body: SubTopicCreate, session=Depends(get_session)
) -> SubTopicOut:
    _, id_col, name_col = repo.SUBTOPIC_MODELS[kind]
    instance = await repo.create_sub_topic(session, kind, body.id_topic, body.name)
    return repo.to_sub_topic_out(instance, id_col, name_col)


@router.delete(
    "/sub-topics/{kind}/{sub_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_sub_topic(kind: SubTopicKind, sub_id: uuid.UUID, session=Depends(get_session)) -> None:
    deleted = await repo.delete_sub_topic(session, kind, sub_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sub-topic not found")
