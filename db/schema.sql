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
    topic_name  VARCHAR(150) NOT NULL UNIQUE,
    -- Shown under the topic on the public "Sub Theme Paper" cards. Same rows
    -- authors pick from when submitting, so the landing page and the
    -- submission form can never disagree about what the themes are.
    description TEXT,
    sort_order  INT NOT NULL DEFAULT 0
);

CREATE TABLE sub_topic_stem (
    id_stem     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stem_topic  VARCHAR(150) NOT NULL,
    id_topic    UUID NOT NULL REFERENCES main_topic(id_topic) ON DELETE CASCADE,
    -- Sub-themes are published as a numbered list, so the order is part of the
    -- content. Without this they come back alphabetically and item 1 is wrong.
    sort_order  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id_topic, stem_topic)
);

CREATE TABLE sub_topic_humanity (
    id_humanity     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    humanity_topic  VARCHAR(150) NOT NULL,
    id_topic        UUID NOT NULL REFERENCES main_topic(id_topic) ON DELETE CASCADE,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id_topic, humanity_topic)
);

CREATE TABLE sub_topic_interdisciplinary (
    id_interdisciplinary    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    interdisciplinary_topic VARCHAR(150) NOT NULL,
    id_topic                UUID NOT NULL REFERENCES main_topic(id_topic) ON DELETE CASCADE,
    sort_order              INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id_topic, interdisciplinary_topic)
);

-- === Users ===

-- register_as: how a participant signed up.
--   'student'           -> occupation must be one of the three curated student
--                          levels (fixed ids in the seed below)
--   'general_presenter' -> occupation is typed freely and stored as its own
--                          occupation row
CREATE TYPE register_as_kind AS ENUM ('student', 'general_presenter');

CREATE TABLE users (
    id_user             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_name           VARCHAR(150) NOT NULL,
    institution_name    VARCHAR(200),
    id_occupation       UUID REFERENCES occupation(id_occupation),
    id_role             UUID NOT NULL REFERENCES role(id_role),
    register_as         register_as_kind,
    email               VARCHAR(150) NOT NULL UNIQUE,
    phone_number        VARCHAR(30),
    password_hash       VARCHAR(200) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX idx_users_live ON users(id_user) WHERE deleted_at IS NULL;

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
    'assigned_to_sc',                -- EIC assign >=1 SC reviewer
    'abstract_review_complete',      -- internal: semua reviewer selesai, nunggu keputusan EIC
    'abstract_accepted',             -- EIC announce: abstrak diterima, author submit full paper
    'rejected',                      -- pipeline selesai, tidak lolos (terminal)
    'full_paper_submitted',          -- full paper (baru atau revisi) masuk antrian reviewer
    'full_paper_review_complete',    -- internal: semua reviewer selesai, nunggu keputusan EIC
    'revision_needed',               -- EIC announce: author harus resubmit full paper
    'accepted'                       -- EIC announce: full paper diterima (terminal)
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
    id_topic                 UUID REFERENCES main_topic(id_topic),
    -- The sub-theme the author picked, stored as its text: the three sub-theme
    -- tables have different keys so one column cannot reference all of them,
    -- and the label should survive the published list being reworded.
    sub_topic                VARCHAR(150),
    id_recommended_journal   UUID REFERENCES journal(id_journal),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at              TIMESTAMPTZ
);

CREATE INDEX idx_articles_status ON articles(status);
CREATE INDEX idx_articles_id_user ON articles(id_user);

-- Partial indexes: the overwhelmingly common query is "live rows only".
CREATE INDEX idx_articles_live ON articles(id_article) WHERE deleted_at IS NULL;

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

-- === Audit log (admin-only action history) ===
-- Explicit, significant events only — written at the handler inside the same
-- transaction as the change they describe, so a rolled-back request leaves no
-- phantom audit row. id_actor is nullable and NOT cascading: an audit trail
-- that vanishes with its actor is not an audit trail.

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

-- === Landing page content (admin-editable CMS) ===
-- Single-value text lives in site_content as key/value: the admin UI renders a
-- labelled form field per key rather than a JSON editor. Anything that is a
-- list with an order of its own gets a real table below.

