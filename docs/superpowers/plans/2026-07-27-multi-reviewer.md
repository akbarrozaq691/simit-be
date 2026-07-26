# Multi-Reviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-reviewer model (`articles.id_sc` + `articles.sc_notes`) with many reviewers per article, per-reviewer review records keyed to file versions, an automatic conflict-of-interest gate on assignment, and an EIC announce step that takes an explicit decision.

**Architecture:** Two new tables (`article_reviewer` for assignment, `article_review` for decisions). Reviews attach to `article_version.id_version`, so each revision round is naturally distinct. All new decision logic goes into the existing pure module `src/article_state.py` (no DB/HTTP deps, unit-testable) — the routers stay thin. The four internal `*_decided_*` statuses collapse to two `*_review_complete` statuses, since decisions now live per-reviewer rather than in the article's status.

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Pydantic v2, pytest.

## Global Constraints

- **Reviewer count is flexible** — no enforced minimum or maximum. Do not add one.
- **The EIC decides the outcome.** Reviewers advise. Do NOT implement majority/unanimity vote counting anywhere.
- **Announce requires every assigned reviewer to have submitted for the current version.** Unresponsive reviewers are handled by unassigning them, not by relaxing the gate.
- **COI:** reject assignment when reviewer and author `institution_name` match case-insensitively after trimming. If **either** is NULL/empty, ALLOW (absence of evidence is not evidence of conflict). Overridable with `override_coi: true`. Rejection is **409**.
- **Blind review:** an SC sees only their own reviews. EIC/admin see all. The author sees none (403).
- These are accepted breaking changes (the user approved them): `POST /announce` now requires a body; `ArticleOut.id_sc`/`sc_notes` are replaced by `reviewers: list[uuid]`.
- `POST /articles/{id}/assign` must keep working as a single-reviewer shortcut.
- Existing suite is 39 tests with pristine output (no warnings). Both properties must hold after every task.
- There is no integration-test harness in this repo. Endpoint behavior is verified by manual smoke test (Task 8). Do NOT write tests that merely assert mock behavior to fake coverage.

---

### Task 1: Pure logic — COI check, announce gate, phase-legal decisions

**Files:**
- Modify: `src/article_state.py`
- Modify: `tests/test_article_state.py`

**Interfaces:**
- Produces (consumed by Tasks 5-7):
  - `ABSTRACT_REVIEWABLE = {"assigned_to_sc"}` (unchanged)
  - `FULL_PAPER_REVIEWABLE = {"full_paper_submitted"}` (unchanged)
  - `ABSTRACT_ANNOUNCEABLE = {"abstract_review_complete"}`
  - `FULL_PAPER_ANNOUNCEABLE = {"full_paper_review_complete"}`
  - `institutions_conflict(a: str | None, b: str | None) -> bool`
  - `review_complete_status_for_phase(phase: str) -> str`
  - `announced_status_for(phase: str, decision: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_article_state.py`:

```python
# ---- COI ----


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("Universitas Indonesia", "Universitas Indonesia", True),
        ("universitas indonesia", "UNIVERSITAS INDONESIA", True),
        ("  Universitas Indonesia  ", "Universitas Indonesia", True),
        ("Universitas Indonesia", "Institut Teknologi Bandung", False),
        (None, "Universitas Indonesia", False),
        ("Universitas Indonesia", None, False),
        (None, None, False),
        ("", "Universitas Indonesia", False),
        ("   ", "Universitas Indonesia", False),
    ],
)
def test_institutions_conflict(a, b, expected):
    assert article_state.institutions_conflict(a, b) is expected


# ---- Review-complete status ----


def test_review_complete_status_abstract():
    assert article_state.review_complete_status_for_phase("abstract") == "abstract_review_complete"


def test_review_complete_status_full_paper():
    assert (
        article_state.review_complete_status_for_phase("full_paper")
        == "full_paper_review_complete"
    )


def test_review_complete_status_unknown_phase_raises():
    with pytest.raises(ValueError):
        article_state.review_complete_status_for_phase("nonsense")


# ---- Announce ----


@pytest.mark.parametrize(
    "phase,decision,expected",
    [
        ("abstract", "accept", "abstract_accepted"),
        ("abstract", "reject", "rejected"),
        ("full_paper", "accept", "accepted"),
        ("full_paper", "revision", "revision_needed"),
    ],
)
def test_announced_status_for(phase, decision, expected):
    assert article_state.announced_status_for(phase, decision) == expected


@pytest.mark.parametrize(
    "phase,decision",
    [
        ("abstract", "revision"),
        ("full_paper", "reject"),
        ("abstract", "nonsense"),
        ("nonsense", "accept"),
    ],
)
def test_announced_status_for_illegal_combination_raises(phase, decision):
    with pytest.raises(ValueError):
        article_state.announced_status_for(phase, decision)


def test_announceable_sets():
    assert article_state.ABSTRACT_ANNOUNCEABLE == {"abstract_review_complete"}
    assert article_state.FULL_PAPER_ANNOUNCEABLE == {"full_paper_review_complete"}
```

Also DELETE these now-obsolete tests from the same file (they test the four
retired `*_decided_*` statuses and the functions that produced them):
`test_decide_abstract_review_accept`, `test_decide_abstract_review_reject`,
`test_decide_full_paper_review_accept`, `test_decide_full_paper_review_revision`,
`test_decide_full_paper_review_unknown_decision_raises`,
`test_announce_result`, `test_announce_result_not_announceable_raises`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_article_state.py -v`
Expected: FAIL — `AttributeError: module 'src.article_state' has no attribute 'institutions_conflict'` (and similar for the other new names).

- [ ] **Step 3: Rewrite `src/article_state.py`**

Replace the entire file with:

```python
"""Pure status-transition and policy logic for the article review pipeline.

No DB or HTTP dependencies — routers call these and then persist. Kept
separate so the riskiest logic (state transitions, conflict-of-interest
screening) is unit-testable on its own.

Decisions live per-reviewer in `article_review`; an article's status only
records how far the pipeline has advanced, not what any reviewer decided.
"""

ABSTRACT_REVIEWABLE = {"assigned_to_sc"}
FULL_PAPER_REVIEWABLE = {"full_paper_submitted"}

