import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from ...deps import get_session, require_roles
from ...schemas import (
    JournalCreate,
    JournalOut,
    OccupationCreate,
    OccupationOut,
    RoleCreate,
    RoleOut,
)
from . import repository as repo

role_router = APIRouter(prefix="/roles", tags=["roles"])
occupation_router = APIRouter(prefix="/occupations", tags=["occupations"])
journal_router = APIRouter(prefix="/journals", tags=["journals"])


# ---- Role ----


@role_router.get("", response_model=list[RoleOut])
async def list_roles(session=Depends(get_session)) -> list[RoleOut]:
    return await repo.list_roles(session)


@role_router.post(
    "",
    response_model=RoleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin"))],
)
async def create_role(body: RoleCreate, session=Depends(get_session)) -> RoleOut:
    return await repo.create_role(session, body.name_role)


# ---- Occupation ----


@occupation_router.get("", response_model=list[OccupationOut])
async def list_occupations(session=Depends(get_session)) -> list[OccupationOut]:
    return await repo.list_occupations(session)


@occupation_router.post(
    "",
    response_model=OccupationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin"))],
)
async def create_occupation(body: OccupationCreate, session=Depends(get_session)) -> OccupationOut:
    return await repo.create_occupation(session, body.occupation_name)


@occupation_router.put(
    "/{id_occupation}",
    response_model=OccupationOut,
    dependencies=[Depends(require_roles("admin"))],
)
@occupation_router.patch(
    "/{id_occupation}",
    response_model=OccupationOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def update_occupation(
    id_occupation: uuid.UUID, body: OccupationCreate, session=Depends(get_session)
) -> OccupationOut:
    occupation = await repo.update_occupation(session, id_occupation, body.occupation_name)
    if occupation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "occupation not found")
    return occupation


@occupation_router.delete(
    "/{id_occupation}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_occupation(id_occupation: uuid.UUID, session=Depends(get_session)) -> None:
    deleted = await repo.delete_occupation(session, id_occupation)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "occupation not found")


# ---- Journal ----


@journal_router.get("", response_model=list[JournalOut])
async def list_journals(session=Depends(get_session)) -> list[JournalOut]:
    return await repo.list_journals(session)


@journal_router.post(
    "",
    response_model=JournalOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def create_journal(body: JournalCreate, session=Depends(get_session)) -> JournalOut:
    return await repo.create_journal(session, body.journal_name)


@journal_router.put(
    "/{id_journal}",
    response_model=JournalOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
@journal_router.patch(
    "/{id_journal}",
    response_model=JournalOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def update_journal(
    id_journal: uuid.UUID, body: JournalCreate, session=Depends(get_session)
) -> JournalOut:
    journal = await repo.update_journal(session, id_journal, body.journal_name)
    if journal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "journal not found")
    return journal


@journal_router.delete(
    "/{id_journal}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def delete_journal(id_journal: uuid.UUID, session=Depends(get_session)) -> None:
    deleted = await repo.delete_journal(session, id_journal)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "journal not found")
