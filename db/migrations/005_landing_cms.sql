-- Migration 005: landing-page CMS
--
-- Adds the admin-editable landing page content: a key/value table for
-- single-value text, plus real tables for the parts that are ordered lists
-- (schedule, FAQ, gallery). Sub themes reuse the existing main_topic rows —
-- the same ones authors pick from when submitting — so the landing page and
-- the submission form can never disagree about what the themes are; that table
-- just gains a description and a sort order.
--
-- Purely additive: nothing is dropped. The seed values come from the approved
-- Figma draft and are all editable afterwards, so re-running is guarded with
-- ON CONFLICT DO NOTHING rather than overwriting an admin's edits.
--
-- Fresh databases do not need this; db/schema.sql already has all of it.

BEGIN;

ALTER TABLE main_topic ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE main_topic ADD COLUMN IF NOT EXISTS sort_order INT NOT NULL DEFAULT 0;

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
    ('footer_copyright',  'Copyright @2026 Pusat Studi PPI Türkiye')
ON CONFLICT (content_key) DO NOTHING;

-- The draft shows four identical schedule rows as placeholders; seeded as one
-- real row so an admin edits rather than deletes duplicates.
INSERT INTO schedule_item (title, description, date_text, sort_order) VALUES
    ('Registration', 'Select your participant category, and submit the required personal details.', '15 - 30 Juli 2026', 1);

COMMIT;
