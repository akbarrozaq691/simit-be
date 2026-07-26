-- Migration 001: two-phase review pipeline + article version history
--
-- Upgrades an EXISTING database created from the old db/schema.sql (9-value
-- article_status enum, no article_version table) to the current schema,
-- preserving all article/user data.
--
-- Fresh databases do NOT need this — db/schema.sql already creates the new
-- shape via docker-entrypoint-initdb.d on an empty volume. Run this only on
-- a database that already holds data under the old enum.
--
-- Old -> new status mapping. The old enum did not distinguish abstract-phase
-- from full-paper-phase review, so `full_paper_file_path IS NOT NULL` is used
-- to infer which phase a row was actually in:
--
--   submitted             -> submitted
--   assigned_to_sc        -> assigned_to_sc
--   under_review          -> assigned_to_sc            (SC's reviewable state is now assigned_to_sc)
--   passed_review         -> full_paper_decided_accept  (if a full paper exists)
--                         -> abstract_decided_accept    (otherwise)
--   revision_needed       -> full_paper_decided_revision (if a full paper exists)
--                         -> abstract_decided_reject     (otherwise; the old
--                            abstract-phase "not passed" had no revision loop)
--   announced             -> full_paper_submitted       (if a full paper exists)
--                         -> abstract_accepted          (otherwise)
--   full_paper_submitted  -> full_paper_submitted
--   rejected              -> rejected
--   completed             -> accepted
--
-- Note: historical rows mapped to `accepted` may have a NULL
-- id_recommended_journal (the old pipeline did not require one). That is
-- accepted deliberately — the journal requirement is enforced going forward
-- at the announce endpoint, not as a DB constraint, and these rows are
-- already terminal so they will never pass through announce again.

BEGIN;

-- --- 1. Swap the enum type -------------------------------------------------
-- Postgres cannot remove values from an existing enum, so build the new type
-- and convert the column across with the mapping above.

CREATE TYPE article_status_new AS ENUM (
    'submitted',
    'assigned_to_sc',
    'abstract_decided_accept',
    'abstract_decided_reject',
    'abstract_accepted',
    'rejected',
    'full_paper_submitted',
    'full_paper_decided_revision',
    'full_paper_decided_accept',
    'revision_needed',
    'accepted'
);

-- The column default references the old type; drop it before the swap.
ALTER TABLE articles ALTER COLUMN status DROP DEFAULT;

ALTER TABLE articles
    ALTER COLUMN status TYPE article_status_new
    USING (
        CASE status::text
            WHEN 'submitted'            THEN 'submitted'
            WHEN 'assigned_to_sc'       THEN 'assigned_to_sc'
            WHEN 'under_review'         THEN 'assigned_to_sc'
            WHEN 'passed_review'        THEN CASE WHEN full_paper_file_path IS NOT NULL
                                                 THEN 'full_paper_decided_accept'
                                                 ELSE 'abstract_decided_accept' END
            WHEN 'revision_needed'      THEN CASE WHEN full_paper_file_path IS NOT NULL
                                                 THEN 'full_paper_decided_revision'
                                                 ELSE 'abstract_decided_reject' END
            WHEN 'announced'            THEN CASE WHEN full_paper_file_path IS NOT NULL
                                                 THEN 'full_paper_submitted'
                                                 ELSE 'abstract_accepted' END
            WHEN 'full_paper_submitted' THEN 'full_paper_submitted'
            WHEN 'rejected'             THEN 'rejected'
            WHEN 'completed'            THEN 'accepted'
        END
    )::article_status_new;

DROP TYPE article_status;
ALTER TYPE article_status_new RENAME TO article_status;

ALTER TABLE articles ALTER COLUMN status SET DEFAULT 'submitted';

-- --- 2. Version history table ---------------------------------------------

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

-- --- 3. Backfill version history from existing file paths ------------------
-- Pre-migration rows have no revision history, so each existing file becomes
-- version 1 of its phase. submitted_by is the article's author; timestamps
-- approximate the original submission (created_at for the abstract,
-- updated_at for the full paper).

INSERT INTO article_version (id_article, phase, version_number, file_path, submitted_by, submitted_at)
SELECT id_article, 'abstract', 1, abstract_file_path, id_user, created_at
FROM articles
WHERE abstract_file_path IS NOT NULL;

INSERT INTO article_version (id_article, phase, version_number, file_path, submitted_by, submitted_at)
SELECT id_article, 'full_paper', 1, full_paper_file_path, id_user, updated_at
FROM articles
WHERE full_paper_file_path IS NOT NULL;

COMMIT;
