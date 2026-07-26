import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from ...deps import get_current_user, get_session, require_roles
from ...schemas import UserCreate, UserCtx, UserOut, UserUpdate
from ...security import hash_password
from ..reference import repository as reference_repo
from . import repository as repo

router = APIRouter(prefix="/users", tags=["users"])


def _ensure_self_or_admin(user: UserCtx, id_user: uuid.UUID) -> None:
    if user.role != "admin" and str(id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


@router.get("", response_model=list[UserOut], dependencies=[Depends(require_roles("admin"))])
async def list_users(session=Depends(get_session)) -> list[UserOut]:
    users = await repo.list_users(session)
    return [repo.to_user_out(u) for u in users]


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin"))],
)
async def create_user(body: UserCreate, session=Depends(get_session)) -> UserOut:
    role = await reference_repo.get_role_by_name(session, body.name_role.value)
    if role is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown name_role")
    if await repo.email_exists(session, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    id_occupation = await reference_repo.get_or_create_occupation(session, body.occupation_name)
    user = await repo.create_user(
        session,
        user_name=body.user_name,
        institution_name=body.institution_name,
        id_occupation=id_occupation,
        id_role=role.id_role,
        email=body.email,
        phone_number=body.phone_number,
        password_hash=hash_password(body.password),
    )
    return repo.to_user_out(user)


@router.get("/{id_user}", response_model=UserOut)
async def get_user(
    id_user: uuid.UUID, user: UserCtx = Depends(get_current_user), session=Depends(get_session)
) -> UserOut:
    _ensure_self_or_admin(user, id_user)
    target = await repo.get_user(session, id_user)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return repo.to_user_out(target)


@router.put("/{id_user}", response_model=UserOut)
@router.patch("/{id_user}", response_model=UserOut)
async def update_user(
    id_user: uuid.UUID,
    body: UserUpdate,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> UserOut:
    _ensure_self_or_admin(user, id_user)

    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    if "password" in updates:
        updates["password_hash"] = hash_password(updates.pop("password"))
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")

    target = await repo.update_user(session, id_user, updates)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return repo.to_user_out(target)


@router.delete(
    "/{id_user}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_user(id_user: uuid.UUID, session=Depends(get_session)) -> None:
    deleted = await repo.delete_user(session, id_user)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