ABSTRACT_ANNOUNCEABLE = {"abstract_review_complete"}
FULL_PAPER_ANNOUNCEABLE = {"full_paper_review_complete"}

_REVIEW_COMPLETE_STATUS = {
    "abstract": "abstract_review_complete",
    "full_paper": "full_paper_review_complete",
}

_ANNOUNCED_STATUS = {
    ("abstract", "accept"): "abstract_accepted",
    ("abstract", "reject"): "rejected",
    ("full_paper", "accept"): "accepted",
    ("full_paper", "revision"): "revision_needed",
}


def institutions_conflict(a: str | None, b: str | None) -> bool:
    """True when two institution names indicate a conflict of interest.

    Compared case-insensitively after trimming. A missing or blank value on
    either side returns False: absence of evidence is not evidence of a
    conflict, and blocking on it would make incomplete profiles unassignable.
    Exact-match only — "Univ. Indonesia" will not match "Universitas
    Indonesia". A deliberate heuristic; the EIC override covers the rest.
    """
    if not a or not b:
        return False
    left = a.strip().casefold()
    right = b.strip().casefold()
    if not left or not right:
        return False
    return left == right


def review_complete_status_for_phase(phase: str) -> str:
    if phase not in _REVIEW_COMPLETE_STATUS:
        raise ValueError(f"unknown phase: {phase!r}")
    return _REVIEW_COMPLETE_STATUS[phase]


def announced_status_for(phase: str, decision: str) -> str:
    key = (phase, decision)
    if key not in _ANNOUNCED_STATUS:
        raise ValueError(f"illegal announce combination: phase={phase!r} decision={decision!r}")
    return _ANNOUNCED_STATUS[key]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_article_state.py -v`
Expected: all pass (9 COI cases, 3 review-complete, 4+4 announce, 1 announceable-sets).

Run: `pytest -q`
Expected: all green except failures in files that import the deleted
`decide_abstract_review` / `announce_result` — those live in
`src/routers/articles/router.py`, which Tasks 5-7 rewrite. If `pytest -q`
reports collection errors from the router import, that is expected at this
point; note it in your report and continue. Do NOT edit the router in this task.

- [ ] **Step 5: Commit**

```bash
git add src/article_state.py tests/test_article_state.py
git commit -m "feat: add COI, phase-decision, and announce policy to article_state"
```

---

### Task 2: Database schema (`db/schema.sql`)

**Files:**
- Modify: `db/schema.sql`

**Interfaces:**
- Produces: `article_status` enum with 9 values; tables `article_reviewer` and
  `article_review`; `articles` without `id_sc` / `sc_notes`. Consumed by Task 3's
  ORM models.

- [ ] **Step 1: Replace the enum**

In `db/schema.sql`, replace the whole `CREATE TYPE article_status AS ENUM (...)`
block with:

```sql
CREATE TYPE article_status AS ENUM (
    'submitted',                    -- peserta submit abstrak
    'assigned_to_sc',                -- EIC assign >=1 SC reviewer
    'abstract_review_complete',      -- internal: semua reviewer selesai, nunggu keputusan EIC
    'abstract_accepted',             -- EIC announce: abstrak diterima, author submit full paper
    'rejected',                      -- pipeline selesai, tidak lolos (terminal)
    'full_paper_submitted',          -- full paper (baru atau revisi) masuk antrian reviewer
    'full_paper_review_complete',    -- internal: semua reviewer selesai, nunggu keputusan EIC
    'revision_needed',               -- EIC announce: author harus resubmit full paper
    'accepted'                       -- EIC announce: full paper diterima (terminal)
);
```

- [ ] **Step 2: Drop the retired columns from `articles`**

In the `CREATE TABLE articles (...)` block, delete these two lines:

```sql
    id_sc                    UUID REFERENCES users(id_user),            -- SC yang di-assign EIC
```

```sql
    sc_notes                TEXT,
```

Also delete the index on the dropped column:

```sql
CREATE INDEX idx_articles_id_sc ON articles(id_sc);
```

- [ ] **Step 3: Add the two new tables**

After the `article_version` table and its index, add:

```sql
-- === Reviewer assignment (many reviewers per article) ===

CREATE TABLE article_reviewer (
    id_assignment   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_article      UUID NOT NULL REFERENCES articles(id_article) ON DELETE CASCADE,
    id_reviewer     UUID NOT NULL REFERENCES users(id_user),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_article, id_reviewer)
);

CREATE INDEX idx_article_reviewer_article ON article_reviewer(id_article);
CREATE INDEX idx_article_reviewer_reviewer ON article_reviewer(id_reviewer);

-- === Per-reviewer review decisions ===
-- Keyed on the file version reviewed, so each revision round is distinct
-- without a manual round counter. Which decisions are legal is phase-dependent
-- (abstract: accept/reject, full_paper: accept/revision) and enforced in the
-- application layer, since the phase lives on article_version.

CREATE TABLE article_review (
    id_review       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_version      UUID NOT NULL REFERENCES article_version(id_version) ON DELETE CASCADE,
    id_reviewer     UUID NOT NULL REFERENCES users(id_user),
    decision        VARCHAR(20) NOT NULL CHECK (decision IN ('accept', 'reject', 'revision')),
    notes           TEXT,
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_version, id_reviewer)
);

CREATE INDEX idx_article_review_version ON article_review(id_version);
```

- [ ] **Step 4: Verify against a real Postgres**

If Docker is unavailable in your environment, say so in your report and skip to
Step 5 — the controller will run this.

```bash
docker run --rm -d --name schema-check-mr -e POSTGRES_DB=simit \
  -e POSTGRES_USER=simit -e POSTGRES_PASSWORD=changeme -p 15433:5432 \
  -v "$(pwd)/db/schema.sql:/docker-entrypoint-initdb.d/01_schema.sql" postgres:16-alpine
