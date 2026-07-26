# API Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three defects logged in the previous feature's final review — `/review` publishes no OpenAPI schema, `version_number` allocation can collide under concurrency, and file upload buffers unbounded content.

**Architecture:** All three are contained fixes to two existing files. `/review` switches from a hand-validated `dict` body to a pydantic union, moving validation into FastAPI and adding an explicit phase/body-mismatch guard. Version allocation gains a SAVEPOINT-wrapped retry loop inside the existing repository helper. Upload gains a bounded read against a new configurable settings value.

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Pydantic v2, pytest.

## Global Constraints

- The `/review` change must NOT break existing clients: same URL, same accepted body shapes. A deployed frontend already calls it.
- Upload limit default is **10 MB**, configurable via `MAX_UPLOAD_MB`.
- Oversized upload returns **413**, not 400 (400 is already used for wrong content-type).
- Phase/body mismatch on `/review` returns **409**, with a message naming both the article's phase and the expected body shape.
- Version-race behavior is NOT unit-testable in this repo (no integration/concurrency test harness). Do not fabricate a test that merely asserts mocks; verify sequential allocation still works and note the gap.
- Existing test suite is 28 tests and its output is pristine (no warnings). Both properties must hold after every task.

---

### Task 1: Upload size limit

**Files:**
- Modify: `src/settings.py`
- Modify: `.env.example`
- Modify: `src/routers/articles/router.py` (the `upload_article_file` handler)
- Create: `tests/test_upload_limit.py`

**Interfaces:**
- Produces: `settings.max_upload_mb: int` (default 10), and a module-level
  `MAX_UPLOAD_BYTES` in the router computed from it.

- [ ] **Step 1: Add the setting**

In `src/settings.py`, inside the `Settings` class, immediately after the
`storage_region` line, add:

```python
    # ---- Uploads ----
    # Max accepted PDF size for POST /articles/{id}/upload.
    max_upload_mb: int = 10
```

- [ ] **Step 2: Add the .env.example entry**

In `.env.example`, append after the `STORAGE_REGION=auto` line:

```
# Max upload size in MB for article PDFs (default 10)
MAX_UPLOAD_MB=10
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_upload_limit.py`:

```python
"""The upload endpoint must reject oversized files with 413 without buffering
the whole body. These tests exercise the size-check helper directly — the
endpoint itself needs a live app + storage, covered by manual verification."""

import pytest

from src.routers.articles import router as articles_router
from src.settings import settings

# Derived, not hardcoded: MAX_UPLOAD_MB may be overridden via .env, and these
# tests assert the wiring and the boundary, not the specific default.
LIMIT = settings.max_upload_mb * 1024 * 1024


def test_default_limit_is_10_mb_unless_overridden():
    """Documents the intended default. Skips rather than fails if the local
    environment overrides it, since that's a valid configuration."""
    if settings.max_upload_mb != 10:
        pytest.skip(f"MAX_UPLOAD_MB overridden to {settings.max_upload_mb}")
    assert articles_router.MAX_UPLOAD_BYTES == 10 * 1024 * 1024


def test_max_upload_bytes_derived_from_settings():
    assert articles_router.MAX_UPLOAD_BYTES == LIMIT


@pytest.mark.parametrize("offset,should_reject", [(-LIMIT + 1, False), (-1, False), (0, False), (1, True)])
def test_exceeds_limit_boundary(offset, should_reject):
    content = b"x" * (LIMIT + offset)
    assert articles_router._exceeds_upload_limit(content) is should_reject
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_upload_limit.py -v`
Expected: FAIL — `AttributeError: module 'src.routers.articles.router' has no attribute 'MAX_UPLOAD_BYTES'`

- [ ] **Step 5: Implement in the router**

In `src/routers/articles/router.py`, add the import of settings alongside the
existing relative imports:

```python
from ...settings import settings
```

Then, just below the `router = APIRouter(...)` line, add:

```python
MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024


def _exceeds_upload_limit(content: bytes) -> bool:
    return len(content) > MAX_UPLOAD_BYTES
```

Then in `upload_article_file`, replace:

```python
    content = await file.read()
```

with:

```python
    # Read one byte past the limit: enough to detect an oversized upload
    # without ever buffering the whole thing.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if _exceeds_upload_limit(content):
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file too large (max {settings.max_upload_mb} MB)",
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_upload_limit.py -v`
Expected: 6 passed (or 5 passed + 1 skipped if MAX_UPLOAD_MB is overridden
locally).

Run: `pytest -q`
Expected: 34 passed, no warnings (28 existing + 6 new).

- [ ] **Step 7: Commit**

```bash
git add src/settings.py .env.example src/routers/articles/router.py tests/test_upload_limit.py
git commit -m "feat: cap article upload size at 10 MB (configurable)"
```

---

