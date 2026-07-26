# Multi-Reviewer Assignment, Per-Reviewer Reviews, and Conflict-of-Interest

Date: 2026-07-27
Status: approved (design)

## Context

The review pipeline currently supports exactly **one** reviewer per article:
`articles.id_sc` (a single FK) plus `articles.sc_notes` (a single text column).
The SC's decision is encoded directly in the article's status
(`abstract_decided_accept`, `full_paper_decided_revision`, etc.), and
`announce` merely translates that internal status into a public one.

That shape cannot express what a real paper-management system needs: several
reviewers per paper, each with an independent decision and notes, and an
editor who weighs them. This spec restructures reviewer assignment and review
storage, and adds conflict-of-interest screening.

This is the first of three deferred Scope-B features. It comes first
deliberately: it restructures reviewer identity, so building the audit log or
soft-delete before it would mean reworking them afterward.

## Product Decisions (settled with the user)

- **Reviewer count is flexible** — the EIC assigns however many SCs a given
  paper needs (1, 2, 3…). No fixed minimum or maximum is enforced.
- **The EIC decides the outcome.** Reviewers advise; they do not vote. When
  reviewers disagree, the EIC sees every review and picks the result. No
  automatic majority or unanimity rule.
- **Announce requires all assigned reviewers to have submitted** for the
  current phase. If a reviewer goes unresponsive, the EIC unassigns them
  rather than waiting.
- **COI screening is automatic on institution match**, with an explicit EIC
  override.
- **Review is blind.** A reviewer sees only their own review. EIC/admin see
  all of them.

## Data Model

Two new tables. Assignment and review are deliberately separate: a reviewer
can be assigned but not yet have reviewed, and that gap is exactly what the
"all reviewers submitted" gate needs to measure.

```sql
CREATE TABLE article_reviewer (
    id_assignment   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_article      UUID NOT NULL REFERENCES articles(id_article) ON DELETE CASCADE,
    id_reviewer     UUID NOT NULL REFERENCES users(id_user),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_article, id_reviewer)
);

CREATE TABLE article_review (
    id_review       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_version      UUID NOT NULL REFERENCES article_version(id_version) ON DELETE CASCADE,
    id_reviewer     UUID NOT NULL REFERENCES users(id_user),
    decision        VARCHAR(20) NOT NULL CHECK (decision IN ('accept', 'reject', 'revision')),
    notes           TEXT,
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_version, id_reviewer)
);
```

### Why reviews attach to `id_version`, not `id_article`

A full paper can be revised repeatedly. If a review were keyed on
`(article, reviewer, phase)`, a reviewer could not review v2 after reviewing
v1 without a manual `round` counter. Keying on `id_version` makes each
revision a fresh review round for free, and gives an exact answer to "has
this reviewer reviewed the *current* file?" — which is what the announce gate
checks.

The `decision` CHECK allows all three values in one column; which are legal is
phase-dependent and enforced in the application layer (abstract:
`accept`/`reject`; full paper: `accept`/`revision`). A DB-level phase
constraint would require duplicating the phase onto this table, denormalizing
what `article_version.phase` already records.

### Retired columns

`articles.id_sc` and `articles.sc_notes` are dropped — they are superseded and
keeping them would create two sources of truth. Both are migrated into the new
tables first (see Migration).

## Status Machine Changes

The four internal `*_decided_*` values collapse into two. Decisions now live
per-reviewer in `article_review`; the article status only records *"every
assigned reviewer has submitted for the current version — the EIC may now
decide."*

| Current | Becomes |
|---|---|
| `abstract_decided_accept`, `abstract_decided_reject` | `abstract_review_complete` |
| `full_paper_decided_revision`, `full_paper_decided_accept` | `full_paper_review_complete` |

Resulting enum (9 values, down from 11):

```
submitted, assigned_to_sc, abstract_review_complete, abstract_accepted,
rejected, full_paper_submitted, full_paper_review_complete,
revision_needed, accepted
```

Flow:

```
submitted
  → assigned_to_sc                (EIC assigns >=1 reviewer)
  → abstract_review_complete      (set automatically when the last assigned
                                   reviewer submits for the abstract version)
  → abstract_accepted | rejected  (EIC announce, decision in the body)

full_paper_submitted
  → full_paper_review_complete    (set automatically when the last assigned
                                   reviewer submits for the current full-paper version)
  → accepted | revision_needed    (EIC announce, decision in the body)
      revision_needed → author resubmits → full_paper_submitted (loop, uncapped)
```

Author-visible mapping (`src/status.py`) adds the two new internal states as
`under_review` and drops the four retired ones. The author-visible set is
unchanged: `submitted`, `under_review`, `abstract_accepted`,
`revision_needed`, `accepted`, `rejected`.

## Conflict of Interest

On assignment, reject if the prospective reviewer's `institution_name` matches
the article author's, compared case-insensitively after trimming whitespace.

- If **either** institution is `NULL` or empty, allow the assignment. A missing
  value is absence of evidence, not evidence of conflict — blocking on it
  would make incomplete profiles unassignable.
- The EIC can override with `override_coi: true` in the assign request. The
  override is per-request and applies to that assignment call only.
- Rejection is **409** (a conflict with current state), listing which
  reviewers were refused and the shared institution.