CREATE TABLE site_content (
    content_key   VARCHAR(60) PRIMARY KEY,
    content_value TEXT NOT NULL DEFAULT '',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE schedule_item (
    id_schedule  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title        VARCHAR(150) NOT NULL,
    description  TEXT,
    date_text    VARCHAR(100),   -- free text ("15 - 30 Juli 2026"), not a date:
                                 -- the organisers write ranges and notes here
    sort_order   INT NOT NULL DEFAULT 0
);

CREATE INDEX idx_schedule_item_order ON schedule_item(sort_order);

CREATE TABLE faq_item (
    id_faq      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    sort_order  INT NOT NULL DEFAULT 0
);

CREATE INDEX idx_faq_item_order ON faq_item(sort_order);

CREATE TABLE gallery_image (
    id_image    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path   VARCHAR(500) NOT NULL,   -- storage path from POST /uploads
    caption     VARCHAR(200),
    sort_order  INT NOT NULL DEFAULT 0
);

CREATE INDEX idx_gallery_image_order ON gallery_image(sort_order);

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

-- Curated student levels. The ids are FIXED rather than generated: the
-- registration form offers exactly these three when someone registers as a
-- student, so both the frontend and migration 004 can refer to them directly
-- and get the same rows in every environment. Freely-typed occupations
-- (general presenters) get ordinary random ids.
INSERT INTO occupation (id_occupation, occupation_name) VALUES
    ('11111111-1111-4111-8111-111111111111', 'Bachelor Student'),
    ('22222222-2222-4222-8222-222222222222', 'Master Student'),
    ('33333333-3333-4333-8333-333333333333', 'Doctoral Student');

-- default admin account, password: Admin@123 (bcrypt hash below) -- CHANGE IN PRODUCTION
INSERT INTO users (user_name, institution_name, id_role, email, phone_number, password_hash)
SELECT 'Admin', 'SIMIT', id_role, 'admin@simit.local', '0000000000',
       '$2b$12$O5V04JAEO5jciLdu56VctOaBKVsfHgzPOqYXJS/gm/ONfFjx4gD/y'
FROM role WHERE name_role = 'admin';

INSERT INTO journal (journal_name) VALUES
    ('PIJAR'),
    ('Jurnal Kimia Riset'),
    ('Jurnal UPI');

-- === Landing page seed ===
-- Initial values taken from the approved Figma draft. Everything here is
-- admin-editable afterwards; nothing in the frontend hardcodes these strings.

INSERT INTO site_content (content_key, content_value) VALUES
    ('hero_title',        '4th International Symposium PPI Türkiye 2026'),
    ('hero_tagline',      'Breaking Boundaries: Interdisciplinary research for global innovation and resilience.'),
    ('hero_date',         '15 - 16 OCTOBER 2026'),
    ('hero_location',     'ANKARA'),
    ('hero_cta_label',    'Explore Event'),
    ('hero_image_path',   ''),
    ('about_heading',     'About The Simit International Symposium'),
    ('about_body',        'SİMİT is an event for Indonesian students and academics in Turkey and surrounding countries to engage in in-depth discussions. Share knowledge on Indonesia''s strategic role in addressing global conflict challenges as a developing country through academic presentations, in-depth debates, and collaborative networking.

In addition to exploring potential solutions, this international symposium can help deepen understanding of issues that the region and Indonesia itself will face. It does this by focusing on Indonesia''s involvement and contribution on to the global arena from a variety of angles'),
    ('schedule_heading',  'Event Schedule'),
    ('schedule_subtitle', 'A carefully planned timeline ensuring maximum engagement and smooth event flow'),
    ('subtheme_heading',  'Sub Theme Paper'),
    ('subtheme_subtitle', 'Here is sub-theme for article'),
    ('venue_heading',     'Venue'),
    ('venue_body',        ''),
    ('venue_address',     ''),
    ('venue_main',        ''),
    ('venue_metro',       ''),
    ('faq_heading',       'Frequently Asked Questions'),
    ('faq_subtitle',      'We compiled a list of answers to address your most pressing questions regarding our Events.'),
    ('contact_phone_1',   '+90 535 716 94 73'),
    ('contact_phone_2',   ''),
    ('contact_instagram', 'simit_ppi'),
    ('contact_email',     'pusatstudippiturki@gmail.com'),
    ('contact_address',   'Ankara, Türkiye'),
    ('footer_tagline',    'Breaking Boundaries: Interdisciplinary research for global innovation and resilience. Simit 2026'),
    ('footer_copyright',  'Copyright @2026 Pusat Studi PPI Türkiye');

-- The draft shows four identical schedule rows as placeholders; seeded as one
-- real row so an admin edits rather than deletes duplicates.
INSERT INTO schedule_item (title, description, date_text, sort_order) VALUES
    ('Registration', 'Select your participant category, and submit the required personal details.', '15 - 30 Juli 2026', 1);

-- === Paper topics ===
-- The three tracks and their sub-themes, as published for SIMIT 2026. Authors
-- pick a main topic when submitting, and the landing page lists the sub-themes
-- under each one. Seeded rather than left to the admin screens because with no
-- topics at all the Sub Theme section is empty and nobody can submit anything.
--
-- Sub-themes live in one table per track (see the three tables above), so each
-- block below inserts into the table matching its track.
INSERT INTO main_topic (topic_name, description, sort_order) VALUES
    ('STEM Studies',
     'Engineering, environment, health, food systems and digital technology.', 1),
    ('Social Humanity Studies',
     'Society, education, law and economics.', 2),
    ('Interdisciplinary Studies',
     'Themes that cut across the natural and social sciences.', 3);

INSERT INTO sub_topic_stem (id_topic, stem_topic, sort_order)
SELECT id_topic, name, n FROM main_topic,
    (VALUES
        ('Energy Transition, Sustainable Engineering, and Supply Chain Resilience', 1),
        ('Climate Change, Green Sustainability, and Environmental Resilience', 2),
        ('Health, Biotechnology, and Quality of Life', 3),
        ('Food Security, Agriculture, and Natural Resource Management', 4),
        ('Digital Innovation, Artificial Intelligence, Big Data and Smart Systems', 5)
    ) AS t(name, n)
WHERE topic_name = 'STEM Studies';

INSERT INTO sub_topic_humanity (id_topic, humanity_topic, sort_order)
SELECT id_topic, name, n FROM main_topic,
    (VALUES
        ('Social Resilience, Culture Identity, and Community Empowerment', 1),
        ('Education Transformation, Human Development, and Future Skills', 2),
        ('Law Governance, Human Rights, and Global Peace and Security', 3),
        ('Economic Sustainability, Entrepreneurship, and Inclusive Growth', 4)
    ) AS t(name, n)
WHERE topic_name = 'Social Humanity Studies';

INSERT INTO sub_topic_interdisciplinary (id_topic, interdisciplinary_topic, sort_order)
SELECT id_topic, name, n FROM main_topic,
    (VALUES
        ('Disaster Risk Reduction, Urban Resilience, and Sustainable Communities', 1),
        ('Ethics, Policy, and Responsible Artificial Intelligence', 2),
        ('Public Health, Global Social Transformation, and Human Resilience', 3),
        ('Green Economy, Innovation Ecosystems, and Global Partnerships', 4)
    ) AS t(name, n)
WHERE topic_name = 'Interdisciplinary Studies';
