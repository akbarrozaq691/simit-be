import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from ...deps import get_session, require_roles
from ...schemas import (
    FaqItemCreate,
    FaqItemOut,
    GalleryImageCreate,
    GalleryImageOut,
    JournalOut,
    LandingContentOut,
    LandingTopicOut,
    ScheduleItemCreate,
    ScheduleItemOut,
    SiteContentUpdate,
    TopicOut,
)
from . import repository as repo

# Public read of the whole landing page.
public_router = APIRouter(prefix="/landing", tags=["landing"])

# Admin-only editing. Split from the public router so the auth requirement is
# structural rather than repeated per endpoint.
admin_router = APIRouter(
    prefix="/admin/content",
    tags=["content-admin"],
    dependencies=[Depends(require_roles("admin"))],
)


@public_router.get("", response_model=LandingContentOut)
async def get_landing(session=Depends(get_session)) -> LandingContentOut:
    """Everything the landing page renders, in one request.

    Deliberately unauthenticated: this is the public front page. Seven separate
    calls would mean seven round trips before anything appears, and one failing
    section would leave the page half-built.
    """
    sub_topics = await repo.sub_topics_by_topic(session)
    return LandingContentOut(
        content=await repo.all_content(session),
        schedule=[ScheduleItemOut.model_validate(i) for i in await repo.list_schedule(session)],
        faq=[FaqItemOut.model_validate(i) for i in await repo.list_faq(session)],
        gallery=[GalleryImageOut.model_validate(i) for i in await repo.list_gallery(session)],
        topics=[
            LandingTopicOut(
                **TopicOut.model_validate(t).model_dump(),
                sub_topics=sub_topics.get(t.id_topic, []),
            )
            for t in await repo.list_topics_ordered(session)
        ],
        journals=[JournalOut.model_validate(j) for j in await repo.list_journals(session)],
    )


# ---- text content ----


@admin_router.put("", response_model=dict[str, str])
async def update_content(body: SiteContentUpdate, session=Depends(get_session)) -> dict[str, str]:
    """Saves a batch of key/value edits in one transaction.

    One request per section rather than per field: a section form should either
    save or not, never half-apply.
    """
    return await repo.set_content(session, body.values)


# ---- schedule ----


@admin_router.post("/schedule", response_model=ScheduleItemOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleItemCreate, session=Depends(get_session)) -> ScheduleItemOut:
    item = await repo.create_schedule(session, **body.model_dump())
    return ScheduleItemOut.model_validate(item)


@admin_router.patch("/schedule/{id_schedule}", response_model=ScheduleItemOut)
async def update_schedule(
    id_schedule: uuid.UUID, body: ScheduleItemCreate, session=Depends(get_session)
) -> ScheduleItemOut:
    item = await repo.update_schedule(session, id_schedule, body.model_dump())
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule item not found")
    return ScheduleItemOut.model_validate(item)


@admin_router.delete("/schedule/{id_schedule}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(id_schedule: uuid.UUID, session=Depends(get_session)) -> None:
    if not await repo.delete_schedule(session, id_schedule):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule item not found")


# ---- faq ----


@admin_router.post("/faq", response_model=FaqItemOut, status_code=status.HTTP_201_CREATED)
async def create_faq(body: FaqItemCreate, session=Depends(get_session)) -> FaqItemOut:
    item = await repo.create_faq(session, **body.model_dump())
    return FaqItemOut.model_validate(item)


@admin_router.patch("/faq/{id_faq}", response_model=FaqItemOut)
async def update_faq(
    id_faq: uuid.UUID, body: FaqItemCreate, session=Depends(get_session)
) -> FaqItemOut:
    item = await repo.update_faq(session, id_faq, body.model_dump())
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ item not found")
    return FaqItemOut.model_validate(item)


@admin_router.delete("/faq/{id_faq}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(id_faq: uuid.UUID, session=Depends(get_session)) -> None:
    if not await repo.delete_faq(session, id_faq):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ item not found")


# ---- gallery ----


@admin_router.post("/gallery", response_model=GalleryImageOut, status_code=status.HTTP_201_CREATED)
async def create_gallery(body: GalleryImageCreate, session=Depends(get_session)) -> GalleryImageOut:
    """Registers an already-uploaded image.

    The file itself goes through POST /uploads first; this stores the returned
    path. Same two-step shape as article submission, and it keeps this endpoint
    free of multipart handling.
    """
    item = await repo.create_gallery(session, **body.model_dump())
    return GalleryImageOut.model_validate(item)


@admin_router.delete("/gallery/{id_image}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gallery(id_image: uuid.UUID, session=Depends(get_session)) -> None:
    if not await repo.delete_gallery(session, id_image):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image not found")
