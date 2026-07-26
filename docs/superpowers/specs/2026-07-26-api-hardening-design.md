# API Hardening: /review Schema, Version Race, Upload Size Limit

Date: 2026-07-26
Status: approved (design)

## Context

Three defects were logged during the final review of the two-phase review
pipeline work (see `2026-07-26-paper-status-versioning-upload-design.md`).
None block the shipped feature, but all three are real and worth closing
before further feature work. They are grouped into one spec because all
three touch the same two files (`src/routers/articles/router.py`,
`src/routers/articles/repository.py`) and none is large enough to warrant
its own design cycle.

## 1. `/review` has no discoverable request schema

`review_article` currently declares `body: dict` and validates it internally
against whichever schema matches the article's current phase
(`src/routers/articles/router.py:165`). This was deliberate — FastAPI cannot
discriminate two body shapes from a path parameter — but it means OpenAPI
publishes no schema at all for the endpoint. Clients cannot discover from
Swagger that two body shapes exist, nor what fields either one takes.

### Decision: union body type, one endpoint

```python
body: AbstractReviewRequest | FullPaperReviewRequest
```

`AbstractReviewRequest` requires `accept: bool`; `FullPaperReviewRequest`
requires `decision: Literal["accept","revision"]`. The required fields are
disjoint, so pydantic v2's smart-union resolves the shape unambiguously
without needing a discriminator field, and FastAPI publishes an `anyOf` of
both schemas.

This is **not** a breaking change: existing clients send the same bodies to
the same URL. Rejected alternatives: splitting into
`/review/abstract` + `/review/full-paper` (cleanest REST, but breaks the
already-deployed frontend), and keeping `dict` with hand-written
`openapi_extra` (documentation that drifts from the code).

### Consequences

- The two `try/except ValidationError` blocks disappear. FastAPI validates
  before the handler runs, so malformed bodies now produce FastAPI's standard
  422 envelope instead of a hand-rolled one — more consistent with every
  other endpoint in the app.
- A new failure mode becomes reachable and must be handled explicitly:
  a **phase/body mismatch** (e.g. an `{"accept": true}` body sent for an
  article sitting in `full_paper_submitted`). Previously impossible, because
  the schema was chosen *from* the phase. Now the client picks the shape, so
  the handler must verify the shape matches the phase and reject with
  **409** and a message naming both the phase and the expected body shape.
- Handler control flow becomes: resolve phase → assert body type matches
  phase (409 if not) → apply the decision.

## 2. `version_number` allocation can collide

`add_article_version` computes `max(version_number) + 1` in one statement
(`repository.py:_next_version_number`) and inserts in another. Two concurrent
submissions for the same `(article, phase)` can both read the same max and
both attempt the same `version_number`, hitting the
`UNIQUE (id_article, phase, version_number)` constraint. The loser surfaces
as an unhandled `IntegrityError` → 500.

Likelihood is low (it needs the same author submitting the same phase twice
simultaneously) but the failure is ugly and the fix is contained.

### Decision: SAVEPOINT + bounded retry

Wrap the insert in `session.begin_nested()` (a SAVEPOINT) and retry on
`IntegrityError`, recomputing the version number each attempt, up to 3
attempts. On the final failure, re-raise.

A SAVEPOINT is required because Postgres aborts the whole transaction on a
constraint violation — without one, a retry would run inside a poisoned
transaction. Rejected alternative: `SELECT ... FOR UPDATE` on the `articles`
row, which serializes correctly but requires changing the shared
`get_article` used by every endpoint, for a race that only affects this one
write path.

## 3. Upload reads unbounded file content into memory

`upload_article_file` calls `await file.read()` with no argument
(`router.py`), pulling the entire upload into memory before it ever reaches
the size-agnostic storage client. A large file pressures memory with no
guard.

### Decision: 10 MB cap, enforced before buffering completes

```python
content = await file.read(MAX_UPLOAD_BYTES + 1)
if len(content) > MAX_UPLOAD_BYTES:
    raise HTTPException(413, f"file too large (max {settings.max_upload_mb} MB)")
```

Reading `MAX + 1` bytes is what makes the check meaningful: it never buffers
more than one byte past the limit, so an oversized upload is rejected without
being fully read. Status **413 Payload Too Large** is the correct code (400
would conflate it with the existing wrong-content-type rejection).

The limit lives in settings as `max_upload_mb: int = 10`, so it is
env-configurable (`MAX_UPLOAD_MB`) alongside the existing `storage_*` keys,
and gets a matching `.env.example` entry.

Note this bounds the *request body* only. It does not bound total storage
consumption per author — that is quota management, out of scope here.

## Testing

- **Union discrimination** — unit-testable at the schema layer: an
  `{"accept": ...}` payload resolves to `AbstractReviewRequest`, a
  `{"decision": ...}` payload to `FullPaperReviewRequest`, and a payload
  matching neither raises `ValidationError`.
- **Size limit** — unit-testable boundary behavior: exactly-at-limit accepted,
  one byte over rejected with 413.
- **Version race** — NOT unit-testable in this repo. Reproducing it needs two
  genuinely concurrent database transactions, and there is no integration-test
  harness (no test containers, no fixture DB). The retry logic will be verified
  by code review plus a manual single-threaded check that normal sequential
  version allocation still produces 1, 2, 3. This is an accepted, documented
  coverage gap — building the harness is a larger separate effort.

## Out of Scope

Deferred Scope-B features remain untouched: multi-reviewer /
conflict-of-interest, audit log, soft-delete. Each gets its own spec. Note
that multi-reviewer will restructure reviewer assignment (`articles.id_sc`
becomes a relation), which is why it should precede audit-log work rather
than follow it.
