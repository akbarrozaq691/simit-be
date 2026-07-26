# Audit Log and Soft Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only audit log of significant actions, and replace hard deletes of articles and users with archival (`deleted_at`) plus restore — which also fixes the existing 500 on deleting a referenced user.

**Architecture:** One new table (`audit_log`) plus a nullable `deleted_at` column on `articles` and `users`. Audit writes go through a single explicit helper called from handlers, sharing the request's transaction so an audit row can never describe a change that rolled back. Soft-delete filtering lives in the repository layer so handlers stay thin. Archived users lose access immediately via a liveness check in `get_current_user`.

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Pydantic v2, pytest.

## Global Constraints

- **Audit log is admin-only.** It records the EIC's actions too, so the EIC must not be able to read it.
- **Audit events are an explicit, fixed list** (see Task 1) written at handlers. Do NOT add catch-all middleware, and do NOT log request payloads (they contain passwords).
- **No before/after row snapshots** — only the small `detail` shapes specified.
- Soft delete covers **articles and users only**. Related rows (`article_version`, `article_review`, `article_reviewer`) are deliberately left intact.
- `DELETE` responses stay **204** so existing clients are unaffected. Deleting something already archived is **404**.
- **Archived users must lose access immediately**: rejected at login AND rejected on every authenticated request. A still-valid JWT must not keep working.
- Archived emails stay claimed (the `UNIQUE` constraint is not relaxed) — re-registering returns the existing 409.
- Migration 003 is **purely additive**: it drops nothing and backfills nothing.
- Existing suite is 48 tests with pristine output (no warnings). Both must hold after every task.
- No integration-test harness exists. Endpoint behavior is verified by manual smoke test (Task 7). Do NOT write tests that only assert mock behavior.

---

### Task 1: Audit log infrastructure

**Files:**
- Modify: `db/schema.sql`
- Modify: `src/models.py`
- Create: `src/audit.py`
- Create: `tests/test_audit.py`

**Interfaces:**
- Produces (consumed by Tasks 2, 4, 5, 6):
  - `AuditLog` ORM model (`id_audit`, `id_actor`, `action`, `entity_type`, `entity_id`, `detail`, `created_at`)
  - `audit.ACTIONS: frozenset[str]` — the allowed action strings
  - `async def audit.record(session, *, id_actor, action, entity_type, entity_id, detail=None) -> None`

- [ ] **Step 1: Add the table to `db/schema.sql`**

After the `article_review` table and its index, add:

```sql
-- === Audit log (admin-only action history) ===
-- Explicit, significant events only — written at the handler inside the same
-- transaction as the change they describe, so a rolled-back request leaves no
-- phantom audit row. id_actor is nullable and NOT cascading: an audit trail
-- that vanishes with its actor is not an audit trail.

CREATE TABLE audit_log (
    id_audit      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_actor      UUID REFERENCES users(id_user),
    action        VARCHAR(50) NOT NULL,
    entity_type   VARCHAR(30) NOT NULL,
    entity_id     UUID,
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_created_at ON audit_log(created_at DESC);
CREATE INDEX idx_audit_log_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_log_actor ON audit_log(id_actor);
```

- [ ] **Step 2: Add the ORM model**

In `src/models.py`, add `JSONB` to the postgres dialect imports:

```python
from sqlalchemy.dialects.postgresql import JSONB
```

Then after the `ArticleReview` class add:

```python
class AuditLog(Base):
    __tablename__ = "audit_log"

    id_audit: Mapped[uuid.UUID] = _uuid_pk()
    id_actor: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id_user"))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_audit.py`:

```python
"""The audit action vocabulary is a fixed, deliberate list. These tests pin it
so a typo in a handler's action string fails loudly instead of silently
writing an unqueryable row."""

import pytest

from src import audit

EXPECTED_ACTIONS = {
    "article.created",
    "article.status_changed",
    "article.deleted",
    "article.restored",
    "article.version_submitted",
    "reviewer.assigned",
    "reviewer.unassigned",
    "review.submitted",
    "user.created",
    "user.deleted",
    "user.restored",
}


def test_action_vocabulary_is_exactly_the_agreed_set():
    assert audit.ACTIONS == EXPECTED_ACTIONS


@pytest.mark.parametrize("action", sorted(EXPECTED_ACTIONS))
def test_every_action_is_recognised(action):
    assert audit.is_known_action(action) is True


def test_unknown_action_is_rejected():
    assert audit.is_known_action("article.mystery") is False


def test_entity_types_are_constrained():
    assert audit.ENTITY_TYPES == {"article", "user"}
```

