# Paper Status Redesign + Versioning + File Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split article review into a two-phase state machine (abstract accept/reject, full-paper revision-loop/accept), add a version history table, and add a real PDF upload endpoint backed by a swappable S3-compatible storage client.

**Architecture:** Status-transition logic is pulled into a new pure module (`src/article_state.py`) with no DB/HTTP dependencies, so it's unit-testable without a database. Router handlers stay thin: load the article, call the pure transition function, persist, respond. Versioning is an insert-only side table (`article_version`) written alongside every abstract/full-paper file submission. Upload is a standalone endpoint that returns a file path string — it does not mutate article state, matching how the frontend already treats `file_path` fields as opaque strings.

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Pydantic v2, boto3 (S3-compatible storage, sync client wrapped in a thread), pytest + pytest-asyncio for the new pure-logic test coverage.

## Global Constraints

- No revision-count cap on the full-paper revision loop (product decision).
- EIC-announce mediation step applies to both abstract and full-paper decisions (product decision).
- Abstract file (`abstract_file_path`) stays a required field alongside the text `abstract` field — unchanged from current behavior (product decision).
- Storage credentials are placeholders (`storage_*` settings default to `""`); the upload endpoint must fail loudly (500, explicit message) if unconfigured rather than silently degrading.
- Only PDF (`application/pdf`) is accepted by the upload endpoint.
- `db/schema.sql` is the only source of truth for schema (no migration tool in this repo) — changes are direct edits, applied via a fresh `docker-compose down -v && docker-compose up -d` in dev.
- Git was initialized on 2026-07-26 (repo had none before). Each task's implementer commits its own changes as usual under the subagent-driven-development process; there is no pre-existing history to preserve, so no special handling is needed beyond normal commit hygiene.

---

### Task 1: Test tooling + pure state-machine module

**Files:**
- Modify: `requirements.txt`
- Create: `src/article_state.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_article_state.py`