```

On Windows Git Bash, prefix docker commands with `MSYS_NO_PATHCONV=1` so the
volume path is not mangled.

Wait for readiness, then:
- `docker logs schema-check-mr 2>&1 | grep -i error` → expect no output
- `docker exec schema-check-mr psql -U simit -d simit -c "\d article_reviewer"` → 4 columns, PK, UNIQUE, 2 indexes, 2 FKs
- `docker exec schema-check-mr psql -U simit -d simit -c "\d article_review"` → 6 columns, PK, UNIQUE, CHECK on decision, 2 FKs
- `docker exec schema-check-mr psql -U simit -d simit -c "SELECT unnest(enum_range(NULL::article_status));"` → exactly the 9 values above
- `docker exec schema-check-mr psql -U simit -d simit -c "\d articles"` → confirm `id_sc` and `sc_notes` are GONE

Then `docker stop schema-check-mr`.

- [ ] **Step 5: Commit**

```bash
git add db/schema.sql
git commit -m "feat: schema for multi-reviewer assignment and per-reviewer reviews"
```

---

### Task 3: ORM models

**Files:**
- Modify: `src/models.py`

**Interfaces:**
- Produces: `ArticleReviewer` (`id_assignment`, `id_article`, `id_reviewer`,
  `assigned_at`) and `ArticleReview` (`id_review`, `id_version`, `id_reviewer`,
  `decision`, `notes`, `reviewed_at`); `Article` without `id_sc` / `sc_notes`;
  9-value `ArticleStatusEnum`. Consumed by Task 4's repository.

- [ ] **Step 1: Update `ArticleStatusEnum`**

Replace the `ArticleStatusEnum` definition with:

```python
ArticleStatusEnum = PGEnum(
    "submitted",
    "assigned_to_sc",
    "abstract_review_complete",
    "abstract_accepted",
    "rejected",
    "full_paper_submitted",
    "full_paper_review_complete",
    "revision_needed",
    "accepted",
    name="article_status",
    create_type=False,  # already created by db/schema.sql
)
```

- [ ] **Step 2: Remove the retired columns from `Article`**

In the `Article` class, delete these two attribute definitions:

```python
    id_sc: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id_user"))
```

```python
    sc_notes: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 3: Add the two new models**

After the `ArticleVersion` class, add:

```python
class ArticleReviewer(Base):
    __tablename__ = "article_reviewer"
    __table_args__ = (UniqueConstraint("id_article", "id_reviewer"),)

    id_assignment: Mapped[uuid.UUID] = _uuid_pk()
    id_article: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id_article", ondelete="CASCADE"), nullable=False
    )
    id_reviewer: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id_user"), nullable=False
    )
    assigned_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class ArticleReview(Base):
    __tablename__ = "article_review"
    __table_args__ = (UniqueConstraint("id_version", "id_reviewer"),)

    id_review: Mapped[uuid.UUID] = _uuid_pk()
    id_version: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_version.id_version", ondelete="CASCADE"), nullable=False
    )
    id_reviewer: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id_user"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
```

- [ ] **Step 4: Verify the module imports**

Run: `python -c "from src import models; print(models.ArticleReviewer.__tablename__, models.ArticleReview.__tablename__)"`
Expected: prints `article_reviewer article_review`.

Run: `python -c "from src.models import Article; print(hasattr(Article, 'id_sc'), hasattr(Article, 'sc_notes'))"`
Expected: prints `False False`.

- [ ] **Step 5: Commit**

```bash
git add src/models.py
git commit -m "feat: ORM models for reviewer assignment and per-reviewer reviews"
```

---

### Task 4: Repository — assignment, review, and gate queries

**Files:**
- Modify: `src/routers/articles/repository.py`

**Interfaces:**
- Consumes: `ArticleReviewer`, `ArticleReview`, `ArticleVersion`, `User` models
- Produces (consumed by Tasks 5-7):
  - `async def list_reviewer_ids(session, id_article) -> list[uuid.UUID]`
  - `async def is_assigned(session, id_article, id_reviewer) -> bool`
  - `async def add_reviewer(session, id_article, id_reviewer) -> bool` (False if already assigned)
  - `async def remove_reviewer(session, id_article, id_reviewer) -> bool`
  - `async def latest_version(session, id_article, phase) -> ArticleVersion | None`
  - `async def add_review(session, *, id_version, id_reviewer, decision, notes) -> ArticleReview`
  - `async def has_reviewed(session, id_version, id_reviewer) -> bool`
  - `async def all_assigned_have_reviewed(session, id_article, id_version) -> bool`
  - `async def list_reviews_for_article(session, id_article, only_reviewer=None) -> list[ArticleReview]`
  - `async def get_user_institution(session, id_user) -> str | None`

- [ ] **Step 1: Update imports**

In `src/routers/articles/repository.py`, change the models import to:

```python
from ...models import Article, ArticleReview, ArticleReviewer, ArticleVersion, User
```

- [ ] **Step 2: Update `to_article_out` for the new shape**

`ArticleOut` no longer has `id_sc`/`sc_notes` and gains `reviewers`. Replace
`to_article_out` with:

```python
def to_article_out(article: Article, viewer_role: str, reviewers: list[uuid.UUID]) -> ArticleOut:
    out = ArticleOut.model_validate(article)
    out.reviewers = reviewers
    if viewer_role == "author":
        out.status = AUTHOR_STATUS_MAP.get(out.status, out.status)
    return out
```

Callers must now pass the reviewer list — Tasks 5-7 update them.

- [ ] **Step 3: Add the new helpers**

Append to `src/routers/articles/repository.py`:

```python
# ---- Reviewer assignment ----


async def list_reviewer_ids(session: AsyncSession, id_article: uuid.UUID) -> list[uuid.UUID]:
    result = await session.execute(
        select(ArticleReviewer.id_reviewer)
        .where(ArticleReviewer.id_article == id_article)
        .order_by(ArticleReviewer.assigned_at)
    )
    return list(result.scalars().all())


async def is_assigned(
    session: AsyncSession, id_article: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    result = await session.execute(
        select(ArticleReviewer.id_assignment).where(
            ArticleReviewer.id_article == id_article,
            ArticleReviewer.id_reviewer == id_reviewer,
        )
    )
    return result.scalar_one_or_none() is not None


async def add_reviewer(
    session: AsyncSession, id_article: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    """Assigns a reviewer. Returns False if they were already assigned
    (idempotent by design — re-assigning is not an error)."""
    if await is_assigned(session, id_article, id_reviewer):
        return False
    session.add(ArticleReviewer(id_article=id_article, id_reviewer=id_reviewer))
    await session.flush()
    return True


async def remove_reviewer(
    session: AsyncSession, id_article: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    result = await session.execute(
        select(ArticleReviewer).where(
            ArticleReviewer.id_article == id_article,
            ArticleReviewer.id_reviewer == id_reviewer,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        return False
    await session.delete(assignment)
    await session.flush()
    return True


# ---- Reviews ----


async def latest_version(
    session: AsyncSession, id_article: uuid.UUID, phase: str
) -> ArticleVersion | None:
    result = await session.execute(
        select(ArticleVersion)
        .where(ArticleVersion.id_article == id_article, ArticleVersion.phase == phase)
        .order_by(ArticleVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def has_reviewed(
    session: AsyncSession, id_version: uuid.UUID, id_reviewer: uuid.UUID
) -> bool:
    result = await session.execute(
        select(ArticleReview.id_review).where(
            ArticleReview.id_version == id_version,
            ArticleReview.id_reviewer == id_reviewer,
        )
    )
    return result.scalar_one_or_none() is not None


async def add_review(
    session: AsyncSession,
    *,
    id_version: uuid.UUID,
    id_reviewer: uuid.UUID,
    decision: str,
    notes: str | None,
) -> ArticleReview:
    review = ArticleReview(
        id_version=id_version,
        id_reviewer=id_reviewer,
        decision=decision,
        notes=notes,
    )
    session.add(review)
    await session.flush()
    return review


async def all_assigned_have_reviewed(
    session: AsyncSession, id_article: uuid.UUID, id_version: uuid.UUID
) -> bool:
    """True when every currently-assigned reviewer has a review on this version.

    False when no reviewers are assigned — an article with no reviewers has not
    completed review, it has not started it.
    """
    assigned = await list_reviewer_ids(session, id_article)
    if not assigned:
        return False
    result = await session.execute(
        select(ArticleReview.id_reviewer).where(ArticleReview.id_version == id_version)
    )
    reviewed = set(result.scalars().all())
    return all(r in reviewed for r in assigned)


async def list_reviews_for_article(
    session: AsyncSession,
    id_article: uuid.UUID,
    only_reviewer: uuid.UUID | None = None,
) -> list[ArticleReview]:
    """All reviews across every version of an article, newest version first.
    `only_reviewer` narrows to one reviewer's own reviews (blind-review view)."""
    stmt = (
        select(ArticleReview)
        .join(ArticleVersion, ArticleReview.id_version == ArticleVersion.id_version)
        .where(ArticleVersion.id_article == id_article)
        .order_by(ArticleVersion.phase, ArticleVersion.version_number.desc())
    )
    if only_reviewer is not None:
        stmt = stmt.where(ArticleReview.id_reviewer == only_reviewer)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_institution(session: AsyncSession, id_user: uuid.UUID) -> str | None:
    user = await session.get(User, id_user)
    return user.institution_name if user else None
```

- [ ] **Step 4: Update `list_articles_for` for the new reviewer model**

The SC branch currently filters on `Article.id_sc`, a column that no longer
exists. Replace the function with:

```python
async def list_articles_for(session: AsyncSession, role: str, id_user: str) -> list[Article]:
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
    stmt = stmt.order_by(Article.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())
```

- [ ] **Step 5: Verify the module imports**

Run: `python -c "from src.routers.articles import repository as r; print(r.all_assigned_have_reviewed, r.list_reviewer_ids)"`
Expected: prints both function objects.

- [ ] **Step 6: Commit**

```bash
git add src/routers/articles/repository.py
git commit -m "feat: repository helpers for reviewer assignment and reviews"
```

---

### Task 5: Schemas

**Files:**
- Modify: `src/schemas.py`

**Interfaces:**
- Produces (consumed by Tasks 6-7): `ArticleOut` with `reviewers: list[uuid.UUID]`
  and without `id_sc`/`sc_notes`; `AssignReviewersRequest`;
  `ArticleAssignRequest` gaining `override_coi`; `AbstractAnnounceRequest`;
  `FullPaperAnnounceRequest`; `ArticleReviewOut`; updated `ArticleStatus` enum.

- [ ] **Step 1: Update the `ArticleStatus` enum**

Replace it with the 9 current values:

```python
class ArticleStatus(str, Enum):
    submitted = "submitted"
    assigned_to_sc = "assigned_to_sc"
    abstract_review_complete = "abstract_review_complete"
    abstract_accepted = "abstract_accepted"
    rejected = "rejected"
    full_paper_submitted = "full_paper_submitted"
    full_paper_review_complete = "full_paper_review_complete"
    revision_needed = "revision_needed"
    accepted = "accepted"
```

- [ ] **Step 2: Update `ArticleOut`**

In `ArticleOut`, delete these two fields:

```python
    id_sc: uuid.UUID | None
```

```python
    sc_notes: str | None
```

and add, after `id_recommended_journal`:

```python
    reviewers: list[uuid.UUID] = []
```

- [ ] **Step 3: Add `override_coi` to the existing single-reviewer request**

Replace `ArticleAssignRequest` with:

```python
class ArticleAssignRequest(BaseModel):
    id_sc: uuid.UUID
    override_coi: bool = False
```

(The field stays named `id_sc` — this is the backward-compatible
single-reviewer shortcut and existing clients send that key.)

- [ ] **Step 4: Add the new schemas**

In the `# ---- Articles ----` section, after `ArticleAssignRequest`, add:

```python
class AssignReviewersRequest(BaseModel):
    id_reviewers: list[uuid.UUID] = Field(min_length=1)
    override_coi: bool = False


class AbstractAnnounceRequest(BaseModel):
    decision: Literal["accept", "reject"]


class FullPaperAnnounceRequest(BaseModel):
    decision: Literal["accept", "revision"]
    id_recommended_journal: uuid.UUID | None = None

    @model_validator(mode="after")
    def _journal_required_on_accept(self) -> "FullPaperAnnounceRequest":
        if self.decision == "accept" and self.id_recommended_journal is None:
            raise ValueError("id_recommended_journal is required when decision is 'accept'")
        return self


class ArticleReviewOut(ORMModel):
    id_review: uuid.UUID
    id_version: uuid.UUID
    id_reviewer: uuid.UUID
    decision: str
    notes: str | None
    reviewed_at: dt.datetime
```

- [ ] **Step 5: Verify**

