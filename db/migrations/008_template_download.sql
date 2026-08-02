-- 008: template download section for the landing page.
--
-- Reuses site_content (key/value) instead of a new table: this is a single
-- editable item (heading, description, url), same shape as venue_body or
-- faq_heading. GET /landing and PUT /admin/content already handle arbitrary
-- keys, so no model/router changes are needed.
--
-- Seeded empty (except heading) so the admin content editor has a field to
-- fill in immediately. Re-running is guarded with ON CONFLICT DO NOTHING so
-- it never overwrites an admin's edits.
--
--   docker exec -i be-postgres-1 psql -U simit -d simit < db/migrations/008_template_download.sql

BEGIN;

INSERT INTO site_content (content_key, content_value) VALUES
    ('template_heading',     'Download Template'),
    ('template_description', ''),
    ('template_url',         '')
ON CONFLICT (content_key) DO NOTHING;

COMMIT;