**Interfaces:**
- Produces (used by Task 6's router changes):
  - `ABSTRACT_REVIEWABLE: set[str]` — statuses from which SC can review an abstract
  - `FULL_PAPER_REVIEWABLE: set[str]` — statuses from which SC can review a full paper
  - `decide_abstract_review(accept: bool) -> str`
  - `decide_full_paper_review(decision: str) -> str` (raises `ValueError` on unknown `decision`)
  - `announce_result(current_status: str) -> str` (raises `ValueError` if `current_status` isn't announceable)

- [ ] **Step 1: Add pytest to requirements.txt**

Append to `requirements.txt`:

```
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [ ] **Step 2: Install and confirm pytest runs**

Run: `pip install -r requirements.txt`
Run: `pytest --version`
Expected: prints pytest 8.3.3, no errors.

- [ ] **Step 3: Write the failing tests**

Create `tests/__init__.py` (empty file — makes `tests` a package so imports resolve consistently).

Create `tests/test_article_state.py`:

```python
import pytest

from src import article_state


def test_decide_abstract_review_accept():
    assert article_state.decide_abstract_review(True) == "abstract_decided_accept"


def test_decide_abstract_review_reject():
    assert article_state.decide_abstract_review(False) == "abstract_decided_reject"


def test_decide_full_paper_review_accept():
    assert article_state.decide_full_paper_review("accept") == "full_paper_decided_accept"


def test_decide_full_paper_review_revision():
    assert article_state.decide_full_paper_review("revision") == "full_paper_decided_revision"


def test_decide_full_paper_review_unknown_decision_raises():
    with pytest.raises(ValueError):
        article_state.decide_full_paper_review("maybe")


@pytest.mark.parametrize(
    "decided_status,expected",
    [
        ("abstract_decided_accept", "abstract_accepted"),
        ("abstract_decided_reject", "rejected"),
        ("full_paper_decided_revision", "revision_needed"),
        ("full_paper_decided_accept", "accepted"),
    ],
)
def test_announce_result(decided_status, expected):
    assert article_state.announce_result(decided_status) == expected


def test_announce_result_not_announceable_raises():
    with pytest.raises(ValueError):
        article_state.announce_result("submitted")


def test_abstract_reviewable_set():
    assert article_state.ABSTRACT_REVIEWABLE == {"assigned_to_sc"}


def test_full_paper_reviewable_set():
    assert article_state.FULL_PAPER_REVIEWABLE == {"full_paper_submitted"}
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_article_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.article_state'`

- [ ] **Step 5: Implement `src/article_state.py`**

```python
"""Pure status-transition logic for the article review pipeline.

No DB or HTTP dependencies — router handlers call these functions and then
persist the returned status. Kept separate so the state machine (the
highest-risk part of the review pipeline) is unit-testable on its own.
"""

ABSTRACT_REVIEWABLE = {"assigned_to_sc"}
FULL_PAPER_REVIEWABLE = {"full_paper_submitted"}

_ANNOUNCE_ABSTRACT_ACCEPT = "abstract_decided_accept"
_ANNOUNCE_ABSTRACT_REJECT = "abstract_decided_reject"
_ANNOUNCE_FULL_PAPER_REVISION = "full_paper_decided_revision"
_ANNOUNCE_FULL_PAPER_ACCEPT = "full_paper_decided_accept"

_ANNOUNCE_MAP = {
    _ANNOUNCE_ABSTRACT_ACCEPT: "abstract_accepted",
    _ANNOUNCE_ABSTRACT_REJECT: "rejected",
    _ANNOUNCE_FULL_PAPER_REVISION: "revision_needed",
    _ANNOUNCE_FULL_PAPER_ACCEPT: "accepted",
}


def decide_abstract_review(accept: bool) -> str:
    return _ANNOUNCE_ABSTRACT_ACCEPT if accept else _ANNOUNCE_ABSTRACT_REJECT


def decide_full_paper_review(decision: str) -> str:
    if decision == "accept":
        return _ANNOUNCE_FULL_PAPER_ACCEPT
    if decision == "revision":
        return _ANNOUNCE_FULL_PAPER_REVISION
    raise ValueError(f"unknown decision: {decision!r}")


def announce_result(current_status: str) -> str:
    if current_status not in _ANNOUNCE_MAP:
        raise ValueError(f"cannot announce from status: {current_status!r}")
    return _ANNOUNCE_MAP[current_status]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_article_state.py -v`
Expected: 9 passed.

---

### Task 2: Database schema — new enum values + `article_version` table

**Files:**
- Modify: `db/schema.sql`

**Interfaces:**
- Produces: `article_status` enum with values `submitted, assigned_to_sc,
  abstract_decided_accept, abstract_decided_reject, abstract_accepted,
  rejected, full_paper_submitted, full_paper_decided_revision,
  full_paper_decided_accept, revision_needed, accepted` (replaces the old
  9-value enum). New table `article_version(id_version, id_article, phase,
  version_number, file_path, submitted_by, submitted_at)` consumed by
  Task 5's repository functions.

- [ ] **Step 1: Replace the `article_status` enum definition**

In `db/schema.sql`, replace lines 69-79 (the `CREATE TYPE article_status ...`
block) with:

```sql
CREATE TYPE article_status AS ENUM (
    'submitted',                    -- peserta submit abstrak
    'assigned_to_sc',                -- EIC delivery task ke SC (juga: SC review queue)
    'abstract_decided_accept',       -- internal: SC putuskan lolos, nunggu EIC announce
    'abstract_decided_reject',       -- internal: SC putuskan tolak, nunggu EIC announce
    'abstract_accepted',             -- EIC announce: abstrak diterima, author submit full paper
    'rejected',                      -- pipeline selesai, tidak lolos (terminal)
    'full_paper_submitted',          -- full paper (baru atau revisi) masuk antrian SC
    'full_paper_decided_revision',   -- internal: SC minta revisi, nunggu EIC announce
    'full_paper_decided_accept',     -- internal: SC terima, nunggu EIC announce
    'revision_needed',               -- EIC announce: author harus resubmit full paper
    'accepted'                       -- EIC announce: full paper diterima (terminal, ada id_recommended_journal)
);
```

- [ ] **Step 2: Add the `article_version` table**

In `db/schema.sql`, immediately after the `CREATE INDEX idx_articles_id_sc ...`
line (currently line 103), insert:

```sql
-- === Article file version history ===
-- One row per abstract/full-paper file submission (initial + every revision).
-- articles.abstract_file_path / full_paper_file_path always point at the
-- latest file for that phase; full history lives here.

CREATE TABLE article_version (
    id_version      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_article      UUID NOT NULL REFERENCES articles(id_article) ON DELETE CASCADE,
    phase           VARCHAR(20) NOT NULL CHECK (phase IN ('abstract', 'full_paper')),
    version_number  INT NOT NULL,
    file_path       VARCHAR(500) NOT NULL,
    submitted_by    UUID NOT NULL REFERENCES users(id_user),
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_article, phase, version_number)
);

CREATE INDEX idx_article_version_article ON article_version(id_article);
```

- [ ] **Step 3: Verify the SQL is syntactically valid**

There's no automated migration/test tooling in this repo for schema.sql. Verify
manually:

Run: `docker-compose down -v` (drops the existing dev volume so the init
script re-runs on next start — **only safe because this is local dev data**;
confirm with the user first if there's data anyone cares about)
Run: `docker-compose up -d postgres`
Run: `docker-compose logs postgres | grep -i error`
Expected: no output (no SQL errors during init).

Run: `docker-compose exec postgres psql -U simit -d simit -c "\d article_version"`
Expected: prints the `article_version` table structure with all 7 columns.

---

### Task 3: ORM models — updated enum, `ArticleVersion` model

**Files:**
- Modify: `src/models.py`

**Interfaces:**
- Consumes: nothing new (extends existing `Base`, `Article`, `User`)
- Produces: `ArticleVersion` SQLAlchemy model with attributes
  `id_version: uuid.UUID`, `id_article: uuid.UUID`, `phase: str`,
  `version_number: int`, `file_path: str`, `submitted_by: uuid.UUID`,
  `submitted_at: dt.datetime` — consumed by Task 5's repository.

- [ ] **Step 1: Update `ArticleStatusEnum`**

In `src/models.py`, replace the `ArticleStatusEnum` definition (lines 18-30)
with:

```python
ArticleStatusEnum = PGEnum(
    "submitted",
    "assigned_to_sc",
    "abstract_decided_accept",
    "abstract_decided_reject",
    "abstract_accepted",
    "rejected",
    "full_paper_submitted",
    "full_paper_decided_revision",
    "full_paper_decided_accept",
    "revision_needed",
    "accepted",
    name="article_status",
    create_type=False,  # already created by db/schema.sql
)
```

- [ ] **Step 2: Add the `ArticleVersion` model**

In `src/models.py`, after the `Article` class (after line 147, before the
`Timeline` class), add:

```python
class ArticleVersion(Base):
    __tablename__ = "article_version"

    id_version: Mapped[uuid.UUID] = _uuid_pk()
    id_article: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id_article", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    submitted_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id_user"), nullable=False
    )
    submitted_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `python -c "from src import models; print(models.ArticleVersion.__tablename__)"`
Expected: prints `article_version`, no import errors.

---

### Task 4: Author-facing status map update

**Files:**
- Modify: `src/status.py`
- Create: `tests/test_status.py`

**Interfaces:**
- Consumes: nothing new
- Produces: unchanged public signature (`AUTHOR_STATUS_MAP: dict[str, str]`,
  `to_author_view`, `apply_role_view`), only the dict contents change.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
import pytest

from src.status import AUTHOR_STATUS_MAP, to_author_view

ALL_STATUSES = [
    "submitted",
    "assigned_to_sc",
    "abstract_decided_accept",
    "abstract_decided_reject",
    "abstract_accepted",
    "rejected",
    "full_paper_submitted",
    "full_paper_decided_revision",
    "full_paper_decided_accept",
    "revision_needed",
    "accepted",
]


@pytest.mark.parametrize("real_status", ALL_STATUSES)
def test_every_real_status_has_an_author_mapping(real_status):
    assert real_status in AUTHOR_STATUS_MAP


def test_internal_decided_states_hidden_as_under_review():
    for internal in (
        "assigned_to_sc",
        "abstract_decided_accept",
        "abstract_decided_reject",
        "full_paper_submitted",
        "full_paper_decided_revision",
        "full_paper_decided_accept",
    ):
        assert AUTHOR_STATUS_MAP[internal] == "under_review"


def test_terminal_and_actionable_states_pass_through():
    assert AUTHOR_STATUS_MAP["abstract_accepted"] == "abstract_accepted"
    assert AUTHOR_STATUS_MAP["rejected"] == "rejected"
    assert AUTHOR_STATUS_MAP["revision_needed"] == "revision_needed"
    assert AUTHOR_STATUS_MAP["accepted"] == "accepted"


def test_to_author_view_maps_status_field():
    article = {"status": "assigned_to_sc", "title": "x"}
    assert to_author_view(article)["status"] == "under_review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_status.py -v`
Expected: FAIL — `KeyError` / assertion failures against the old status set.

- [ ] **Step 3: Update `AUTHOR_STATUS_MAP`**

Replace the `AUTHOR_STATUS_MAP` dict in `src/status.py` (lines 9-19) with:

```python
AUTHOR_STATUS_MAP = {
    "submitted": "submitted",
    "assigned_to_sc": "under_review",
    "abstract_decided_accept": "under_review",
    "abstract_decided_reject": "under_review",
    "abstract_accepted": "abstract_accepted",
    "rejected": "rejected",
    "full_paper_submitted": "under_review",
    "full_paper_decided_revision": "under_review",
    "full_paper_decided_accept": "under_review",
    "revision_needed": "revision_needed",
    "accepted": "accepted",
}
```

Also update the module docstring (lines 1-7) to describe the new two-phase
flow instead of the old one:

```python
"""Internal pipeline status vs. what the author is allowed to see.

EIC/SC/admin always see the real `article_status` enum value, including the
internal `*_decided_*` states (SC has decided, waiting on EIC to announce).
Authors only ever see: submitted, under_review, abstract_accepted,
revision_needed, accepted, rejected.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_status.py -v`
Expected: 15 passed (11 parametrized + 4 direct).

---

### Task 5: Schemas — review/revision/upload request & response models

**Files:**
- Modify: `src/schemas.py`

**Interfaces:**
- Consumes: `ArticleStatus` enum (existing, gets updated), `ORMModel` base
  (existing)
- Produces (consumed by Task 6's repository and Task 7's router):
  - `AbstractReviewRequest(accept: bool, notes: str | None)`
  - `FullPaperReviewRequest(decision: Literal["accept", "revision"], notes: str | None, id_recommended_journal: uuid.UUID | None)`
  - `ArticleVersionOut(id_version, id_article, phase, version_number, file_path, submitted_by, submitted_at)` (ORMModel)
  - `UploadResponse(file_path: str)`

- [ ] **Step 1: Update the `ArticleStatus` enum**

In `src/schemas.py`, replace the `ArticleStatus` enum (lines 32-42) with:

```python
class ArticleStatus(str, Enum):
    submitted = "submitted"
    assigned_to_sc = "assigned_to_sc"
    abstract_decided_accept = "abstract_decided_accept"
    abstract_decided_reject = "abstract_decided_reject"
    abstract_accepted = "abstract_accepted"
    rejected = "rejected"
    full_paper_submitted = "full_paper_submitted"
    full_paper_decided_revision = "full_paper_decided_revision"
    full_paper_decided_accept = "full_paper_decided_accept"
    revision_needed = "revision_needed"
    accepted = "accepted"
```

- [ ] **Step 2: Remove the now-obsolete `ArticleReviewRequest`**

Delete the `ArticleReviewRequest` class (the old combined `lolos`/`notes`/
`id_recommended_journal` schema, originally lines 243-246) — it's replaced by
the two schemas below.

- [ ] **Step 3: Add the new request/response schemas**

In `src/schemas.py`, in the `# ---- Articles ----` section, after
`ArticleAssignRequest`, add:

```python
class AbstractReviewRequest(BaseModel):
    accept: bool
    notes: str | None = None


class FullPaperReviewRequest(BaseModel):
    decision: Literal["accept", "revision"]
    notes: str | None = None
    id_recommended_journal: uuid.UUID | None = None

    @model_validator(mode="after")
    def _journal_required_on_accept(self) -> "FullPaperReviewRequest":
        if self.decision == "accept" and self.id_recommended_journal is None:
            raise ValueError("id_recommended_journal is required when decision is 'accept'")
        return self


class ArticleVersionOut(ORMModel):
    id_version: uuid.UUID
    id_article: uuid.UUID
    phase: str
    version_number: int
    file_path: str
    submitted_by: uuid.UUID
    submitted_at: dt.datetime


class UploadResponse(BaseModel):
    file_path: str
```

Note this moves the `id_recommended_journal` validation into the schema
itself (via `model_validator`), which is stricter than the spec's "validate
in the router" sketch — same outcome (400-equivalent via FastAPI's 422 on
validation error), less router branching.

- [ ] **Step 4: Add the `Literal` import**

At the top of `src/schemas.py`, update:

```python
from typing import Annotated
```

to:

```python
from typing import Annotated, Literal
```

- [ ] **Step 5: Verify the module imports cleanly**

Run: `python -c "from src import schemas; schemas.FullPaperReviewRequest(decision='accept', id_recommended_journal='00000000-0000-0000-0000-000000000000')"`
Expected: no error (prints nothing, exits 0).

Run: `python -c "from src import schemas; schemas.FullPaperReviewRequest(decision='accept')"`
Expected: raises `pydantic.ValidationError` mentioning `id_recommended_journal is required when decision is 'accept'`.

---

### Task 6: Storage client (S3-compatible upload)

**Files:**
- Modify: `requirements.txt`
- Modify: `src/settings.py`
- Create: `src/storage.py`
- Create: `tests/test_storage.py`

**Interfaces:**
- Consumes: `settings` singleton from `src/settings.py`
- Produces (consumed by Task 8's router):
  - `class StorageNotConfiguredError(RuntimeError)`
  - `class StorageClient` with `async def upload(self, filename: str, content: bytes, content_type: str) -> str`
  - module-level singleton `client = StorageClient()`

- [ ] **Step 1: Add `boto3` and `python-multipart` to requirements.txt**

FastAPI's `UploadFile` (used by the upload endpoint in Task 8) requires
`python-multipart` to parse `multipart/form-data`. `boto3` is the S3-compatible
client (works against AWS S3, MinIO, Cloudflare R2, etc. via `endpoint_url`).

Append to `requirements.txt`:

```
boto3==1.35.68
python-multipart==0.0.17
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Add storage settings**

In `src/settings.py`, after the `# ---- SMTP ...` block, add:

```python
    # ---- Storage (S3-compatible: AWS S3, MinIO, Cloudflare R2, ...) ----
    # All empty by default — placeholders until real credentials exist.
    storage_base_url: str = ""
    storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_region: str = "auto"
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_storage.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from src import storage


@pytest.mark.asyncio
async def test_upload_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_base_url", "")
    client = storage.StorageClient()
    with pytest.raises(storage.StorageNotConfiguredError):
        await client.upload("paper.pdf", b"%PDF-1.4...", "application/pdf")


@pytest.mark.asyncio
async def test_upload_calls_boto3_put_object_and_returns_url(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_base_url", "https://storage.example.com")
    monkeypatch.setattr(storage.settings, "storage_bucket", "papers")
    monkeypatch.setattr(storage.settings, "storage_access_key", "key")
    monkeypatch.setattr(storage.settings, "storage_secret_key", "secret")
    monkeypatch.setattr(storage.settings, "storage_region", "auto")

    mock_s3 = MagicMock()
    with patch.object(storage.boto3, "client", return_value=mock_s3) as mock_boto_client:
        client = storage.StorageClient()
        result = await client.upload("paper.pdf", b"%PDF-1.4...", "application/pdf")

    mock_boto_client.assert_called_once_with(
        "s3",
        endpoint_url="https://storage.example.com",
        aws_access_key_id="key",
        aws_secret_access_key="secret",
        region_name="auto",
    )
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "papers"
    assert call_kwargs["Body"] == b"%PDF-1.4..."
    assert call_kwargs["ContentType"] == "application/pdf"
    assert call_kwargs["Key"].endswith("paper.pdf")
    assert result == f"https://storage.example.com/papers/{call_kwargs['Key']}"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.storage'`

- [ ] **Step 5: Implement `src/storage.py`**

```python
"""S3-compatible file storage client (AWS S3, MinIO, Cloudflare R2, ...).

boto3 is sync-only; `upload` wraps the blocking call in a thread so it's
safe to await from FastAPI's async handlers. Credentials come from
`settings.storage_*` — all empty by default, so `upload` fails loudly
until they're filled in rather than silently writing nowhere.
"""

import asyncio
import uuid

import boto3

from .settings import settings


class StorageNotConfiguredError(RuntimeError):
    pass


class StorageClient:
    def _make_s3_client(self):
        return boto3.client(
            "s3",
            endpoint_url=settings.storage_base_url,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            region_name=settings.storage_region,
        )

    async def upload(self, filename: str, content: bytes, content_type: str) -> str:
        if not settings.storage_base_url or not settings.storage_bucket:
            raise StorageNotConfiguredError(
                "storage is not configured — set storage_base_url and storage_bucket"
            )

        key = f"{uuid.uuid4()}-{filename}"

        def _put() -> None:
            s3 = self._make_s3_client()
            s3.put_object(
                Bucket=settings.storage_bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )

        await asyncio.to_thread(_put)
        return f"{settings.storage_base_url}/{settings.storage_bucket}/{key}"


client = StorageClient()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: 2 passed.

---

### Task 7: Repository — version history read/write helpers

**Files:**
- Modify: `src/routers/articles/repository.py`

**Interfaces:**
- Consumes: `ArticleVersion` model (Task 3)
- Produces (consumed by Task 8's router):
  - `async def add_article_version(session, *, id_article: uuid.UUID, phase: str, file_path: str, submitted_by: uuid.UUID) -> ArticleVersion`
  - `async def list_versions(session, id_article: uuid.UUID) -> list[ArticleVersion]`

- [ ] **Step 1: Add the import**

In `src/routers/articles/repository.py`, update the models import:

```python
from ...models import Article, ArticleVersion, User
```

Also add `func` to the sqlalchemy import:

```python
from sqlalchemy import func, select
```

- [ ] **Step 2: Add the version helpers**

Append to `src/routers/articles/repository.py`:

```python
async def _next_version_number(session: AsyncSession, id_article: uuid.UUID, phase: str) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(ArticleVersion.version_number), 0)).where(
            ArticleVersion.id_article == id_article, ArticleVersion.phase == phase
        )
    )
    return result.scalar_one() + 1


