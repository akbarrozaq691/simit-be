-- Migration 004: register_as, curated student levels, normalised contact data
--
-- Three changes:
--   1. users.register_as — how a participant signed up ('student' or
--      'general_presenter'). Nullable, because pre-existing accounts (admin,
--      EIC, SC, and authors created before this) were never asked.
--   2. The three curated student occupations, with FIXED ids so the frontend
--      and every environment agree on them.
--   3. A one-off tidy of existing rows: title-case names and institutions,
--      lowercase emails.
--
-- Fresh databases do not need this — db/schema.sql already has all of it.
--
-- Note on (3): this is deliberately CONSERVATIVE. Postgres `initcap` would
-- destroy acronyms — 'SIMIT' becomes 'Simit', 'UPI' becomes 'Upi' — and the
-- existing rows contain exactly those. So it only rewrites values that are
-- unambiguously sloppy input:
--   * entirely lowercase (someone never pressed shift), or
--   * carrying leading/trailing/doubled whitespace
-- Anything with deliberate capitalisation is left alone. Every future write
-- goes through `src/normalize.py`, which knows about particles ("bin",
-- "van der") and acronyms; reimplementing those word lists in SQL would mean
-- maintaining them twice and letting them drift.

BEGIN;

-- --- 1. register_as -------------------------------------------------------

CREATE TYPE register_as_kind AS ENUM ('student', 'general_presenter');

ALTER TABLE users ADD COLUMN register_as register_as_kind;

-- --- 2. Curated student levels with fixed ids -----------------------------
-- ON CONFLICT DO NOTHING so re-running against a database that already has
-- them (or has an occupation of the same name) is harmless.

INSERT INTO occupation (id_occupation, occupation_name) VALUES
    ('11111111-1111-4111-8111-111111111111', 'Bachelor Student'),
    ('22222222-2222-4222-8222-222222222222', 'Master Student'),
    ('33333333-3333-4333-8333-333333333333', 'Doctoral Student')
ON CONFLICT (occupation_name) DO NOTHING;

-- --- 3. Tidy existing contact data ----------------------------------------

-- Collapse stray whitespace for everyone — this never loses information.
UPDATE users
SET user_name = regexp_replace(btrim(user_name), '\s+', ' ', 'g')
WHERE user_name <> regexp_replace(btrim(user_name), '\s+', ' ', 'g');

UPDATE users
SET institution_name = regexp_replace(btrim(institution_name), '\s+', ' ', 'g')
WHERE institution_name IS NOT NULL
  AND institution_name <> regexp_replace(btrim(institution_name), '\s+', ' ', 'g');

-- Title-case ONLY values that are entirely lowercase, so acronyms like 'SIMIT'
-- and 'UPI' survive untouched.
UPDATE users
SET user_name = initcap(user_name)
WHERE user_name = lower(user_name)
  AND user_name ~ '[a-z]';

UPDATE users
SET institution_name = initcap(institution_name)
WHERE institution_name IS NOT NULL
  AND institution_name = lower(institution_name)
  AND institution_name ~ '[a-z]';

-- Blank-but-not-null institutions are absence of data, not data.
UPDATE users SET institution_name = NULL WHERE btrim(coalesce(institution_name, '')) = '';

UPDATE users
SET email = lower(btrim(email))
WHERE email <> lower(btrim(email));

-- Phone numbers are left untouched on purpose. Existing values (e.g. the
-- seeded admin's '0000000000') have no country code, and guessing one would
-- invent data. New and edited numbers must be E.164; these stay as-is until
-- someone updates the profile.

COMMIT;
