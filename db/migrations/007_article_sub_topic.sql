-- 007: record which sub-theme an author submitted under.
--
-- Authors pick a main topic AND one of its numbered sub-themes. Only the main
-- topic was stored, so the sub-theme an author chose was thrown away.
--
-- Stored as the sub-theme's text rather than a foreign key. The three sub-theme
-- tables have different primary keys and column names, so one column cannot
-- reference all of them without a polymorphic kind column and no integrity
-- either way. The name is also what every screen displays, and keeping it means
-- a submitted paper still shows what its author chose even if the published list
-- is later reworded. Submission validates the value against the sub-themes of
-- the chosen topic, so it cannot be invented.
--
-- Nullable: articles submitted before this column existed have no sub-theme, and
-- a topic with no sub-themes still accepts submissions.
--
--   docker exec -i be-postgres-1 psql -U simit -d simit < db/migrations/007_article_sub_topic.sql

BEGIN;

ALTER TABLE articles ADD COLUMN IF NOT EXISTS sub_topic VARCHAR(150);

COMMIT;