async def add_article_version(
    session: AsyncSession,
    *,
    id_article: uuid.UUID,
    phase: str,
    file_path: str,
    submitted_by: uuid.UUID,
) -> ArticleVersion:
    version_number = await _next_version_number(session, id_article, phase)
    version = ArticleVersion(
        id_article=id_article,
        phase=phase,
        version_number=version_number,
        file_path=file_path,
        submitted_by=submitted_by,
    )
    session.add(version)
    await session.flush()
    return version


async def list_versions(session: AsyncSession, id_article: uuid.UUID) -> list[ArticleVersion]:
    result = await session.execute(
        select(ArticleVersion)
        .where(ArticleVersion.id_article == id_article)
        .order_by(ArticleVersion.phase, ArticleVersion.version_number)
    )
    return list(result.scalars().all())
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `python -c "from src.routers.articles import repository; print(repository.add_article_version, repository.list_versions)"`
Expected: prints both function objects, no import errors.

---

### Task 8: Router — review/announce rewrite, revision + upload + versions endpoints

**Files:**
- Modify: `src/routers/articles/router.py`

**Interfaces:**
- Consumes: `article_state` (Task 1), `repo.add_article_version` /
  `repo.list_versions` (Task 7), `storage.client` (Task 6),
  `AbstractReviewRequest` / `FullPaperReviewRequest` / `ArticleVersionOut` /
  `UploadResponse` (Task 5)
