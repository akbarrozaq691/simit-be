import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .database import get_session  # re-exported for routers: Depends(get_session)
from .schemas import UserCtx
from .security import decode_token

__all__ = ["get_session", "get_current_user", "require_roles"]

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserCtx:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    return UserCtx(id_user=payload["id_user"], role=payload["role"])


def require_roles(*roles: str):
    async def checker(user: UserCtx = Depends(get_current_user)) -> UserCtx:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden for this role")
        return user

    return checker
