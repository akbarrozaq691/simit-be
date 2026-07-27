-- 006: give sub-themes an explicit order, and seed the published tracks.
--
-- Sub-themes are published as a numbered list, so their order is content, not a
-- detail. Without a column for it the API returned them alphabetically and the
-- landing page showed the wrong item as number 1.
--
-- Safe to run on a database that already has topics: the inserts skip names that
-- are already present, and nothing existing is deleted or renamed.
--
--   docker exec -i be-postgres-1 psql -U simit -d simit < db/migrations/006_sub_topic_order.sql

BEGIN;

ALTER TABLE sub_topic_stem
    ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sub_topic_humanity
    ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sub_topic_interdisciplinary
    ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;

-- The three tracks for SIMIT 2026.
INSERT INTO main_topic (topic_name, description, sort_order)
SELECT v.n, v.d, v.o FROM (VALUES
    ('STEM Studies',
     'Engineering, environment, health, food systems and digital technology.', 1),
    ('Social Humanity Studies',
     'Society, education, law and economics.', 2),
    ('Interdisciplinary Studies',
     'Themes that cut across the natural and social sciences.', 3)
) AS v(n, d, o)
WHERE NOT EXISTS (SELECT 1 FROM main_topic m WHERE m.topic_name = v.n);

INSERT INTO sub_topic_stem (id_topic, stem_topic, sort_order)
SELECT m.id_topic, t.name, t.n FROM main_topic m, (VALUES
    ('Energy Transition, Sustainable Engineering, and Supply Chain Resilience', 1),
    ('Climate Change, Green Sustainability, and Environmental Resilience', 2),
    ('Health, Biotechnology, and Quality of Life', 3),
    ('Food Security, Agriculture, and Natural Resource Management', 4),
    ('Digital Innovation, Artificial Intelligence, Big Data and Smart Systems', 5)
) AS t(name, n)
WHERE m.topic_name = 'STEM Studies'
  AND NOT EXISTS (
      SELECT 1 FROM sub_topic_stem s
      WHERE s.id_topic = m.id_topic AND s.stem_topic = t.name
  );

INSERT INTO sub_topic_humanity (id_topic, humanity_topic, sort_order)
SELECT m.id_topic, t.name, t.n FROM main_topic m, (VALUES
    ('Social Resilience, Culture Identity, and Community Empowerment', 1),
    ('Education Transformation, Human Development, and Future Skills', 2),
    ('Law Governance, Human Rights, and Global Peace and Security', 3),
    ('Economic Sustainability, Entrepreneurship, and Inclusive Growth', 4)
) AS t(name, n)
WHERE m.topic_name = 'Social Humanity Studies'
  AND NOT EXISTS (
      SELECT 1 FROM sub_topic_humanity s
      WHERE s.id_topic = m.id_topic AND s.humanity_topic = t.name
  );

INSERT INTO sub_topic_interdisciplinary (id_topic, interdisciplinary_topic, sort_order)
SELECT m.id_topic, t.name, t.n FROM main_topic m, (VALUES
    ('Disaster Risk Reduction, Urban Resilience, and Sustainable Communities', 1),
    ('Ethics, Policy, and Responsible Artificial Intelligence', 2),
    ('Public Health, Global Social Transformation, and Human Resilience', 3),
    ('Green Economy, Innovation Ecosystems, and Global Partnerships', 4)
) AS t(name, n)
WHERE m.topic_name = 'Interdisciplinary Studies'
  AND NOT EXISTS (
      SELECT 1 FROM sub_topic_interdisciplinary s
      WHERE s.id_topic = m.id_topic AND s.interdisciplinary_topic = t.name
  );

-- Number rows that already existed. Inserting alone is not enough: on any
-- database where these sub-themes were added by hand they are already present,
-- the INSERTs above skip them, and every row keeps sort_order 0 — which sorts
-- alphabetically and puts the wrong item at number 1.
UPDATE sub_topic_stem s SET sort_order = v.n
FROM (VALUES
    ('Energy Transition, Sustainable Engineering, and Supply Chain Resilience', 1),
    ('Climate Change, Green Sustainability, and Environmental Resilience', 2),
    ('Health, Biotechnology, and Quality of Life', 3),
    ('Food Security, Agriculture, and Natural Resource Management', 4),
    ('Digital Innovation, Artificial Intelligence, Big Data and Smart Systems', 5)
) AS v(name, n)
WHERE s.stem_topic = v.name AND s.sort_order <> v.n;

UPDATE sub_topic_humanity s SET sort_order = v.n
FROM (VALUES
    ('Social Resilience, Culture Identity, and Community Empowerment', 1),
    ('Education Transformation, Human Development, and Future Skills', 2),
    ('Law Governance, Human Rights, and Global Peace and Security', 3),
    ('Economic Sustainability, Entrepreneurship, and Inclusive Growth', 4)
) AS v(name, n)
WHERE s.humanity_topic = v.name AND s.sort_order <> v.n;

UPDATE sub_topic_interdisciplinary s SET sort_order = v.n
FROM (VALUES
    ('Disaster Risk Reduction, Urban Resilience, and Sustainable Communities', 1),
    ('Ethics, Policy, and Responsible Artificial Intelligence', 2),
    ('Public Health, Global Social Transformation, and Human Resilience', 3),
    ('Green Economy, Innovation Ecosystems, and Global Partnerships', 4)
) AS v(name, n)
WHERE s.interdisciplinary_topic = v.name AND s.sort_order <> v.n;

COMMIT;