- Produces: updated `POST /articles/{id}/review`, updated
  `POST /articles/{id}/announce`, new `POST /articles/{id}/revision`, new
  `POST /articles/{id}/upload`, new `GET /articles/{id}/versions`

- [ ] **Step 1: Update imports**

In `src/routers/articles/router.py`, replace the imports block (lines 1-16)
with:

```python
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import ValidationError

from ... import article_state, emailer, storage
from ...deps import get_current_user, get_session, require_roles
from ...schemas import (
    AbstractReviewRequest,
    ArticleAssignRequest,
    ArticleCreate,
    ArticleFullPaperRequest,
    ArticleOut,
    ArticleUpdate,
    ArticleVersionOut,
    FullPaperReviewRequest,
    UploadResponse,
    UserCtx,
)
from . import repository as repo
```

- [ ] **Step 2: Wire version-tracking into `create_article`**

In `create_article` (originally lines 39-52), after
`article = await repo.create_article(...)`, add the version insert before
the `return`:

```python
@router.post(
    "",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("author"))],
)
async def create_article(
    body: ArticleCreate, user: UserCtx = Depends(get_current_user), session=Depends(get_session)
) -> ArticleOut:
    article = await repo.create_article(
        session,
        title=body.title,
        authors=body.authors,
        abstract=body.abstract,
        keywords=body.keywords,
        abstract_file_path=body.abstract_file_path,
        id_topic=body.id_topic,
        id_user=uuid.UUID(user.id_user),
    )
    await repo.add_article_version(
        session,
        id_article=article.id_article,
        phase="abstract",
        file_path=body.abstract_file_path,
        submitted_by=uuid.UUID(user.id_user),
    )
    await session.flush()
    return repo.to_article_out(article, "author")
```