Run:
```bash
python -c "
from src.schemas import AssignReviewersRequest, FullPaperAnnounceRequest, ArticleOut
import pydantic
AssignReviewersRequest(id_reviewers=['00000000-0000-0000-0000-000000000001'])
try:
    AssignReviewersRequest(id_reviewers=[])
except pydantic.ValidationError:
    print('empty reviewer list rejected: ok')
try:
    FullPaperAnnounceRequest(decision='accept')
except pydantic.ValidationError:
    print('accept without journal rejected: ok')
print('id_sc' in ArticleOut.model_fields, 'reviewers' in ArticleOut.model_fields)
"
```
Expected: prints `empty reviewer list rejected: ok`, `accept without journal rejected: ok`, then `False True`.

- [ ] **Step 6: Commit**

```bash
git add src/schemas.py
git commit -m "feat: schemas for multi-reviewer assignment and EIC announce decisions"
```

---

### Task 6: Router — reviewer assignment endpoints + COI

**Files:**
- Modify: `src/routers/articles/router.py`

**Interfaces:**
- Consumes: Task 1's `article_state.institutions_conflict`, Task 4's repository
  helpers, Task 5's `AssignReviewersRequest` / `ArticleAssignRequest`
- Produces: `POST /articles/{id}/reviewers`, `DELETE /articles/{id}/reviewers/{id_reviewer}`,
  and a rewritten `assign_article` delegating to the same helper.

- [ ] **Step 1: Update imports**

Add to the schema import block: `AssignReviewersRequest`. The full import list
this file needs from `...schemas` after this task:

```python
from ...schemas import (
    AbstractReviewRequest,
    ArticleAssignRequest,
    ArticleCreate,
    ArticleFullPaperRequest,
    ArticleOut,
    ArticleUpdate,
    ArticleVersionOut,
    AssignReviewersRequest,
    FullPaperReviewRequest,
    UploadResponse,
    UserCtx,
)
```

- [ ] **Step 2: Add the phase helper**

`_current_review_phase` is used by the endpoints below and again in Task 7, so
define it first. Add near `_not_found`:

```python
def _current_review_phase(article_status: str) -> str | None:
    """Which phase is actively under review, or None if the article is not in
    a reviewable state."""
    if article_status in article_state.ABSTRACT_REVIEWABLE:
        return "abstract"
    if article_status in article_state.FULL_PAPER_REVIEWABLE:
        return "full_paper"
    return None
```

- [ ] **Step 3: Add the shared assignment helper**

Add above `assign_article`:

```python
async def _assign_reviewers(
    session,
    article,
    id_reviewers: list[uuid.UUID],
    override_coi: bool,
    background_tasks: BackgroundTasks,
) -> None:
    """Validates and assigns reviewers, emailing each newly assigned one.

    Shared by POST /reviewers and the single-reviewer POST /assign shortcut.
    Raises 400 for non-SC users and 409 for a COI the caller did not override.
    """
    author_institution = await repo.get_user_institution(session, article.id_user)

    for id_reviewer in id_reviewers:
        role = await repo.get_user_role(session, id_reviewer)
        if role != "SC":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{id_reviewer} does not belong to a SC user"
            )
        if not override_coi:
            reviewer_institution = await repo.get_user_institution(session, id_reviewer)
            if article_state.institutions_conflict(author_institution, reviewer_institution):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"conflict of interest: reviewer {id_reviewer} shares the author's "
                    f"institution ({reviewer_institution}); pass override_coi=true to assign anyway",
                )

    newly_assigned = []
    for id_reviewer in id_reviewers:
        if await repo.add_reviewer(session, article.id_article, id_reviewer):
            newly_assigned.append(id_reviewer)

    if article.status == "submitted":
        article.status = "assigned_to_sc"
    await session.flush()

    for id_reviewer in newly_assigned:
        email = await repo.get_user_email(session, id_reviewer)
        background_tasks.add_task(
            emailer.send,
            email,
            "New article assigned for review",
            f"Article '{article.title}' has been assigned to you for review.",
        )
```

Note the two-pass structure: every reviewer is validated **before** any is
assigned, so a bad id or an unoverridden COI late in the list does not leave
half the batch applied.

- [ ] **Step 4: Replace `assign_article` with the shortcut form**

```python
@router.post(
    "/{id_article}/assign",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def assign_article(
    id_article: uuid.UUID,
    body: ArticleAssignRequest,
    background_tasks: BackgroundTasks,
    session=Depends(get_session),
) -> ArticleOut:
    """Single-reviewer shortcut, kept for backward compatibility.
    Prefer POST /articles/{id}/reviewers for assigning several at once."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if article.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot assign in status {article.status}")

    await _assign_reviewers(session, article, [body.id_sc], body.override_coi, background_tasks)
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)
```

- [ ] **Step 5: Add the multi-reviewer endpoints**

Append to the file:

```python
@router.post(
    "/{id_article}/reviewers",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def assign_reviewers(
    id_article: uuid.UUID,
    body: AssignReviewersRequest,
    background_tasks: BackgroundTasks,
    session=Depends(get_session),
) -> ArticleOut:
    """EIC assigns one or more SC reviewers. Additive: call again to add more.
    Re-assigning an already-assigned reviewer is a no-op, not an error."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if article.status not in ("submitted", "assigned_to_sc", "full_paper_submitted"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot assign reviewers in status {article.status}"
        )

    await _assign_reviewers(
        session, article, body.id_reviewers, body.override_coi, background_tasks
    )
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)


@router.delete(
    "/{id_article}/reviewers/{id_reviewer}",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def unassign_reviewer(
    id_article: uuid.UUID,
    id_reviewer: uuid.UUID,
    session=Depends(get_session),
) -> ArticleOut:
    """Unassigns a reviewer — the escape hatch for an unresponsive reviewer
    blocking the announce gate. Any review they already submitted is kept.

    If the remaining assigned reviewers have all submitted for the current
    version, the article advances to *_review_complete, exactly as it would
    have when the last review landed.
    """
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()
    if not await repo.remove_reviewer(session, id_article, id_reviewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reviewer not assigned to this article")

    phase = _current_review_phase(article.status)
    if phase is not None:
        version = await repo.latest_version(session, id_article, phase)
        if version is not None and await repo.all_assigned_have_reviewed(
            session, id_article, version.id_version
        ):
            article.status = article_state.review_complete_status_for_phase(phase)
    await session.flush()

    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)
```

- [ ] **Step 6: Verify (partial — the file is mid-refactor)**

