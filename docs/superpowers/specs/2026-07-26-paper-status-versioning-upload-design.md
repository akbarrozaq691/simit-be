# Paper Status Redesign + Versioning + File Upload

Date: 2026-07-26
Status: approved (design), pending implementation plan

## Context

Current `articles` pipeline (`db/schema.sql`, `src/routers/articles/`) treats abstract
review and full-paper review as one undifferentiated status track
(`submitted → assigned_to_sc → under_review → revision_needed/passed_review →
announced → full_paper_submitted → rejected/completed`). This has two problems:

1. **No distinct semantics per phase.** Abstract review should be a hard
   accept/reject (no revision loop). Full-paper review should support a
   revision loop (`need_revision` → author resubmits → re-reviewed) before a
   final accept, which must carry the recommended journal.
2. **No resubmission path.** Once `revision_needed` is set, `announce_article`
   unconditionally turns it into `rejected` — there is no way for an author to
   fix and resubmit. This blocks the full-paper revision loop entirely.

Additionally, the system has no real file upload (clients pass a raw
`file_path` string) and no history of prior file versions when a paper is
revised.

This spec covers three related changes, scoped together because versioning
only makes sense once resubmission exists, and resubmission only makes sense
once the status machine distinguishes abstract vs. full-paper phases:

- New two-phase status state machine
- `article_version` table + version history on every file submission
- Real file upload endpoint backed by a swappable cloud-storage client

Out of scope (deferred, not part of this spec): multi-reviewer/conflict-of-interest,
audit log, soft-delete for articles/users. These get their own specs later.

## Status State Machine

Replaces the `article_status` Postgres enum and all status-branching logic in
`src/routers/articles/router.py`.

```
Abstract phase
  submitted
    → assigned_to_sc              (EIC/admin: POST /articles/{id}/assign;
                                    this is also SC's reviewable queue state —
                                    no separate "under review" transition,
                                    matching the original schema where that
                                    value was never actually set by any code path)
    → abstract_decided_accept     (internal, SC: POST /articles/{id}/review)
    → abstract_decided_reject     (internal, SC: POST /articles/{id}/review)
  ---- EIC announce (POST /articles/{id}/announce) ----
    → abstract_accepted           (author must now submit full paper)
    → rejected                    (terminal)

Full-paper phase (only reachable from abstract_accepted)
  full_paper_submitted            (author: POST /articles/{id}/full-paper,
                                    or resubmission: POST /articles/{id}/revision)
    → full_paper_decided_revision (internal, SC: POST /articles/{id}/review)
    → full_paper_decided_accept   (internal, SC: POST /articles/{id}/review)
  ---- EIC announce (POST /articles/{id}/announce) ----
    → revision_needed             (author must resubmit — loops back to
                                    full_paper_submitted via POST .../revision)
    → accepted                    (terminal; id_recommended_journal required)
```

Notes:

- `_decided_*` states are **internal only** — they exist so the EIC-announce
  mediation step (existing pattern, kept for both phases per product
  decision) has something to act on. Authors never see these values.
- No cap on revision loop iterations (product decision: SC decides when it's
  done, no automatic reject-after-N-revisions).
- `rejected` is the sole terminal-failure state, reused for both phases
  (abstract hard-reject and — if ever needed — an EIC/admin override).
  There's no separate "full paper rejected" status because the confirmed
  flow only defines `need_revision` / `accept` for full-paper decisions.
- `completed` (old enum value) is dropped. `accepted` is the terminal success
  state; nothing downstream of it is modeled in-app.

### Author-facing status map (`src/status.py`)

Internal `_decided_*` states are folded into `under_review` for authors, same
pattern as today:

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

## Database Changes (`db/schema.sql`)

1. Replace the `article_status` enum values per the list above (Postgres
   requires `ALTER TYPE ... ADD VALUE` per value, or drop/recreate the type —
   since this is pre-production schema, recreate is fine: drop dependent
   default, drop type, recreate, re-add default).
2. New table:

```sql
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

`version_number` is a per-`(article, phase)` counter starting at 1, computed
in the repository layer (`select max(version_number) + 1`), not a DB
sequence — keeps it simple and phase-scoped.

`articles.abstract_file_path` / `articles.full_paper_file_path` keep pointing
at the latest file for that phase (unchanged columns/behavior); full history
lives in `article_version`.

## API Changes (`src/routers/articles/`)

### `POST /articles/{id}/upload` (new)

- Auth: any authenticated user who owns the article (author) — same
  ownership check as `update_article`.
- `multipart/form-data`, single field `file`.
- Validates `content_type == "application/pdf"` (reject 400 otherwise).
- Delegates to `src/storage.py` client, gets back a path/URL.
- Returns `{"file_path": "<path or url>"}`. Client then passes that string
  into `ArticleCreate.abstract_file_path`, `ArticleFullPaperRequest.full_paper_file_path`,
  or the new revision request body — this endpoint does **not** itself mutate
  the article, keeping it decoupled from article state transitions (mirrors
  how the frontend already treats file_path as an opaque string today).

### `POST /articles/{id}/review` (behavior change)

Body becomes phase-dependent. Two new schemas:

```python
class AbstractReviewRequest(BaseModel):
    accept: bool
    notes: str | None = None

class FullPaperReviewRequest(BaseModel):
    decision: Literal["accept", "revision"]
    notes: str | None = None
    id_recommended_journal: uuid.UUID | None = None  # required if decision == "accept"
```

Router picks the schema based on the article's current phase (status in
`{"abstract_under_review"}` → abstract body; status in
`{"full_paper_submitted"}` → full-paper body — FastAPI can't discriminate
this via `response_model`-style union cleanly, so the endpoint takes a raw
dict body and validates against the right Pydantic model internally, or
simplest: keep two separate request models but accept a single permissive
body and branch — implementation plan should pick whichever keeps the router
readable; both are under 20 lines either way). Sets the matching `_decided_*`
status. `id_sc` ownership check unchanged.

### `POST /articles/{id}/announce` (behavior change)

Branches on current `_decided_*` status:

- `abstract_decided_accept` → `abstract_accepted`
- `abstract_decided_reject` → `rejected`
- `full_paper_decided_revision` → `revision_needed`
- `full_paper_decided_accept` → `accepted` (requires `id_recommended_journal`
  already set from the review step; 409 if missing — shouldn't happen if
  review step validated it, but guard anyway)

Anything else → 409 (unchanged pattern).

### `POST /articles/{id}/revision` (new)

- Auth: author, must own the article.
- Precondition: `status == "revision_needed"`.
- Body: `{"full_paper_file_path": str}` (same shape as
  `ArticleFullPaperRequest`, reused).
- Inserts an `article_version` row (`phase="full_paper"`, next version
  number), updates `articles.full_paper_file_path`, sets
  `status = "full_paper_submitted"`.
- Notifies assigned SC by email (same pattern as `submit_full_paper`).

### `POST /articles` and `POST /articles/{id}/full-paper` (versioning hook)

Both now also insert an `article_version` row (`phase="abstract"` /
`phase="full_paper"` respectively, `version_number=1` for the first
abstract/full-paper submission).

## Storage Abstraction (`src/storage.py`, new file)

```python
class StorageClient(Protocol):
    async def upload(self, filename: str, content: bytes, content_type: str) -> str: ...
```

One implementation to start: a thin client that reads
`settings.storage_*` config and calls a generic S3-compatible PUT (works
against AWS S3, MinIO, Cloudflare R2, etc. — all speak the same API). Returns
the object's public/base URL.

`settings.py` additions:

```python
storage_base_url: str = ""
storage_bucket: str = ""
storage_access_key: str = ""
storage_secret_key: str = ""
storage_region: str = "auto"
```

All default to empty string — placeholders until real credentials exist.
Until filled in, the upload endpoint should fail fast with a clear 500
("storage not configured") rather than silently writing to local disk, so
the gap is loud in dev instead of masked.

## Error Handling

- Upload: 400 for wrong content-type, 500 (explicit message) if storage
  unconfigured, 502 if the storage backend call fails.
- Review/announce endpoints: existing 409-on-wrong-status pattern extended
  to the new phase-specific states — no new error-handling pattern needed.
- Revision endpoint: 409 if status isn't `revision_needed`, 403 if not the
  owning author.

## Testing

No existing test suite found in the repo (`requirements.txt` has no
pytest). Implementation plan should confirm whether to add one now (at
minimum: status-transition unit tests for the new state machine, since it's
the highest-risk part of this change) or continue manual verification —
this is a call for the implementation-planning step, not this design.

## Migration Note

`db/schema.sql` is the source of truth (no migrations directory found), so
this is a direct edit to the enum + new table. If there's existing seeded
data in a real Postgres instance using the old enum values, that data needs
a manual `UPDATE` mapping old→new status values before the type swap —
implementation plan should call this out explicitly if a live DB exists
beyond local dev.