- [ ] **Step 4: Run to verify it fails**

Run: `pytest tests/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.audit'`

- [ ] **Step 5: Implement `src/audit.py`**

```python
"""Audit log writer.

Significant events only, recorded explicitly at the handler that performs the
change. The insert shares the caller's transaction, so an audit row can never
describe a change that was rolled back.

Deliberately NOT middleware: the event list below is a curated set, and
request-level interception would both capture noise and risk logging sensitive
payloads.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog

ACTIONS = frozenset(
    {
        "article.created",
        "article.status_changed",
        "article.deleted",
        "article.restored",
        "article.version_submitted",
        "reviewer.assigned",
        "reviewer.unassigned",
        "review.submitted",
        "user.created",
        "user.deleted",
        "user.restored",
    }
)

ENTITY_TYPES = frozenset({"article", "user"})


def is_known_action(action: str) -> bool:
    return action in ACTIONS


async def record(
    session: AsyncSession,
    *,
    id_actor: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    detail: dict | None = None,
) -> None:
    """Appends an audit row. Raises ValueError on an unknown action or entity
    type — a mistyped action string would produce a row nobody can query, so
    failing loudly in development is better than logging garbage."""
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action: {action!r}")
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"unknown audit entity_type: {entity_type!r}")

    session.add(
        AuditLog(
            id_actor=id_actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
    await session.flush()
```

Note `ACTIONS` is a `frozenset` but the test compares it to a plain `set` —
that comparison succeeds, since `frozenset({"a"}) == {"a"}`.

- [ ] **Step 6: Run to verify it passes**

Run: `pytest tests/test_audit.py -v`
Expected: 14 passed (1 vocabulary + 11 parametrized + 1 unknown + 1 entity types).

Run: `pytest -q`
Expected: 62 passed, no warnings.

- [ ] **Step 7: Commit**

```bash
git add db/schema.sql src/models.py src/audit.py tests/test_audit.py
git commit -m "feat: audit log table, model, and writer"
```

---

### Task 2: Audit log read endpoint

**Files:**
- Modify: `src/schemas.py`
- Create: `src/routers/audit/__init__.py` (empty)
- Create: `src/routers/audit/repository.py`
- Create: `src/routers/audit/router.py`
- Modify: `src/main.py`

**Interfaces:**
- Consumes: `AuditLog` model, `require_roles`
- Produces: `AuditLogOut` schema; `GET /audit-log` (admin only, filtered + paginated)

- [ ] **Step 1: Add the response schema**

In `src/schemas.py`, at the end of the file, add:

```python
# ---- Audit log ----

class AuditLogOut(ORMModel):
    id_audit: uuid.UUID
    id_actor: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    detail: dict | None
    created_at: dt.datetime
```

- [ ] **Step 2: Create the repository**

Create `src/routers/audit/__init__.py` as an empty file.

Create `src/routers/audit/repository.py`:

```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import AuditLog


async def list_audit(
    session: AsyncSession,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    action: str | None = None,
    id_actor: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if id_actor is not None:
        stmt = stmt.where(AuditLog.id_actor == id_actor)
    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
```

- [ ] **Step 3: Create the router**

Create `src/routers/audit/router.py`:

```python
import uuid

from fastapi import APIRouter, Depends, Query

from ...deps import get_session, require_roles
from ...schemas import AuditLogOut
from . import repository as repo

router = APIRouter(prefix="/audit-log", tags=["audit"])


@router.get("", response_model=list[AuditLogOut], dependencies=[Depends(require_roles("admin"))])
async def list_audit_log(
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    action: str | None = None,
    id_actor: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session=Depends(get_session),
) -> list[AuditLogOut]:
    """Admin-only action history, newest first.

    Paginated by necessity, not preference: unlike the other list endpoints,
    this table grows without bound.
    """
    rows = await repo.list_audit(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        id_actor=id_actor,
        limit=limit,
        offset=offset,
    )
    return [AuditLogOut.model_validate(r) for r in rows]
```