`review_article` and `announce_article` still reference removed functions at
this point; Task 7 fixes them. Do NOT try to fix them here.

Run: `grep -n "id_sc\|sc_notes" src/routers/articles/router.py`
Expected: the only remaining hit is `body.id_sc` inside `assign_article`
(the backward-compatible request field). Anything else is a leftover to fix.

- [ ] **Step 7: Commit**

```bash
git add src/routers/articles/router.py
git commit -m "feat: multi-reviewer assignment endpoints with COI screening"
```

---

### Task 7: Router — review recording, announce decision, reviews listing

**Files:**
- Modify: `src/routers/articles/router.py`

**Interfaces:**
- Consumes: Task 1's `article_state` policy functions, Task 4's repository
  helpers, Task 5's `AbstractAnnounceRequest` / `FullPaperAnnounceRequest` /
  `ArticleReviewOut`
- Produces: rewritten `review_article` and `announce_article`, new
  `GET /articles/{id}/reviews`. After this task the module imports cleanly again.

- [ ] **Step 1: Update imports**

Add `AbstractAnnounceRequest`, `FullPaperAnnounceRequest`, `ArticleReviewOut`
to the `...schemas` import block.

- [ ] **Step 2: Rewrite `review_article`**

Replace it entirely:

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
    """An assigned SC records their own review of the current file version.

    Body shape must match the phase: `{"accept": bool}` for an abstract,
    `{"decision": "accept"|"revision"}` for a full paper. When the last
    assigned reviewer submits, the article advances to *_review_complete and
    the EIC can announce.
    """
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    id_reviewer = uuid.UUID(user.id_user)
    if not await repo.is_assigned(session, id_article, id_reviewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not an assigned reviewer")

    phase = _current_review_phase(article.status)
    if phase is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot review in status {article.status}")

    if phase == "abstract":
        if not isinstance(body, AbstractReviewRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"article is in abstract review (status {article.status}); "
                'expected an abstract review body: {"accept": bool}',
            )
        decision = "accept" if body.accept else "reject"
    else:
        if not isinstance(body, FullPaperReviewRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"article is in full-paper review (status {article.status}); "
                'expected a full-paper review body: {"decision": "accept"|"revision"}',
            )
        decision = body.decision

    version = await repo.latest_version(session, id_article, phase)
    if version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"article has no {phase} version to review"
        )
    if await repo.has_reviewed(session, version.id_version, id_reviewer):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "you have already reviewed this version"
        )

    await repo.add_review(
        session,
        id_version=version.id_version,
        id_reviewer=id_reviewer,
        decision=decision,
        notes=body.notes,
    )

    if await repo.all_assigned_have_reviewed(session, id_article, version.id_version):
        article.status = article_state.review_complete_status_for_phase(phase)
    await session.flush()

    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "SC", reviewers)
```

Note `id_recommended_journal` is no longer read from the review body — the
journal is now the EIC's call at announce time. `FullPaperReviewRequest` keeps
the field for backward compatibility; it is simply ignored here.

- [ ] **Step 3: Rewrite `announce_article`**

Replace it entirely:

```python
@router.post(
    "/{id_article}/announce",
    response_model=ArticleOut,
    dependencies=[Depends(require_roles("admin", "EIC"))],
)
async def announce_article(
    id_article: uuid.UUID,
    body: AbstractAnnounceRequest | FullPaperAnnounceRequest,
    background_tasks: BackgroundTasks,
    session=Depends(get_session),
) -> ArticleOut:
    """EIC weighs the reviews and announces the outcome to the author.

    Abstract phase: `{"decision": "accept"|"reject"}`.
    Full-paper phase: `{"decision": "accept"|"revision"}` plus
    `id_recommended_journal` when accepting.

    Requires every assigned reviewer to have submitted (status *_review_complete).
    """
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    if article.status in article_state.ABSTRACT_ANNOUNCEABLE:
        phase = "abstract"
        if not isinstance(body, AbstractAnnounceRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                'article is in abstract review; expected {"decision": "accept"|"reject"}',
            )
        decision = body.decision
    elif article.status in article_state.FULL_PAPER_ANNOUNCEABLE:
        phase = "full_paper"
        if not isinstance(body, FullPaperAnnounceRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                'article is in full-paper review; expected '
                '{"decision": "accept"|"revision", "id_recommended_journal": uuid}',
            )
        decision = body.decision
        if body.id_recommended_journal is not None:
            article.id_recommended_journal = body.id_recommended_journal
    else:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot announce in status {article.status}"
        )

    article.status = article_state.announced_status_for(phase, decision)
    await session.flush()

    author_email = await repo.get_user_email(session, article.id_user)
    background_tasks.add_task(
        emailer.send,
        author_email,
        f"Update on your article '{article.title}'",
        f"Your article status is now: {article.status}.",
    )
    reviewers = await repo.list_reviewer_ids(session, id_article)
    return repo.to_article_out(article, "EIC", reviewers)
