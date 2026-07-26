-- SIMIT paper submission schema (PostgreSQL)

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

-- === Reference tables ===

CREATE TABLE role (
    id_role     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name_role   VARCHAR(50) NOT NULL UNIQUE   -- 'admin', 'EIC', 'SC', 'author'
);

CREATE TABLE occupation (
    id_occupation   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occupation_name VARCHAR(100) NOT NULL UNIQUE
);

-- === Topics ===

CREATE TABLE main_topic (
    id_topic    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_name  VARCHAR(150) NOT NULL UNIQUE
);

CREATE TABLE sub_topic_stem (
    id_stem     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stem_topic  VARCHAR(150) NOT NULL,
    id_topic    UUID NOT NULL REFERENCES main_topic(id_topic) ON DELETE CASCADE,
    UNIQUE (id_topic, stem_topic)
);

CREATE TABLE sub_topic_humanity (
    id_humanity     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    humanity_topic  VARCHAR(150) NOT NULL,
    id_topic        UUID NOT NULL REFERENCES main_topic(id_topic) ON DELETE CASCADE,
    UNIQUE (id_topic, humanity_topic)
);

CREATE TABLE sub_topic_interdisciplinary (
    id_interdisciplinary    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    interdisciplinary_topic VARCHAR(150) NOT NULL,
    id_topic                UUID NOT NULL REFERENCES main_topic(id_topic) ON DELETE CASCADE,
    UNIQUE (id_topic, interdisciplinary_topic)
);

-- === Users ===

CREATE TABLE users (
    id_user             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_name           VARCHAR(150) NOT NULL,
    institution_name    VARCHAR(200),
    id_occupation       UUID REFERENCES occupation(id_occupation),
    id_role             UUID NOT NULL REFERENCES role(id_role),
    email               VARCHAR(150) NOT NULL UNIQUE,
    phone_number        VARCHAR(30),
    password_hash       VARCHAR(200) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- === Recommendation output journals (PIJAR, Jurnal Kimia Riset, Jurnal UPI, ...) ===

CREATE TABLE journal (
    id_journal      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_name    VARCHAR(150) NOT NULL UNIQUE
);

-- === Articles ===
-- status tracks two-phase pipeline: abstract review (SC decides, EIC announces) then full-paper review (SC decides, EIC announces).
-- Authors see only: submitted, under_review (masks internal *_decided_*, assigned_to_sc, full_paper_submitted), abstract_accepted, revision_needed, accepted, rejected.
-- SC/EIC/admin see the real internal status values.

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

CREATE TABLE articles (
    id_article              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title                   VARCHAR(300) NOT NULL,
    authors                 TEXT NOT NULL,
    abstract                TEXT NOT NULL,
    keywords                VARCHAR(300),
    abstract_file_path      VARCHAR(500) NOT NULL,   -- upload abstrak
    full_paper_file_path    VARCHAR(500),
    status                  article_status NOT NULL DEFAULT 'submitted',

    id_user                 UUID NOT NULL REFERENCES users(id_user),   -- peserta/author
    id_sc                    UUID REFERENCES users(id_user),            -- SC yang di-assign EIC
    id_topic                 UUID REFERENCES main_topic(id_topic),
    id_recommended_journal   UUID REFERENCES journal(id_journal),

    sc_notes                TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_articles_status ON articles(status);
CREATE INDEX idx_articles_id_user ON articles(id_user);
CREATE INDEX idx_articles_id_sc ON articles(id_sc);

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

-- === Timeline (jadwal penting: batas submit abstrak, batas review, pengumuman, dll) ===

CREATE TABLE timeline (
    id_timeline UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       VARCHAR(200) NOT NULL,
    description TEXT,
    start_date  TIMESTAMPTZ NOT NULL,
    end_date    TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_date >= start_date)
);

CREATE INDEX idx_timeline_start_date ON timeline(start_date);

-- === Seed data ===

INSERT INTO role (name_role) VALUES ('admin'), ('EIC'), ('SC'), ('author');

-- default admin account, password: Admin@123 (bcrypt hash below) -- CHANGE IN PRODUCTION
INSERT INTO users (user_name, institution_name, id_role, email, phone_number, password_hash)
SELECT 'Admin', 'SIMIT', id_role, 'admin@simit.local', '0000000000',
       '$2b$12$O5V04JAEO5jciLdu56VctOaBKVsfHgzPOqYXJS/gm/ONfFjx4gD/y'
FROM role WHERE name_role = 'admin';

INSERT INTO journal (journal_name) VALUES
    ('PIJAR'),
    ('Jurnal Kimia Riset'),
    ('Jurnal UPI');