- [ ] **Step 4: Register the router**

In `src/main.py`, add the import alongside the others:

```python
from .routers.audit.router import router as audit_router
```

and register it with the same prefix pattern as the rest:

```python
app.include_router(audit_router, prefix=p)
```

- [ ] **Step 5: Verify**

Run: `python -c "from src.main import app; print([r.path for r in app.routes if 'audit' in r.path])"`
Expected: prints `['/v1/api/audit-log']`.

Run: `pytest -q`
Expected: 62 passed, no warnings.

- [ ] **Step 6: Commit**

```bash
git add src/schemas.py src/routers/audit src/main.py
git commit -m "feat: admin-only paginated audit log endpoint"
```

---

### Task 3: Soft-delete data layer

**Files:**
- Modify: `db/schema.sql`
- Modify: `src/models.py`
- Modify: `src/schemas.py`
- Modify: `src/routers/articles/repository.py`
- Modify: `src/routers/users/repository.py`

**Interfaces:**
- Produces (consumed by Tasks 4-5):
  - `Article.deleted_at`, `User.deleted_at`
  - `ArticleOut.deleted_at`, `UserOut.deleted_at`
  - articles repo: `get_article(session, id, include_deleted=False)`, `list_articles_for(..., include_deleted=False)`, `soft_delete_article`, `restore_article`
  - users repo: `get_user(session, id, include_deleted=False)`, `list_users(session, include_deleted=False)`, `soft_delete_user`, `restore_user`, `email_exists` unchanged (must still see archived rows)

- [ ] **Step 1: Add the columns to `db/schema.sql`**

In `CREATE TABLE articles`, after `updated_at`, add:

```sql
    deleted_at              TIMESTAMPTZ
```

In `CREATE TABLE users`, after `created_at`, add:

```sql
    deleted_at          TIMESTAMPTZ
```

After the existing `articles` indexes add:

```sql
-- Partial indexes: the overwhelmingly common query is "live rows only".
CREATE INDEX idx_articles_live ON articles(id_article) WHERE deleted_at IS NULL;
```

and after the `users` table:

```sql
CREATE INDEX idx_users_live ON users(id_user) WHERE deleted_at IS NULL;
```

- [ ] **Step 2: Add the model columns**

In `src/models.py`, add to `Article`:

```python
    deleted_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
```

and the same line to `User`.

- [ ] **Step 3: Add the schema fields**

In `src/schemas.py`, add to `ArticleOut` and to `UserOut`:

```python
    deleted_at: dt.datetime | None = None
```

- [ ] **Step 4: Update the articles repository**

In `src/routers/articles/repository.py`, replace `get_article` and
`list_articles_for`, and add the two new functions:

```python
async def get_article(
    session: AsyncSession, id_article: uuid.UUID, include_deleted: bool = False
) -> Article | None:
    article = await session.get(Article, id_article)
    if article is None:
        return None
    if article.deleted_at is not None and not include_deleted:
        return None
    return article


async def list_articles_for(
    session: AsyncSession, role: str, id_user: str, include_deleted: bool = False
) -> list[Article]:
    stmt = select(Article)
    if role == "author":
        stmt = stmt.where(Article.id_user == uuid.UUID(id_user))
    elif role == "SC":
        stmt = stmt.where(
            Article.id_article.in_(
                select(ArticleReviewer.id_article).where(
                    ArticleReviewer.id_reviewer == uuid.UUID(id_user)
                )
            )
        )
    # EIC/admin see everything
    if not include_deleted:
        stmt = stmt.where(Article.deleted_at.is_(None))
    stmt = stmt.order_by(Article.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def soft_delete_article(session: AsyncSession, article: Article) -> None:
    article.deleted_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()


async def restore_article(session: AsyncSession, article: Article) -> None:
    article.deleted_at = None
    await session.flush()
```

Add `import datetime as dt` at the top of the file if it isn't already there.

Note `get_article` returning `None` for an archived article is what makes every
existing handler reject it with 404 automatically — they all already branch on
`if article is None: raise _not_found()`. That is deliberate: archived articles
drop out of the pipeline without touching each handler.

- [ ] **Step 5: Update the users repository**

In `src/routers/users/repository.py`, replace `get_user`, `list_users`, and
`delete_user`:

```python
async def get_user(
    session: AsyncSession, id_user: uuid.UUID, include_deleted: bool = False
) -> User | None:
    stmt = _select_with_relations().where(User.id_user == id_user)
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_users(session: AsyncSession, include_deleted: bool = False) -> list[User]:
    stmt = _select_with_relations().order_by(User.created_at.desc())
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def soft_delete_user(session: AsyncSession, id_user: uuid.UUID) -> bool:
    """Archives instead of deleting. This is also what fixes the previous 500:
    hard-deleting a user referenced by an article (as author or reviewer) raised
    a ForeignKeyViolationError."""
    user = await get_user(session, id_user)
    if user is None:
        return False
    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()
    return True


async def restore_user(session: AsyncSession, id_user: uuid.UUID) -> User | None:
    user = await get_user(session, id_user, include_deleted=True)
    if user is None or user.deleted_at is None:
        return None
    user.deleted_at = None
    await session.flush()
    return user


async def is_live(session: AsyncSession, id_user: uuid.UUID) -> bool:
    """Used by get_current_user to revoke archived users' tokens immediately."""
    result = await session.execute(
        select(User.id_user).where(User.id_user == id_user, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none() is not None
```

Add `import datetime as dt` at the top if missing. Delete the old
`delete_user` function — Task 5 switches its caller.

**Do not change `email_exists`** — it must keep seeing archived rows so an
archived address stays claimed.

- [ ] **Step 6: Verify imports**

Run: `python -c "from src.routers.articles import repository as a; from src.routers.users import repository as u; print(a.soft_delete_article, u.soft_delete_user, u.is_live)"`
Expected: prints all three function objects.

Run: `pytest -q`
Expected: 62 passed, no warnings.

- [ ] **Step 7: Commit**

```bash
git add db/schema.sql src/models.py src/schemas.py src/routers/articles/repository.py src/routers/users/repository.py
git commit -m "feat: soft-delete columns and repository support"
```

---

### Task 4: Article soft-delete endpoints

**Files:**
- Modify: `src/routers/articles/router.py`

**Interfaces:**
- Consumes: Task 3's repo functions
- Produces: `DELETE /articles/{id}` archiving instead of deleting;
  `POST /articles/{id}/restore`; `include_deleted` on `GET /articles`

- [ ] **Step 1: Rewrite `delete_article`**

Replace it with:

```python
@router.delete(
    "/{id_article}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_article(id_article: uuid.UUID, session=Depends(get_session)) -> None:
    """Archives the article. Versions, reviews and assignments are kept — the
    review record is the reason this is a soft delete."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    await repo.soft_delete_article(session, article)
```

- [ ] **Step 2: Add the restore endpoint**

Append to the file:

```python
@router.post(
    "/{id_article}/restore",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def restore_article(id_article: uuid.UUID, session=Depends(get_session)) -> ArticleOut:
    article = await repo.get_article(session, id_article, include_deleted=True)
    if article is None:
        raise _not_found()
    if article.deleted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "article is not archived")
    await repo.restore_article(session, article)
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "admin", reviewers)
```

- [ ] **Step 3: Add `include_deleted` to the list endpoint**

Replace `list_articles` with:

```python
@router.get("", response_model=list[ArticleOut])
async def list_articles(
    include_deleted: bool = False,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> list[ArticleOut]:
    if include_deleted and user.role != "admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only admin may list archived articles"
        )
    articles = await repo.list_articles_for(
        session, user.role, user.id_user, include_deleted=include_deleted
    )
    out = []
    for a in articles:
        reviewers = await repo.list_reviewer_ids(session, a.id_article)
        out.append(repo.to_article_out(a, user.role, reviewers))
    return out
```

- [ ] **Step 4: Let admin fetch an archived article by id**

In `get_article`, replace the lookup line:

```python
    article = await repo.get_article(session, id_article)
```

with:

```python
    # Admin can inspect an archive; everyone else sees a 404 for it.
    article = await repo.get_article(
        session, id_article, include_deleted=(user.role == "admin")
    )
```

Leave every other handler's `repo.get_article(session, id_article)` call
untouched — the default `include_deleted=False` is exactly what makes archived
articles unusable in the pipeline.

- [ ] **Step 5: Verify**

Run: `python -c "from src.main import app; print('ok')"`
Expected: prints `ok`.