```

- [ ] **Step 4: Add the reviews-listing endpoint**

Append to the file:

```python
@router.get("/{id_article}/reviews", response_model=list[ArticleReviewOut])
async def list_article_reviews(
    id_article: uuid.UUID,
    user: UserCtx = Depends(get_current_user),
    session=Depends(get_session),
) -> list[ArticleReviewOut]:
    """Blind review: an SC sees only their own reviews, EIC/admin see all.
    Authors see none — reviewer feedback reaches them via the EIC's
    announcement, not directly."""
    article = await repo.get_article(session, id_article)
    if article is None:
        raise _not_found()

    if user.role == "author":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "authors cannot read reviews directly")

    only_reviewer = uuid.UUID(user.id_user) if user.role == "SC" else None
    if only_reviewer is not None and not await repo.is_assigned(
        session, id_article, only_reviewer
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not an assigned reviewer")

    reviews = await repo.list_reviews_for_article(session, id_article, only_reviewer)
    return [ArticleReviewOut.model_validate(r) for r in reviews]
```

- [ ] **Step 5: Fix the remaining `to_article_out` callers**

Every call now needs the reviewer list. Find them:

Run: `grep -n "to_article_out" src/routers/articles/router.py`

For each remaining call site (`list_articles`, `create_article`, `get_article`,
`update_article`, `submit_full_paper`, `submit_revision`), pass the reviewers.
In `list_articles`, fetch per article:

```python
@router.get("", response_model=list[ArticleOut])
async def list_articles(
    user: UserCtx = Depends(get_current_user), session=Depends(get_session)
) -> list[ArticleOut]:
    articles = await repo.list_articles_for(session, user.role, user.id_user)
    out = []
    for a in articles:
        reviewers = await repo.list_reviewer_ids(session, a.id_article)
        out.append(repo.to_article_out(a, user.role, reviewers))
    return out
```

For the single-article handlers, add before the return:

```python
    reviewers = await repo.list_reviewer_ids(session, id_article)
```

and pass it as the third argument. In `create_article` (which has no
`id_article` variable yet) use `article.id_article`.

- [ ] **Step 6: Verify the module and app import cleanly**

Run: `python -c "from src.main import app; print('ok')"`
Expected: prints `ok`.

Run: `grep -n "decide_abstract_review\|decide_full_paper_review\|announce_result\|sc_notes" src/routers/articles/router.py`
Expected: no output (all retired references gone).

Run: `pytest -q`
Expected: all green, no warnings. Note `tests/test_review_schemas.py` has an
OpenAPI assertion for `/review` — the union body is unchanged there, so it
should still pass.

- [ ] **Step 7: Commit**

```bash
git add src/routers/articles/router.py
git commit -m "feat: per-reviewer review records and EIC announce decision"
```

---

### Task 8: Author status map + migration 002

**Files:**
- Modify: `src/status.py`
- Modify: `tests/test_status.py`
- Create: `db/migrations/002_multi_reviewer.sql`

**Interfaces:**
- Produces: `AUTHOR_STATUS_MAP` covering the 9 current statuses; a migration
  upgrading an existing database.

- [ ] **Step 1: Update the failing test first**

In `tests/test_status.py`, replace `ALL_STATUSES` with:

```python
ALL_STATUSES = [
    "submitted",
    "assigned_to_sc",
    "abstract_review_complete",
    "abstract_accepted",
    "rejected",
    "full_paper_submitted",
    "full_paper_review_complete",
    "revision_needed",
    "accepted",
]
```

and replace the internal-states test with:

```python
def test_internal_states_hidden_as_under_review():
    for internal in (
        "assigned_to_sc",
        "abstract_review_complete",
        "full_paper_submitted",
        "full_paper_review_complete",
    ):
        assert AUTHOR_STATUS_MAP[internal] == "under_review"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_status.py -v`
Expected: FAIL — the new status names are not in `AUTHOR_STATUS_MAP`.

- [ ] **Step 3: Update `src/status.py`**

Replace `AUTHOR_STATUS_MAP` with:

```python
AUTHOR_STATUS_MAP = {
    "submitted": "submitted",
    "assigned_to_sc": "under_review",
    "abstract_review_complete": "under_review",
    "abstract_accepted": "abstract_accepted",
    "rejected": "rejected",
    "full_paper_submitted": "under_review",
    "full_paper_review_complete": "under_review",
    "revision_needed": "revision_needed",
    "accepted": "accepted",
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest -q`
Expected: all green, no warnings.

- [ ] **Step 5: Write migration 002**

Create `db/migrations/002_multi_reviewer.sql`:

```sql
-- Migration 002: multi-reviewer assignment + per-reviewer reviews
--
-- Upgrades a database already migrated by 001 (11-value article_status,
-- article_version present, single articles.id_sc/sc_notes) to the
-- multi-reviewer shape. Fresh databases do not need this — db/schema.sql
-- already creates the current shape.
--
-- The old schema stored only the NET decision of a single reviewer, so the
-- backfill is a best-effort reconstruction: it recovers what that one reviewer
-- decided, attributed to the relevant file version. Which revision round a
-- decision belonged to cannot be recovered for pre-migration data.

BEGIN;

-- --- 1. New tables ---------------------------------------------------------

CREATE TABLE article_reviewer (
    id_assignment   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_article      UUID NOT NULL REFERENCES articles(id_article) ON DELETE CASCADE,
    id_reviewer     UUID NOT NULL REFERENCES users(id_user),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_article, id_reviewer)
);

CREATE INDEX idx_article_reviewer_article ON article_reviewer(id_article);
CREATE INDEX idx_article_reviewer_reviewer ON article_reviewer(id_reviewer);

CREATE TABLE article_review (
    id_review       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_version      UUID NOT NULL REFERENCES article_version(id_version) ON DELETE CASCADE,
    id_reviewer     UUID NOT NULL REFERENCES users(id_user),
    decision        VARCHAR(20) NOT NULL CHECK (decision IN ('accept', 'reject', 'revision')),
    notes           TEXT,
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_version, id_reviewer)
);

CREATE INDEX idx_article_review_version ON article_review(id_version);

-- --- 2. Backfill assignments from the single id_sc -------------------------

INSERT INTO article_reviewer (id_article, id_reviewer, assigned_at)
SELECT id_article, id_sc, created_at
FROM articles
WHERE id_sc IS NOT NULL;

-- --- 3. Backfill reviews from the old status ------------------------------
-- Abstract-phase decisions attach to the article's abstract version.

INSERT INTO article_review (id_version, id_reviewer, decision, notes, reviewed_at)
SELECT v.id_version,
       a.id_sc,
       CASE a.status::text
           WHEN 'abstract_decided_accept' THEN 'accept'
           WHEN 'abstract_accepted'       THEN 'accept'
           WHEN 'abstract_decided_reject' THEN 'reject'
           WHEN 'rejected'                THEN 'reject'
       END,
       a.sc_notes,
       a.updated_at
FROM articles a
JOIN article_version v
  ON v.id_article = a.id_article AND v.phase = 'abstract'
WHERE a.id_sc IS NOT NULL
  AND a.status::text IN ('abstract_decided_accept', 'abstract_accepted',
                         'abstract_decided_reject', 'rejected');

-- Full-paper decisions attach to the LATEST full-paper version.

INSERT INTO article_review (id_version, id_reviewer, decision, notes, reviewed_at)
SELECT v.id_version,
       a.id_sc,
       CASE a.status::text
           WHEN 'full_paper_decided_accept'   THEN 'accept'
           WHEN 'accepted'                    THEN 'accept'
           WHEN 'full_paper_decided_revision' THEN 'revision'
           WHEN 'revision_needed'             THEN 'revision'
       END,
       a.sc_notes,
       a.updated_at
