import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from ...deps import get_session, require_roles
from ...schemas import TimelineCreate, TimelineOut, TimelineUpdate
from . import repository as repo

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineOut])
async def list_timeline(session=Depends(get_session)) -> list[TimelineOut]:
    return await repo.list_timeline(session)


@router.post(
    "",
    response_model=TimelineOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def create_timeline(body: TimelineCreate, session=Depends(get_session)) -> TimelineOut:
    return await repo.create_timeline(
        session, body.title, body.description, body.start_date, body.end_date
    )


@router.put(
    "/{id_timeline}",
    response_model=TimelineOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
@router.patch(
    "/{id_timeline}",
    response_model=TimelineOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def update_timeline(
    id_timeline: uuid.UUID, body: TimelineUpdate, session=Depends(get_session)
) -> TimelineOut:
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")

    try:
        item = await repo.update_timeline(session, id_timeline, updates)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_date must be >= start_date")
    return item


@router.delete(
    "/{id_timeline}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def delete_timeline(id_timeline: uuid.UUID, session=Depends(get_session)) -> None:
    deleted = await repo.delete_timeline(session, id_timeline)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
