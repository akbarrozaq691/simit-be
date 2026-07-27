import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from ... import audit
from ...deps import get_current_user, get_session, require_roles
from ...schemas import ReviewerOption, UserCreate, UserCtx, UserOut, UserUpdate
from ...security import hash_password
from ..reference import repository as reference_repo
from . import repository as repo

router = APIRouter(prefix="/users", tags=["users"])


def _ensure_self_or_admin(user: UserCtx, id_user: uuid.UUID) -> None:
    if user.role != "admin" and str(id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


@router.get("", response_model=list[UserOut], dependencies=[Depends(require_roles("admin"))])
async def list_users(include_deleted: bool = False, session=Depends(get_session)) -> list[UserOut]:
    users = await repo.list_users(session, include_deleted=include_deleted)
    return [repo.to_user_out(u) for u in users]


# Declared before /{id_user} so the literal path wins the match.
@router.get(
    "/reviewers",
    response_model=list[ReviewerOption],
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def list_reviewers(session=Depends(get_session)) -> list[ReviewerOption]:
    """The reviewers an editor can assign.

    Separate from GET /users, which is admin-only: assigning a reviewer is an
    EIC job, but the full user list carries every author's contact details. This
    returns names and ids, which is all the assignment screen uses.
    """
    return [
        ReviewerOption(id_user=u.id_user, user_name=u.user_name)
        for u in await repo.list_reviewers(session)
    ]


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin"))],
)
async def create_user(
    body: UserCreate,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> UserOut:
    role = await reference_repo.get_role_by_name(session, body.name_role.value)
    if role is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown name_role")
    if await repo.email_exists(session, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    id_occupation = await reference_repo.get_or_create_occupation(session, body.occupation_name)
    created = await repo.create_user(
        session,
        user_name=body.user_name,
        institution_name=body.institution_name,
        id_occupation=id_occupation,
        id_role=role.id_role,
        email=body.email,
        phone_number=body.phone_number,
        password_hash=hash_password(body.password),
    )
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="user.created",
        entity_type="user",
        entity_id=created.id_user,
        detail={"role": body.name_role.value},
    )
    return repo.to_user_out(created)


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
async def delete_user(
    id_user: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> None:
    """Archives the user. Previously a hard delete, which raised a raw
    ForeignKeyViolationError (500) for any user referenced by an article as
    author or reviewer."""
    if not await repo.soft_delete_user(session, id_user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="user.deleted",
        entity_type="user",
        entity_id=id_user,
        detail={},
    )


@router.post(
    "/{id_user}/restore",
    response_model=UserOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def restore_user(
    id_user: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> UserOut:
    restored = await repo.restore_user(session, id_user)
    if restored is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "user not found or not archived"
        )
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="user.restored",
        entity_type="user",
        entity_id=id_user,
        detail={},
    )
    return repo.to_user_out(restored)