FROM articles a
JOIN (
    SELECT id_article, id_version,
           row_number() OVER (PARTITION BY id_article ORDER BY version_number DESC) AS rn
    FROM article_version
    WHERE phase = 'full_paper'
) v ON v.id_article = a.id_article AND v.rn = 1
WHERE a.id_sc IS NOT NULL
  AND a.status::text IN ('full_paper_decided_accept', 'accepted',
                         'full_paper_decided_revision', 'revision_needed');

-- --- 4. Swap the enum to the 9-value set ----------------------------------

CREATE TYPE article_status_new AS ENUM (
    'submitted',
    'assigned_to_sc',
    'abstract_review_complete',
    'abstract_accepted',
    'rejected',
    'full_paper_submitted',
    'full_paper_review_complete',
    'revision_needed',
    'accepted'
);

ALTER TABLE articles ALTER COLUMN status DROP DEFAULT;

ALTER TABLE articles
    ALTER COLUMN status TYPE article_status_new
    USING (
        CASE status::text
            WHEN 'abstract_decided_accept'      THEN 'abstract_review_complete'
            WHEN 'abstract_decided_reject'      THEN 'abstract_review_complete'
            WHEN 'full_paper_decided_accept'    THEN 'full_paper_review_complete'
            WHEN 'full_paper_decided_revision'  THEN 'full_paper_review_complete'
            ELSE status::text
        END
    )::article_status_new;

DROP TYPE article_status;
ALTER TYPE article_status_new RENAME TO article_status;

ALTER TABLE articles ALTER COLUMN status SET DEFAULT 'submitted';

-- --- 5. Drop the superseded columns ---------------------------------------

DROP INDEX IF EXISTS idx_articles_id_sc;
ALTER TABLE articles DROP COLUMN id_sc;
ALTER TABLE articles DROP COLUMN sc_notes;

COMMIT;
```

- [ ] **Step 6: Commit**

```bash
git add src/status.py tests/test_status.py db/migrations/002_multi_reviewer.sql
git commit -m "feat: author status map for new statuses + migration 002"
```

---

### Task 9: Manual end-to-end verification

**Files:** none (verification only)

This repo has no integration-test harness, so endpoint behavior is verified
manually against the dockerized stack, as with the previous two features.

If Docker is unavailable in your environment, report that and stop — the
controller will run this task.

- [ ] **Step 1: Back up the dev database before migrating**

```bash
mkdir -p db/backups
docker exec be-postgres-1 pg_dump -U simit -d simit > db/backups/pre-002-$(date +%Y%m%d).sql
```

Confirm the file is non-empty before continuing. (On Windows Git Bash, prefix
`docker exec` with `MSYS_NO_PATHCONV=1`.)

- [ ] **Step 2: Run migration 002 and verify the data**

```bash
docker exec -i be-postgres-1 psql -U simit -d simit -v ON_ERROR_STOP=1 < db/migrations/002_multi_reviewer.sql
```

Expect the statement log to end with `COMMIT`. Then verify:
- `SELECT unnest(enum_range(NULL::article_status));` → exactly 9 values
- `SELECT count(*) FROM article_reviewer;` → one row per pre-migration article that had an `id_sc`
- `SELECT decision, count(*) FROM article_review GROUP BY decision;` → decisions reconstructed from the old statuses
- `\d articles` → `id_sc` and `sc_notes` gone
- `SELECT count(*) FROM articles;` → unchanged from before the migration

- [ ] **Step 3: Rebuild the app and confirm it reads migrated data**

```bash
docker-compose up -d --build app
curl -s http://localhost:8888/health
```
Then list articles as admin and confirm each carries a `reviewers` array and a
valid new-enum status.

- [ ] **Step 4: Walk the multi-reviewer flow**

As admin, create two SC users at **different** institutions and one author.
Then:

1. Author creates an article → `submitted`
2. EIC `POST /articles/{id}/reviewers` with **both** SC ids → `assigned_to_sc`,
   `reviewers` has 2 entries
3. SC #1 `POST /review` `{"accept": true}` → status stays `assigned_to_sc`
   (not all reviewers done yet)
4. SC #1 `POST /review` again → **409** "already reviewed this version"
5. SC #2 `POST /review` `{"accept": true}` → status becomes
   `abstract_review_complete`
6. SC #1 `GET /reviews` → sees **only their own** review (blind)
7. EIC `GET /reviews` → sees **both**
8. Author `GET /reviews` → **403**
9. EIC `POST /announce` `{"decision": "accept"}` → `abstract_accepted`
10. Author submits full paper → `full_paper_submitted`
11. Both SCs review with `{"decision": "revision"}` → `full_paper_review_complete`
12. EIC `POST /announce` `{"decision": "revision"}` → `revision_needed`
13. Author `POST /revision` → `full_paper_submitted`, and
    `GET /versions` shows `full_paper` 1 and 2
14. Both SCs review the new version → `full_paper_review_complete` again
    (proving reviews are keyed per version, not per article)
15. EIC `POST /announce` `{"decision": "accept", "id_recommended_journal": "<uuid>"}`
    → `accepted`

- [ ] **Step 5: Verify COI**

1. Create an SC whose `institution_name` exactly matches an author's.
2. `POST /articles/{id}/reviewers` with that SC → **409** naming the shared
   institution.
3. Same call with `"override_coi": true` → succeeds.
4. Create an SC with `institution_name` null → assignment succeeds without
   override (NULL is permissive by design).

- [ ] **Step 6: Verify the unassign escape hatch**

1. Assign 2 SCs to a fresh article.
2. Have only SC #1 review → status stays `assigned_to_sc`.
3. `DELETE /articles/{id}/reviewers/{sc2_id}` → status advances to
   `abstract_review_complete` (the remaining reviewer is done).
4. Confirm SC #1's review still exists via EIC `GET /reviews`.

- [ ] **Step 7: Verify the backward-compatible shortcut**

`POST /articles/{id}/assign` with `{"id_sc": "<uuid>"}` on a `submitted`
article still works and produces a one-entry `reviewers` array.

- [ ] **Step 8: Clean up test data**

Delete the articles and users created for this verification, and confirm the
pre-existing article count is restored.
