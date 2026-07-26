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