- [ ] **Step 3: Replace `review_article`**

Replace the entire `review_article` function (originally lines 145-171) with:

```python
@router.post(
    "/{id_article}/review",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("SC"))],
)
async def review_article(
    id_article: uuid.UUID,
    body: dict,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """SC reviews the abstract or full paper. Body shape depends on which
    phase the article is currently in — validated against the matching
    schema once we know the phase."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_sc) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not the assigned reviewer")

    if article.status in article_state.ABSTRACT_REVIEWABLE:
        try:
            payload = AbstractReviewRequest(**body)
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.errors())
        article.status = article_state.decide_abstract_review(payload.accept)
        if payload.notes is not None:
            article.sc_notes = payload.notes
    elif article.status in article_state.FULL_PAPER_REVIEWABLE:
        try:
            payload = FullPaperReviewRequest(**body)
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.errors())
        article.status = article_state.decide_full_paper_review(payload.decision)
        if payload.notes is not None:
            article.sc_notes = payload.notes
        if payload.id_recommended_journal is not None:
            article.id_recommended_journal = payload.id_recommended_journal
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot review in status {article.status}")

    await session.flush()
    return repo.to_article_out(article, "SC")
```

- [ ] **Step 4: Replace `announce_article`**

Replace the entire `announce_article` function (originally lines 174-206)
with:

```python
@router.post(
    "/{id_article}/announce",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def announce_article(
    id_article: uuid.UUID,
    background_tasks: BackgroundTasks,
    session=Depends(get_session),
) -> ArticleOut:
    """EIC announces the SC's decision (abstract or full paper) to the author."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    try:
        new_status = article_state.announce_result(article.status)
    except ValueError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot announce in status {article.status}")

    if new_status == "accepted" and article.id_recommended_journal is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "id_recommended_journal not set")

    article.status = new_status
    await session.flush()

    author_email = await repo.get_user_email(session, article.id_user)
    background_tasks.add_task(
        emailer.send,
        author_email,
        f"Update on your article '{article.title}'",
        f"Your article status is now: {article.status}.",
    )
    return repo.to_article_out(article, "EIC")
```

- [ ] **Step 5: Wire version-tracking into `submit_full_paper`**

In `submit_full_paper` (originally lines 209-243), after
`article.full_paper_file_path = body.full_paper_file_path`, add the version
insert:

```python
@router.post(
    "/{id_article}/full-paper",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("author"))],
)
async def submit_full_paper(
    id_article: uuid.UUID,
    body: ArticleFullPaperRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """Author submits the full paper after being announced as abstract_accepted."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if article.status != "abstract_accepted":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot submit full paper in status {article.status}"
        )

    article.full_paper_file_path = body.full_paper_file_path
    article.status = "full_paper_submitted"
    await repo.add_article_version(
        session,
        id_article=id_article,
        phase="full_paper",
        file_path=body.full_paper_file_path,
        submitted_by=uuid.UUID(user.id_user),
    )
    await session.flush()

    sc_email = await repo.get_user_email(session, article.id_sc)
    background_tasks.add_task(
        emailer.send,
        sc_email,
        "Full paper submitted for review",
        f"Article '{article.title}' full paper is ready for your review.",
    )
    return repo.to_article_out(article, "author")
```