Institution matching is a heuristic, not a guarantee — "Univ. Indonesia" and
"Universitas Indonesia" will not match. This is accepted: it catches the
common case cheaply, and the override plus the EIC's judgment covers the rest.
Author-declared COI (a reviewer recusing themselves) is **out of scope** here;
the user chose automatic screening only.

## API Changes

### New

- **`POST /articles/{id}/reviewers`** (EIC/admin) —
  `{"id_reviewers": [uuid, ...], "override_coi": false}`. Adds reviewers to the
  article; additive, so it can be called again to add more. Validates every id
  belongs to an `SC` user (400 otherwise) and runs COI screening (409).
  Re-assigning an already-assigned reviewer is idempotent, not an error.
  Sets status to `assigned_to_sc` if the article is still `submitted`.
  Emails each newly assigned reviewer.
- **`DELETE /articles/{id}/reviewers/{id_reviewer}`** (EIC/admin) — unassigns.
  Exists so an unresponsive reviewer cannot block the announce gate. Deleting
  an assignment does **not** delete reviews the reviewer already submitted
  (those stay as part of the record). If removing them means the remaining
  assigned reviewers have all now submitted, the status advances to
  `*_review_complete` as it would have naturally.
- **`GET /articles/{id}/reviews`** — returns reviews for the article. SC sees
  only their own (blind); EIC/admin see all; the author sees **none** (403) —
  reviewer feedback reaches authors through the EIC's announcement, not
  directly.

### Changed

- **`POST /articles/{id}/review`** (SC) — now writes an `article_review` row
  against the current version instead of mutating article status directly.
  Body stays phase-shaped (`{"accept": bool}` for abstract,
  `{"decision": ...}` for full paper — unchanged from the client's view).
  Requires the caller to be an assigned reviewer (403 otherwise). Rejects a
  second review of the same version (409 — use nothing; there is no update
  path in this scope). After writing, if every assigned reviewer has now
  reviewed the current version, the article advances to the matching
  `*_review_complete` status.
- **`POST /articles/{id}/announce`** (EIC/admin) — **now takes a body**:
  abstract phase `{"decision": "accept"|"reject"}`; full-paper phase
  `{"decision": "accept"|"revision", "id_recommended_journal": uuid}` (journal
  required when accepting). Requires status `*_review_complete` (409
  otherwise). **Breaking change** — this endpoint previously took no body.
- **`POST /articles/{id}/assign`** — kept as a single-reviewer shortcut that
  delegates to the same logic as `POST /reviewers`, so existing clients keep
  working. It gains `override_coi` as an optional field.
- **`ArticleOut`** — `id_sc` and `sc_notes` are replaced by
  `reviewers: list[uuid.UUID]` (the assigned reviewer ids).
  **Breaking change** for any client reading those two fields.

## Migration (002)

`db/schema.sql` is the source of truth for fresh databases; existing databases
need this migration. It must run in one transaction:

1. Create `article_reviewer` and `article_review`.
2. Backfill assignments: one `article_reviewer` row per article with a
   non-NULL `id_sc`.
3. Backfill reviews — reconstruct what the single SC decided from the old
   status, attaching each to the relevant `article_version` row:
   - `abstract_decided_accept` → decision `accept` on the abstract version
   - `abstract_decided_reject` → decision `reject` on the abstract version
   - `full_paper_decided_accept` → `accept` on the latest full-paper version
   - `full_paper_decided_revision` → `revision` on the latest full-paper version
   - Terminal states carry the decision implied by the outcome:
     `rejected` → `reject` on the abstract version; `accepted` → `accept` on
     the latest full-paper version; `abstract_accepted` → `accept` on the
     abstract version; `revision_needed` → `revision` on the latest
     full-paper version.
   - `submitted`, `assigned_to_sc`, `full_paper_submitted` → no review row
     (nothing was decided yet).
   - `sc_notes` carries over into the reconstructed row's `notes`.
   Articles with a NULL `id_sc` get no review row regardless of status — there
   is no reviewer to attribute one to.
4. Swap the enum to the 9-value set, mapping the four retired values to the
   two new ones.
5. Drop `articles.id_sc` and `articles.sc_notes`.

The backfill is a best-effort reconstruction: the old schema recorded only the
*net* decision, so nuances (who reviewed which revision) cannot be recovered
for pre-migration data. This is documented rather than papered over.

## Testing

- **Pure logic, unit-testable** (the pattern already established in
  `src/article_state.py`): the COI comparison (case/whitespace handling,
  NULL-permissive behavior), the phase→review-complete-status mapping, and the
  (phase, decision)→announced-status mapping including illegal combinations.
  The "all assigned reviewers done" gate is a DB query, verified in the manual
  smoke test rather than unit-tested.
- **Endpoint behavior**: verified by the existing manual smoke-test approach
  against the dockerized stack — this repo still has no integration-test
  harness, and building one remains a separate effort.
- **Migration**: verified by running it against a copy of the live dev
  database and checking row counts and reconstructed decisions, the same way
  migration 001 was verified.

## Out of Scope

- Reviewer self-declared COI / recusal.
- Reviewer deadlines, reminder emails, workload balancing.
- Editing or withdrawing a submitted review.
- Audit log and soft-delete — the remaining two Scope-B features, each getting
  its own spec. Audit log should come next, now that reviewer identity is
  settled.