Run: `grep -c "include_deleted" src/routers/articles/router.py`
Expected: 4 (list signature, list guard, list repo call, get_article call).

Run: `pytest -q`
Expected: 62 passed, no warnings.

- [ ] **Step 6: Commit**

```bash
git add src/routers/articles/router.py
git commit -m "feat: archive and restore articles instead of hard delete"
```

---

### Task 5: User soft-delete endpoints + immediate token revocation

**Files:**
- Modify: `src/routers/users/router.py`
- Modify: `src/routers/auth/router.py`
- Modify: `src/deps.py`

**Interfaces:**
- Consumes: Task 3's users repo functions (`soft_delete_user`, `restore_user`, `is_live`)
- Produces: `DELETE /users/{id}` archiving; `POST /users/{id}/restore`;
  `include_deleted` on `GET /users`; archived users rejected at login and on
  every authenticated request

- [ ] **Step 1: Switch the delete endpoint to archival**

In `src/routers/users/router.py`, replace `delete_user`:

```python
@router.delete(
    "/{id_user}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_user(id_user: uuid.UUID, session=Depends(get_session)) -> None:
    """Archives the user. Previously a hard delete, which raised a raw
    ForeignKeyViolationError (500) for any user referenced by an article as
    author or reviewer."""
    if not await repo.soft_delete_user(session, id_user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
```

- [ ] **Step 2: Add the restore endpoint**

Append to `src/routers/users/router.py`:

```python
@router.post(
    "/{id_user}/restore",
    response_model=UserOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def restore_user(id_user: uuid.UUID, session=Depends(get_session)) -> UserOut:
    user = await repo.restore_user(session, id_user)
    if user is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "user not found or not archived"
        )
    return repo.to_user_out(user)
```

- [ ] **Step 3: Add `include_deleted` to the user list**

Replace `list_users`:

```python
@router.get("", response_model=list[UserOut], dependencies=[Depends(require_roles("admin"))])
async def list_users(include_deleted: bool = False, session=Depends(get_session)) -> list[UserOut]:
    users = await repo.list_users(session, include_deleted=include_deleted)
    return [repo.to_user_out(u) for u in users]
```

(No role guard needed on the flag here — the whole endpoint is already
admin-only.)

- [ ] **Step 4: Reject archived users at login**

In `src/routers/auth/router.py`, the `login` handler selects the user then
checks the password. Add `User.deleted_at.is_(None)` to that query's `where`
clause so an archived user falls into the existing "invalid email or password"
401 — deliberately not a distinct message, to avoid disclosing that an account
was archived.

The query currently reads:

```python
    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.email == body.email)
    )
```

Change it to:

```python
    result = await session.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.email == body.email, User.deleted_at.is_(None))
    )
```

- [ ] **Step 5: Revoke archived users' existing tokens**

This is the security-critical step. A JWT stays valid until it expires, so
archiving a user does nothing on its own — their token keeps working for up to
`jwt_expire_minutes` (default 24h). `get_current_user` must verify liveness.

In `src/deps.py`, rewrite `get_current_user`:

```python
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
```

Add the imports this needs at the top of `src/deps.py`:

```python
import uuid

from .routers.users import repository as users_repository
```

**Watch for a circular import.** `src/routers/users/repository.py` imports from
`src.schemas`, and `src/deps.py` also imports from `src.schemas` — that is
fine. But `src/routers/users/router.py` imports `deps`. Importing the
*repository* (not the router) from `deps` avoids the cycle. Verify with the
Step 6 import check; if a cycle does appear, do the import inside the function
body instead of at module level and note it in your report.

- [ ] **Step 6: Verify**

Run: `python -c "from src.main import app; print('ok')"`
Expected: prints `ok` (this is also the circular-import check).

Run: `pytest -q`
Expected: 62 passed, no warnings.

- [ ] **Step 7: Commit**

```bash
git add src/routers/users/router.py src/routers/auth/router.py src/deps.py
git commit -m "feat: archive users, restore them, and revoke archived tokens"
```

---

### Task 6: Wire audit events into the handlers

**Files:**
- Modify: `src/routers/articles/router.py`
- Modify: `src/routers/users/router.py`

**Interfaces:**
- Consumes: `audit.record` from Task 1