(Note the status precondition changed from `"announced"` to
`"abstract_accepted"` to match the new enum.)

- [ ] **Step 6: Add the `submit_revision` endpoint**

Append to `src/routers/articles/router.py`:

```python
@router.post(
    "/{id_article}/revision",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("author"))],
)
async def submit_revision(
    id_article: uuid.UUID,
    body: ArticleFullPaperRequest,
    background_tasks: BackgroundTasks,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """Author resubmits the full paper after SC/EIC returned revision_needed."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if article.status != "revision_needed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot resubmit revision in status {article.status}"
        )

    article.full_paper_file_path = body.full_paper_file_path
    article.status = "full_paper_submitted"
    await repo.add_article_version(
        session,
        id_article=id_article,
        phase="full_paper",
        file_path=body.full_paper_file_path,
        submitted_by=uuid.UUID(user.id_user),
    )
    await session.flush()

    sc_email = await repo.get_user_email(session, article.id_sc)
    background_tasks.add_task(
        emailer.send,
        sc_email,
        "Revised full paper submitted for review",
        f"Article '{article.title}' revised full paper is ready for your review.",
    )
    return repo.to_article_out(article, "author")
```

- [ ] **Step 7: Add the `upload_article_file` endpoint**

Append to `src/routers/articles/router.py`:

```python
@router.post("/{id_article}/upload", response_model=UploadResponse)
async def upload_article_file(
    id_article: uuid.UUID,
    file: UploadFile = File(...),
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> UploadResponse:
    """Uploads a PDF and returns its storage path. Does not mutate the
    article — the client passes the returned file_path into create/full-paper/
    revision requests separately, same as the existing string-path fields."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_user) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if file.content_type != "application/pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only PDF files are accepted")

    content = await file.read()
    try:
        path = await storage.client.upload(file.filename or "upload.pdf", content, file.content_type)
    except storage.StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    return UploadResponse(file_path=path)
```

