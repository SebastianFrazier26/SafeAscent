-- Read-only exact-privilege checks for the Phase 2 roles. Exits non-zero on any failure.
-- Run as the same owner that ran create_roles_phase2.sql (membership checks compare to
-- CURRENT_USER). Cumulative: later plans append their expected rows.
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

-- PG16 gives a CREATEROLE creator an ADMIN grant with SET and INHERIT off, which confers no
-- privileges; any grant that can SET or INHERIT these roles counts as membership here.
INSERT INTO role_checks
SELECT 'nobody can SET or INHERIT ' || r,
       NOT EXISTS (SELECT 1 FROM pg_auth_members m
                   WHERE m.roleid = to_regrole(r) AND (m.set_option OR m.inherit_option))
FROM unnest(ARRAY['ingest', 'trainer']) AS r
UNION ALL
SELECT 'nobody but CURRENT_USER holds ADMIN on ' || r,
       NOT EXISTS (SELECT 1 FROM pg_auth_members m
                   WHERE m.roleid = to_regrole(r) AND m.member <> CURRENT_USER::regrole)
FROM unnest(ARRAY['ingest', 'trainer']) AS r;

INSERT INTO role_checks VALUES
  ('trainer cannot log in until Phase 3', NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer')),
  ('ingest can log in', (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'ingest')),
  ('schema internal is owned by migrator',
     (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'internal') = 'migrator'),
  -- PG16 lets CREATE without USAGE create tables in a schema, so both are checked.
  ('app has no USAGE on schema internal', NOT has_schema_privilege('app', 'internal', 'USAGE')),
  ('app has no CREATE on schema internal', NOT has_schema_privilege('app', 'internal', 'CREATE')),
  ('trainer has no USAGE on schema internal', NOT has_schema_privilege('trainer', 'internal', 'USAGE')),
  ('trainer has no CREATE on schema internal', NOT has_schema_privilege('trainer', 'internal', 'CREATE')),
  ('ingest cannot CREATE in internal', NOT has_schema_privilege('ingest', 'internal', 'CREATE')),
  ('analyst cannot CREATE in internal', NOT has_schema_privilege('analyst', 'internal', 'CREATE')),
  ('ingest cannot CREATE in public', NOT has_schema_privilege('ingest', 'public', 'CREATE')),
  ('trainer cannot CREATE in public', NOT has_schema_privilege('trainer', 'public', 'CREATE')),
  ('ingest cannot CREATE schemas in the database', NOT has_database_privilege('ingest', current_database(), 'CREATE')),
  ('trainer cannot CREATE schemas in the database',
     NOT has_database_privilege('trainer', current_database(), 'CREATE')),
  ('no default privileges grant ingest or trainer anything',
     NOT EXISTS (
       SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
       WHERE a.grantee IN (to_regrole('ingest'), to_regrole('trainer'))));

-- The exact table-level privileges ingest holds. Later plans append rows here. mp_ticks is
-- absent on purpose: ingest's SELECT and UPDATE there are column-level (checked below), and a
-- column grant leaves has_table_privilege false.
CREATE TEMP TABLE ingest_privs (tbl text, priv text);
INSERT INTO ingest_privs VALUES
  ('public.mp_routes', 'SELECT'),
  ('public.source_ingest_log', 'SELECT'), ('public.source_ingest_log', 'INSERT'),
  ('public.source_ingest_log', 'UPDATE'),
  ('internal.mp_tick_aggregates', 'SELECT'), ('internal.mp_tick_aggregates', 'INSERT'),
  ('internal.ingest_quarantine', 'SELECT'), ('internal.ingest_quarantine', 'INSERT');

-- PostGIS grants PUBLIC SELECT on spatial_ref_sys and its views, so extension-owned
-- relations are skipped throughout, as verify_roles.sql does.
CREATE TEMP TABLE rels AS
SELECT c.oid, n.nspname || '.' || c.relname AS tbl, n.nspname AS schema, c.relkind
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e');

WITH privs AS (
  SELECT r.tbl, p.priv, has_table_privilege('ingest', r.oid, p.priv) AS held
  FROM rels r
  CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']) AS p(priv)
  WHERE r.relkind <> 'S'
)
INSERT INTO role_checks
SELECT 'ingest ' || priv || ' on ' || tbl || ' matches the expected set',
       held = EXISTS (SELECT 1 FROM ingest_privs e WHERE e.tbl = privs.tbl AND e.priv = privs.priv)
FROM privs;

INSERT INTO role_checks
SELECT 'ingest has nothing on sequence ' || tbl,
       NOT (has_sequence_privilege('ingest', oid, 'USAGE') OR has_sequence_privilege('ingest', oid, 'SELECT')
            OR has_sequence_privilege('ingest', oid, 'UPDATE'))
FROM rels WHERE relkind = 'S'
UNION ALL
SELECT 'app holds nothing on internal relation ' || tbl,
       NOT (has_table_privilege('app', oid, 'SELECT') OR has_table_privilege('app', oid, 'INSERT')
            OR has_table_privilege('app', oid, 'UPDATE') OR has_table_privilege('app', oid, 'DELETE')
            OR has_table_privilege('app', oid, 'TRUNCATE'))
FROM rels WHERE schema = 'internal' AND relkind <> 'S'
UNION ALL
SELECT 'analyst can SELECT ' || tbl, has_table_privilege('analyst', oid, 'SELECT')
FROM rels WHERE schema = 'internal' AND relkind <> 'S'
UNION ALL
SELECT 'analyst cannot INSERT/UPDATE/DELETE/TRUNCATE ' || tbl,
       NOT (has_table_privilege('analyst', oid, 'INSERT') OR has_table_privilege('analyst', oid, 'UPDATE')
            OR has_table_privilege('analyst', oid, 'DELETE') OR has_table_privilege('analyst', oid, 'TRUNCATE'))
FROM rels WHERE schema = 'internal' AND relkind <> 'S'
UNION ALL
SELECT 'trainer holds no privilege on ' || tbl,
       NOT (has_table_privilege('trainer', oid, 'SELECT') OR has_table_privilege('trainer', oid, 'INSERT')
            OR has_table_privilege('trainer', oid, 'UPDATE') OR has_table_privilege('trainer', oid, 'DELETE')
            OR has_table_privilege('trainer', oid, 'TRUNCATE'))
FROM rels WHERE relkind <> 'S';

-- ingest's column grants on mp_ticks: SELECT exactly what R8 reads (never climber_name),
-- UPDATE exactly the two quarantine columns.
WITH cols AS (
  SELECT a.attnum, a.attname,
         a.attname IN ('tick_id', 'route_id', 'tick_date', 'created_at', 'quarantine_reason', 'quarantine_rule_version')
           AS may_select,
         a.attname IN ('quarantine_reason', 'quarantine_rule_version') AS may_update
  FROM pg_attribute a
  WHERE a.attrelid = 'public.mp_ticks'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest mp_ticks column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.mp_ticks', attnum, 'SELECT') = may_select
       AND has_column_privilege('ingest', 'public.mp_ticks', attnum, 'UPDATE') = may_update
       AND NOT has_column_privilege('ingest', 'public.mp_ticks', attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', 'public.mp_ticks', attnum, 'REFERENCES')
FROM cols;

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN RAISE EXCEPTION 'PHASE 2 ROLE CHECKS FAILED: %', failed; END IF;
END $$;

\echo 'ALL PHASE 2 ROLE CHECKS PASSED'
