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
