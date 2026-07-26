from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text

from . import database
from .deps import get_session
from .routers.articles.router import router as articles_router
from .routers.audit.router import router as audit_router
from .routers.auth.router import router as auth_router
from .routers.reference.router import journal_router, occupation_router, role_router
from .routers.timeline.router import router as timeline_router
from .routers.topics.router import router as topics_router
from .routers.users.router import router as users_router
from .settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await database.dispose_engine()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

p = settings.api_prefix
app.include_router(auth_router, prefix=p)
app.include_router(users_router, prefix=p)
app.include_router(role_router, prefix=p)
app.include_router(occupation_router, prefix=p)
app.include_router(journal_router, prefix=p)
app.include_router(timeline_router, prefix=p)
app.include_router(topics_router, prefix=p)
app.include_router(articles_router, prefix=p)
app.include_router(audit_router, prefix=p)


@app.get("/health", tags=["health"])
async def health(session=Depends(get_session)) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