- [ ] **Step 8: Add the `list_article_versions` endpoint**

Append to `src/routers/articles/router.py`:

```python
@router.get("/{id_article}/versions", response_model=list[ArticleVersionOut])
async def list_article_versions(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> list[ArticleVersionOut]:
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    _check_view_permission(article, user)
    versions = await repo.list_versions(session, id_article)
    return [ArticleVersionOut.model_validate(v) for v in versions]
```

(Reuses the existing `_check_view_permission` helper already defined above
`get_article` in this file — same viewing rules as the article itself:
owning author, assigned SC, or EIC/admin.)

- [ ] **Step 9: Verify the module imports cleanly and the app starts**

Run: `python -c "from src.routers.articles import router; print(len(router.router.routes))"`
Expected: prints a number ≥ 11 (original 8 routes + revision + upload +
versions), no import errors.

Run: `python -c "from src.main import app; print('ok')"`
Expected: prints `ok` — confirms the whole app wires together (router
registration in `main.py` doesn't need changes since `articles_router` is
already imported as a single object).

---

### Task 9: Manual end-to-end smoke test

There is no existing integration test harness against a real Postgres in
this repo, and building one (test containers, fixture data, transaction
rollback isolation) is a larger, separate effort than this plan's scope.
Until that exists, verify the new flow manually against the dev stack.

**Files:** none (verification only)

- [ ] **Step 1: Start the stack fresh**

Run: `docker-compose down -v && docker-compose up -d --build`
Run: `docker-compose logs -f app` (watch until it's serving; Ctrl+C once ready)

- [ ] **Step 2: Register an author, admin login, create SC/EIC users**

Use the seeded `admin@simit.local` account (password `Admin@123`) to log in
and create one `EIC` and one `SC` user via `POST /v1/api/users`. Register an
`author` via `POST /v1/api/auth/register` (needs an existing `occupation_name`
— list them first via `GET /v1/api/occupations`, or create one as admin via
`POST /v1/api/occupations` first).

- [ ] **Step 3: Walk the abstract phase**

As author: `POST /v1/api/articles` (create) → confirm response `status ==
"submitted"`.
As admin/EIC: `POST /v1/api/articles/{id}/assign` with the SC's id → confirm
`status == "assigned_to_sc"`.
As SC: `POST /v1/api/articles/{id}/review` with `{"accept": true}` → confirm
`status == "abstract_decided_accept"` (visible to SC/EIC; as author, `GET
/v1/api/articles/{id}` should show `"under_review"`).
As admin/EIC: `POST /v1/api/articles/{id}/announce` → confirm `status ==
"abstract_accepted"`.

- [ ] **Step 4: Walk the full-paper phase, including a revision loop**

As author: `POST /v1/api/articles/{id}/full-paper` with a `full_paper_file_path`
→ confirm `status == "full_paper_submitted"`.
As SC: `POST /v1/api/articles/{id}/review` with
`{"decision": "revision", "notes": "fix section 3"}` → confirm `status ==
"full_paper_decided_revision"`.
As admin/EIC: `POST /v1/api/articles/{id}/announce` → confirm `status ==
"revision_needed"`.
As author: `POST /v1/api/articles/{id}/revision` with a new
`full_paper_file_path` → confirm `status == "full_paper_submitted"` again.
As SC: `POST /v1/api/articles/{id}/review` with
`{"decision": "accept", "id_recommended_journal": "<uuid from GET /v1/api/journals>"}`
→ confirm `status == "full_paper_decided_accept"`.
As admin/EIC: `POST /v1/api/articles/{id}/announce` → confirm `status ==
"accepted"`.

- [ ] **Step 5: Verify version history**

As author or SC: `GET /v1/api/articles/{id}/versions` → confirm 3 rows:
`phase=abstract version_number=1`, `phase=full_paper version_number=1`,
`phase=full_paper version_number=2` (the revision).

- [ ] **Step 6: Verify upload endpoint fails loudly when unconfigured**

With `storage_base_url` unset (default `.env`): as author,
`POST /v1/api/articles/{id}/upload` with a PDF file → confirm `500` with
body mentioning "storage is not configured". Confirm a non-PDF file (e.g.
a `.txt`) is rejected with `400` before even reaching storage.

- [ ] **Step 7: Run the full pytest suite one more time**

Run: `pytest -v`
Expected: all tests from Tasks 1, 4, 6 pass (state machine, status map,
storage client) — nothing regressed.
