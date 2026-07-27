from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...deps import get_session
from ...models import STUDENT_OCCUPATION_IDS, User
from ...schemas import AuthUserOut, LoginRequest, RegisterAs, RegisterRequest, TokenOut
from ...security import create_token, hash_password, verify_password
from ..reference import repository as reference_repo
from ..users import repository as users_repo

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthUserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session=Depends(get_session)) -> AuthUserOut:
    """Public self-register — always creates an 'author' account."""
    if await users_repo.email_exists(session, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    author_role = await reference_repo.get_role_by_name(session, "author")

    if body.register_as == RegisterAs.student:
        # Students may only pick one of the three curated levels. Restricting to
        # the fixed ids means this path cannot be used to introduce arbitrary
        # occupation rows — the free-text branch below is the only one that can,
        # and it is scoped to what the participant typed about themselves.
        if body.id_occupation not in STUDENT_OCCUPATION_IDS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "id_occupation must be one of the student levels offered at registration",
            )
        id_occupation = body.id_occupation
    else:
        # General presenters describe their own occupation, so it is created on
        # demand rather than chosen from the admin-curated list.
        id_occupation = await reference_repo.get_or_create_occupation(
            session, body.occupation_name
        )

    user = await users_repo.create_user(
        session,
        user_name=body.user_name,
        institution_name=body.institution_name,
        id_occupation=id_occupation,
        id_role=author_role.id_role,
        register_as=body.register_as.value,
        email=body.email,
        phone_number=body.phone_number,
        password_hash=hash_password(body.password),
    )
    return AuthUserOut(id_user=user.id_user, user_name=user.user_name, email=user.email, role="author")


@router.post("/login", response_model=TokenOut)
async def login(body: LoginRequest, session=Depends(get_session)) -> TokenOut:
    result = await session.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.email == body.email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")

    token = create_token(str(user.id_user), user.role.name_role)
    return TokenOut(
        access_token=token,
        id_user=user.id_user,
        user_name=user.user_name,
        role=user.role.name_role,
    )
