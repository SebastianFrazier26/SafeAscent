-- Read-only exact-privilege checks for the Phase 2 roles. Exits non-zero on any failure.
-- Cumulative: later plans append their expected rows.
\set ON_ERROR_STOP on

CREATE TEMP TABLE role_checks (check_name text PRIMARY KEY, ok boolean NOT NULL);

INSERT INTO role_checks
SELECT 'role exists: ' || r, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
FROM unnest(ARRAY['ingest', 'trainer']) AS r;

INSERT INTO role_checks
SELECT 'no elevated attributes, noinherit: ' || rolname,
       NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)
FROM pg_roles WHERE rolname IN ('ingest', 'trainer');

INSERT INTO role_checks
SELECT 'no role memberships: ' || r,
       NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles pr ON pr.oid = m.member WHERE pr.rolname = r)
FROM unnest(ARRAY['ingest', 'trainer']) AS r;

INSERT INTO role_checks VALUES
  ('trainer cannot log in until Phase 3', NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer')),
  ('ingest can log in', (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'ingest')),
  ('schema internal is owned by migrator',
     (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'internal') = 'migrator'),
  ('app has no USAGE on schema internal', NOT has_schema_privilege('app', 'internal', 'USAGE')),
  ('trainer has no USAGE on schema internal', NOT has_schema_privilege('trainer', 'internal', 'USAGE')),
  ('ingest cannot CREATE in internal', NOT has_schema_privilege('ingest', 'internal', 'CREATE')),
  ('analyst cannot CREATE in internal', NOT has_schema_privilege('analyst', 'internal', 'CREATE')),
  ('ingest cannot CREATE in public', NOT has_schema_privilege('ingest', 'public', 'CREATE')),
  ('trainer cannot CREATE in public', NOT has_schema_privilege('trainer', 'public', 'CREATE')),
  ('ingest cannot CREATE schemas in the database', NOT has_database_privilege('ingest', current_database(), 'CREATE')),
  ('trainer cannot CREATE schemas in the database',
     NOT has_database_privilege('trainer', current_database(), 'CREATE'));

-- The exact set of tables ingest may write. Later plans append rows here.
CREATE TEMP TABLE ingest_writes (tbl text, priv text);
INSERT INTO ingest_writes VALUES
  ('public.source_ingest_log', 'INSERT'), ('public.source_ingest_log', 'UPDATE'),
  ('internal.mp_tick_aggregates', 'INSERT'),
  ('internal.ingest_quarantine', 'INSERT');

WITH tables AS (
  SELECT c.oid, n.nspname || '.' || c.relname AS tbl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p')
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
), privs AS (
  SELECT t.tbl, p.priv, has_table_privilege('ingest', t.oid, p.priv) AS held
  FROM tables t CROSS JOIN unnest(ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS p(priv)
)
INSERT INTO role_checks
SELECT 'ingest ' || priv || ' on ' || tbl || ' matches the expected set',
       held = EXISTS (SELECT 1 FROM ingest_writes w WHERE w.tbl = privs.tbl AND w.priv = privs.priv)
FROM privs;

-- PostGIS grants PUBLIC SELECT on spatial_ref_sys and its views, so extension-owned
-- relations are skipped, as verify_roles.sql does.
WITH tables AS (
  SELECT c.oid, n.nspname || '.' || c.relname AS tbl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p', 'v', 'm')
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
)
INSERT INTO role_checks
SELECT 'app holds nothing on internal relation ' || tbl,
       NOT (has_table_privilege('app', oid, 'SELECT') OR has_table_privilege('app', oid, 'INSERT')
            OR has_table_privilege('app', oid, 'UPDATE') OR has_table_privilege('app', oid, 'DELETE')
            OR has_table_privilege('app', oid, 'TRUNCATE'))
FROM tables WHERE tbl LIKE 'internal.%'
UNION ALL
SELECT 'analyst cannot INSERT/UPDATE/DELETE/TRUNCATE ' || tbl,
       NOT (has_table_privilege('analyst', oid, 'INSERT') OR has_table_privilege('analyst', oid, 'UPDATE')
            OR has_table_privilege('analyst', oid, 'DELETE') OR has_table_privilege('analyst', oid, 'TRUNCATE'))
FROM tables WHERE tbl LIKE 'internal.%'
UNION ALL
SELECT 'trainer holds no privilege on ' || tbl,
       NOT (has_table_privilege('trainer', oid, 'SELECT') OR has_table_privilege('trainer', oid, 'INSERT')
            OR has_table_privilege('trainer', oid, 'UPDATE') OR has_table_privilege('trainer', oid, 'DELETE')
            OR has_table_privilege('trainer', oid, 'TRUNCATE'))
FROM tables;

-- A column-level UPDATE grant leaves has_table_privilege(..., 'UPDATE') false, so the
-- expected-set check above rightly expects no table-level UPDATE on mp_ticks.
INSERT INTO role_checks VALUES
  ('ingest may update only the quarantine columns of mp_ticks',
     has_column_privilege('ingest', 'public.mp_ticks', 'quarantine_reason', 'UPDATE')
     AND has_column_privilege('ingest', 'public.mp_ticks', 'quarantine_rule_version', 'UPDATE')
     AND NOT EXISTS (
       SELECT 1 FROM pg_attribute a
       WHERE a.attrelid = 'public.mp_ticks'::regclass AND a.attnum > 0 AND NOT a.attisdropped
         AND a.attname NOT IN ('quarantine_reason', 'quarantine_rule_version')
         AND has_column_privilege('ingest', 'public.mp_ticks', a.attnum, 'UPDATE')));

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN RAISE EXCEPTION 'PHASE 2 ROLE CHECKS FAILED: %', failed; END IF;
END $$;

\echo 'ALL PHASE 2 ROLE CHECKS PASSED'