- [ ] **Step 1: Import audit in both routers**

In `src/routers/articles/router.py` change the package import to include it:

```python
from ... import article_state, audit, emailer, storage
```

In `src/routers/users/router.py` add:

```python
from ... import audit
```

- [ ] **Step 2: Record article events**

Add an `audit.record(...)` call in each of these handlers in
`src/routers/articles/router.py`, placed after the change succeeds and before
the return. `id_actor` is `uuid.UUID(user.id_user)` where a `user: UserCtx` is
available; where a handler only has `dependencies=[Depends(require_roles(...))]`
and no `user` parameter, add `user: UserCtx = Depends(get_current_user)` to its
signature so the actor can be recorded.

`create_article` — after the version insert:
```python
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.created",
        entity_type="article",
        entity_id=article.id_article,
        detail={"title": article.title},
    )
```

`create_article`, `submit_full_paper`, `submit_revision` — also record the
version, after each `repo.add_article_version(...)`:
```python
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.version_submitted",
        entity_type="article",
        entity_id=id_article,
        detail={"phase": "<abstract or full_paper for this handler>"},
    )
```
(In `create_article` use `article.id_article` and phase `"abstract"`; in the
other two use `id_article` and phase `"full_paper"`.)

`submit_full_paper`, `submit_revision`, `review_article`, `announce_article`,
`unassign_reviewer` — every handler that changes `article.status` records the
transition. Capture the old value BEFORE assigning the new one:
```python
    previous_status = article.status
```
then after the assignment:
```python
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.status_changed",
        entity_type="article",
        entity_id=id_article,
        detail={"from": previous_status, "to": article.status},
    )
```
In `unassign_reviewer` the status change is conditional — only record it if the
status actually changed.

`_assign_reviewers` — record one row per newly assigned reviewer. It needs the
actor, so add an `id_actor: uuid.UUID` parameter to the helper and pass it from
both call sites (`assign_article` and `assign_reviewers`). Inside the
`newly_assigned` loop:
```python
        await audit.record(
            session,
            id_actor=id_actor,
            action="reviewer.assigned",
            entity_type="article",
            entity_id=article.id_article,
            detail={"id_reviewer": str(id_reviewer), "coi_overridden": override_coi},
        )
```
Recording whether COI was overridden is the point — it is exactly the
discretionary act an audit trail exists for.

`unassign_reviewer`:
```python
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="reviewer.unassigned",
        entity_type="article",
        entity_id=id_article,
        detail={"id_reviewer": str(id_reviewer)},
    )
```

`review_article` — after `repo.add_review(...)`:
```python
    await audit.record(
        session,
        id_actor=id_reviewer,
        action="review.submitted",
        entity_type="article",
        entity_id=id_article,
        detail={
            "id_reviewer": str(id_reviewer),
            "id_version": str(version.id_version),
            "decision": decision,
        },
    )
```

`delete_article` and `restore_article`:
```python
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="article.deleted",   # or "article.restored"
        entity_type="article",
        entity_id=id_article,
        detail={},
    )
```

- [ ] **Step 3: Record user events**

In `src/routers/users/router.py`, add `user: UserCtx = Depends(get_current_user)`
to `create_user`, `delete_user` and `restore_user` where missing, then record:

`create_user` — after the user is created:
```python
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="user.created",
        entity_type="user",
        entity_id=created.id_user,
        detail={"role": body.name_role.value},
    )
```
(name the created object so it doesn't shadow the actor `user` parameter — e.g.
`created = await repo.create_user(...)`.)

`delete_user` / `restore_user`:
```python
    await audit.record(
        session,
        id_actor=uuid.UUID(user.id_user),
        action="user.deleted",   # or "user.restored"
        entity_type="user",
        entity_id=id_user,
        detail={},
    )
```

Do NOT audit `POST /auth/register` — self-registration has no authenticated
actor, and `user.created` is documented as an admin action. Note this in your
report so the gap is deliberate and visible.

- [ ] **Step 4: Verify**

Run: `python -c "from src.main import app; print('ok')"`
Expected: prints `ok`.

Run: `grep -c "audit.record" src/routers/articles/router.py`
Expected: at least 9.

Run: `grep -c "audit.record" src/routers/users/router.py`
Expected: 3.

Run: `pytest -q`
Expected: 62 passed, no warnings.

