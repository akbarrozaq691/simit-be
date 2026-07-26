import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .database import get_session  # re-exported for routers: Depends(get_session)
from .routers.users import repository as users_repository
from .schemas import UserCtx
from .security import decode_token

__all__ = ["get_session", "get_current_user", "require_roles"]

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session=Depends(get_session),
) -> UserCtx:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")

    id_user = payload["id_user"]
    # A valid signature is not enough: an archived user's token must stop
    # working immediately, and there is no other revocation mechanism.
    if not await users_repository.is_live(session, uuid.UUID(id_user)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account is no longer active")

    return UserCtx(id_user=id_user, role=payload["role"])


def require_roles(*roles: str):
    async def checker(user: UserCtx = Depends(get_current_user)) -> UserCtx:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden for this role")
        return user

    return checker