### Task 2: `/review` union body + phase-mismatch guard

**Files:**
- Modify: `src/routers/articles/router.py` (the `review_article` handler)
- Create: `tests/test_review_schemas.py`

**Interfaces:**
- Consumes: `AbstractReviewRequest`, `FullPaperReviewRequest` (already exist in
  `src/schemas.py`), `article_state.ABSTRACT_REVIEWABLE`,
  `article_state.FULL_PAPER_REVIEWABLE`
- Produces: `review_article` with signature
  `body: AbstractReviewRequest | FullPaperReviewRequest` — OpenAPI now
  publishes `anyOf` for the request body.

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_schemas.py`:

```python
"""The /review endpoint accepts two disjoint body shapes via a pydantic union.
These tests pin the discrimination behavior the endpoint relies on, plus the
OpenAPI schema it now publishes."""

import pytest
from pydantic import TypeAdapter, ValidationError

from src.main import app
from src.schemas import AbstractReviewRequest, FullPaperReviewRequest

ReviewBody = TypeAdapter(AbstractReviewRequest | FullPaperReviewRequest)


def test_accept_field_resolves_to_abstract_request():
    parsed = ReviewBody.validate_python({"accept": True, "notes": "ok"})
    assert isinstance(parsed, AbstractReviewRequest)
    assert parsed.accept is True


def test_decision_field_resolves_to_full_paper_request():
    parsed = ReviewBody.validate_python(
        {"decision": "revision", "notes": "fix section 3"}
    )
    assert isinstance(parsed, FullPaperReviewRequest)
    assert parsed.decision == "revision"


def test_body_matching_neither_shape_is_rejected():
    with pytest.raises(ValidationError):
        ReviewBody.validate_python({"nonsense": 1})


def test_empty_body_is_rejected():
    with pytest.raises(ValidationError):
        ReviewBody.validate_python({})