- [ ] **Step 5: Commit**

```bash
git add src/routers/articles/router.py src/routers/users/router.py
git commit -m "feat: record audit events for article and user actions"
```

---

### Task 7: Migration 003 + manual verification

**Files:**
- Create: `db/migrations/003_audit_log_and_soft_delete.sql`

- [ ] **Step 1: Write the migration**

Create `db/migrations/003_audit_log_and_soft_delete.sql`:

```sql
-- Migration 003: audit log + soft delete
--
-- Purely additive: creates one table, adds two nullable columns, adds indexes.
-- Nothing is dropped and nothing needs backfilling — there is no historical
-- action data to reconstruct, and every existing row is live by definition
-- (deleted_at NULL).
--
-- Fresh databases do not need this; db/schema.sql already has all of it.

BEGIN;

CREATE TABLE audit_log (
    id_audit      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_actor      UUID REFERENCES users(id_user),
    action        VARCHAR(50) NOT NULL,
    entity_type   VARCHAR(30) NOT NULL,
    entity_id     UUID,
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_created_at ON audit_log(created_at DESC);
CREATE INDEX idx_audit_log_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_log_actor ON audit_log(id_actor);

ALTER TABLE articles ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE users    ADD COLUMN deleted_at TIMESTAMPTZ;

CREATE INDEX idx_articles_live ON articles(id_article) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_live ON users(id_user) WHERE deleted_at IS NULL;

COMMIT;
```

Do NOT execute it — the controller runs it against the live dev database after
taking a backup.

- [ ] **Step 2: Commit**

```bash
git add db/migrations/003_audit_log_and_soft_delete.sql
git commit -m "feat: migration 003 for audit log and soft delete"
```

- [ ] **Step 3: Controller-run verification (skip if you have no Docker)**

Back up first:
```bash
docker exec be-postgres-1 pg_dump -U simit -d simit > db/backups/pre-003-$(date +%Y%m%d).sql
```
Confirm non-empty, then:
```bash
docker exec -i be-postgres-1 psql -U simit -d simit -v ON_ERROR_STOP=1 < db/migrations/003_audit_log_and_soft_delete.sql
docker-compose up -d --build app
```

Then verify, in order:

1. **Schema**: `\d audit_log` shows 7 columns + 3 indexes; `articles` and
   `users` both have `deleted_at`; both partial indexes exist.
2. **Data intact**: article and user counts unchanged from before the migration.
3. **The old 500 is fixed**: as admin, `DELETE /users/{id}` on a user who
   authors an article AND is an assigned reviewer → **204** (was 500).
4. **Archived user disappears**: `GET /users` no longer lists them;
   `GET /users?include_deleted=true` does.
5. **Token revocation**: capture that user's token BEFORE archiving them, then
   after archiving use it on any authenticated endpoint → **401**. Then confirm
   they cannot log in → **401**.
6. **Restore**: `POST /users/{id}/restore` → 200, they can log in again.
   Restoring a live user → **409**.
7. **Article archive**: `DELETE /articles/{id}` → 204; the article vanishes from
   `GET /articles`; a non-admin `GET /articles/{id}` → 404; admin
   `GET /articles/{id}` shows it with `deleted_at` set; its versions and reviews
   still exist in the database.
8. **Archived article is out of the pipeline**: `POST /articles/{id}/review` or
   `/announce` on it → 404.
9. **Restore article**: `POST /articles/{id}/restore` → 200, reachable again.
10. **`include_deleted` is admin-only**: as EIC, `GET /articles?include_deleted=true`
    → **403**.
11. **Audit log**: as admin, `GET /audit-log` returns rows for the actions just
    performed, newest first, each with the right `id_actor` and `action`.
    Check `?entity_type=article&entity_id=<id>` filters to one paper, and that
    `reviewer.assigned` recorded `coi_overridden` correctly (assign once
    normally, once with override, and compare).
12. **Pagination bounds**: `?limit=1` returns one row; `?limit=0` and
    `?limit=201` are both **422**.
13. **Audit is admin-only**: as EIC, `GET /audit-log` → **403**.

- [ ] **Step 4: Clean up test data**

Delete (hard-delete via SQL if needed, since the API now only archives) the
articles and users created for verification, and confirm the pre-existing
counts are restored.