def test_openapi_publishes_a_request_schema_for_review():
    """Regression guard: the endpoint used to declare `body: dict`, which made
    OpenAPI publish no schema at all."""
    schema = app.openapi()
    path = schema["paths"]["/v1/api/articles/{id_article}/review"]["post"]
    body_schema = path["requestBody"]["content"]["application/json"]["schema"]
    rendered = str(body_schema)
    assert "AbstractReviewRequest" in rendered
    assert "FullPaperReviewRequest" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_review_schemas.py -v`
Expected: the four schema tests PASS (the union works already — they're
pinning behavior), and `test_openapi_publishes_a_request_schema_for_review`
FAILS, because the endpoint still declares `body: dict` so OpenAPI publishes
a bare object schema with neither model name in it.

- [ ] **Step 3: Rewrite the handler**

In `src/routers/articles/router.py`, replace the entire `review_article`
function with:

```python
@router.post(
    "/{id_article}/review",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("SC"))],
)
async def review_article(
    id_article: uuid.UUID,
    body: AbstractReviewRequest | FullPaperReviewRequest,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> ArticleOut:
    """SC reviews the abstract or the full paper. The body shape selects which:
    `{"accept": bool}` for an abstract, `{"decision": "accept"|"revision"}` for
    a full paper. The shape must match the phase the article is actually in."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if str(article.id_sc) != user.id_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not the assigned reviewer")

    if article.status in article_state.ABSTRACT_REVIEWABLE:
        if not isinstance(body, AbstractReviewRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"article is in abstract review (status {article.status}); "
                'expected an abstract review body: {"accept": bool}',
            )
        article.status = article_state.decide_abstract_review(body.accept)
        if body.notes is not None:
            article.sc_notes = body.notes
    elif article.status in article_state.FULL_PAPER_REVIEWABLE:
        if not isinstance(body, FullPaperReviewRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"article is in full-paper review (status {article.status}); "
                'expected a full-paper review body: {"decision": "accept"|"revision"}',
            )
        article.status = article_state.decide_full_paper_review(body.decision)
        if body.notes is not None:
            article.sc_notes = body.notes
        if body.id_recommended_journal is not None:
            article.id_recommended_journal = body.id_recommended_journal
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot review in status {article.status}")

    await session.flush()
    return repo.to_article_out(article, "SC")
```

- [ ] **Step 4: Remove the now-unused import**

`ValidationError` is no longer referenced anywhere in the file (FastAPI does
the validating now). Remove this line from the imports:

```python
from pydantic import ValidationError
```

Confirm with: `grep -n "ValidationError" src/routers/articles/router.py` —
expect no output.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_review_schemas.py -v`
Expected: 5 passed.

Run: `pytest -q`
Expected: 39 passed, no warnings (34 from Task 1 + 5 new).

Run: `python -c "from src.main import app; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/routers/articles/router.py tests/test_review_schemas.py
git commit -m "refactor: give /review a real OpenAPI schema via union body"
```

---

### Task 3: `version_number` collision retry

**Files:**
- Modify: `src/routers/articles/repository.py` (`add_article_version`)

**Interfaces:**
- Consumes: `ArticleVersion` model, existing `_next_version_number` helper
- Produces: `add_article_version` with unchanged signature and return type —
  callers in the router need no changes.

- [ ] **Step 1: Add the import**

In `src/routers/articles/repository.py`, add to the imports:

```python
from sqlalchemy.exc import IntegrityError
```

- [ ] **Step 2: Rewrite `add_article_version`**

Replace the existing `add_article_version` function with:

```python
_VERSION_INSERT_ATTEMPTS = 3


async def add_article_version(
    session: AsyncSession,
    *,
    id_article: uuid.UUID,
    phase: str,
    file_path: str,
    submitted_by: uuid.UUID,
) -> ArticleVersion:
    """Appends a version row, allocating the next per-(article, phase) number.

    Two concurrent submissions can read the same max and collide on the
    UNIQUE(id_article, phase, version_number) constraint. Each attempt runs in
    a SAVEPOINT so a violation can be rolled back without poisoning the caller's
    transaction, then the number is recomputed and the insert retried.
    """
    for attempt in range(1, _VERSION_INSERT_ATTEMPTS + 1):
        version_number = await _next_version_number(session, id_article, phase)
        version = ArticleVersion(
            id_article=id_article,
            phase=phase,
            version_number=version_number,
            file_path=file_path,
            submitted_by=submitted_by,
        )
        try:
            async with session.begin_nested():
                session.add(version)
                await session.flush()
        except IntegrityError:
            if attempt == _VERSION_INSERT_ATTEMPTS:
                raise
            continue
        return version

    # Unreachable: the loop either returns or raises on the final attempt.
    raise AssertionError("version insert loop exited without result")
```

- [ ] **Step 3: Verify the module imports and the suite still passes**

Run: `python -c "from src.routers.articles import repository; print(repository.add_article_version)"`
Expected: prints the function object.

Run: `pytest -q`
Expected: 39 passed, no warnings (unchanged from Task 2 — this task adds no tests).

- [ ] **Step 4: Verify sequential allocation still works against a live database**

The retry path itself needs true concurrency to trigger and there is no
integration harness in this repo (documented, accepted gap). What CAN be
verified is that the SAVEPOINT wrapper did not break normal sequential
allocation — that versions still come out 1, 2, 3.

Do NOT attempt this if Docker is unavailable in your environment; report that
instead and let the controller run it.

If Docker is available:
1. Confirm the app stack is up: `curl -s http://localhost:8888/health` → `{"status":"ok"}`
2. Walk one article through: create (abstract v1) → assign → review accept →
   announce → submit full paper (full_paper v1) → review revision → announce →
   submit revision (full_paper v2)
3. `GET /articles/{id}/versions` must return exactly three rows:
   `abstract` 1, `full_paper` 1, `full_paper` 2

- [ ] **Step 5: Commit**

```bash
git add src/routers/articles/repository.py
git commit -m "fix: retry version_number allocation on UNIQUE collision"
```

---

### Task 4: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full suite, pristine output**

Run: `pytest -q`
Expected: 39 passed, zero warnings.

- [ ] **Step 2: App wiring**

Run: `python -c "from src.main import app; print(len(app.routes))"`
Expected: a number ≥ 47 (unchanged — this plan adds no routes).

- [ ] **Step 3: Confirm the OpenAPI schema renders**

Run:
```bash
python -c "
from src.main import app
s = app.openapi()
b = s['paths']['/v1/api/articles/{id_article}/review']['post']['requestBody']
print(b['content']['application/json']['schema'])
"
```
Expected: output references both `AbstractReviewRequest` and
`FullPaperReviewRequest` (an `anyOf` of two `\$ref`s).

- [ ] **Step 4: Live check of the three fixed behaviors**

Requires the running stack (`docker-compose up -d`; rebuild the app image so
it picks up the code changes: `docker-compose up -d --build app`).

1. **Oversized upload → 413.** Create a >10 MB file and POST it to
   `/articles/{id}/upload` as the owning author with
   `type=application/pdf`. Expect 413 and a "file too large (max 10 MB)"
   detail. Then POST a small PDF and confirm the response is the usual
   500 "storage is not configured" (proving the size gate runs before, and
   does not replace, the storage path).
2. **Phase/body mismatch → 409.** On an article in `assigned_to_sc`, POST
   `{"decision": "accept", "id_recommended_journal": "<uuid>"}` as the assigned
   SC. Expect 409 naming the abstract phase and the expected body shape.
3. **Well-formed bodies still work.** On that same article, POST
   `{"accept": true}` → 200 with status `abstract_decided_accept`.
