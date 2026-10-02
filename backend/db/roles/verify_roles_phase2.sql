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

-- The exact table-level privileges ingest holds. Later plans append rows here. mp_ticks,
-- accidents UPDATE and accidents_raw are absent on purpose: those grants are column-level
-- (checked below), and a column grant leaves has_table_privilege false.
CREATE TEMP TABLE ingest_privs (tbl text, priv text);
INSERT INTO ingest_privs VALUES
  ('public.mp_routes', 'SELECT'),
  ('public.source_ingest_log', 'SELECT'), ('public.source_ingest_log', 'INSERT'),
  ('public.source_ingest_log', 'UPDATE'),
  ('internal.mp_tick_aggregates', 'SELECT'), ('internal.mp_tick_aggregates', 'INSERT'),
  ('internal.ingest_quarantine', 'SELECT'), ('internal.ingest_quarantine', 'INSERT'),
  -- Plan 2 (accident repair); mp_locations SELECT is column-level, checked below.
  ('public.accidents', 'SELECT'),
  ('internal.accident_revisions', 'SELECT'), ('internal.accident_revisions', 'INSERT'),
  -- Plan 2 (0005): the GNIS loader and R5 decisions.
  ('public.gnis_summits', 'SELECT'), ('public.gnis_summits', 'INSERT'), ('public.gnis_summits', 'UPDATE'),
  ('public.gnis_summits', 'DELETE'),
  ('internal.duplicate_decisions', 'SELECT'), ('internal.duplicate_decisions', 'INSERT'),
  ('internal.duplicate_decisions', 'UPDATE'),
  -- Plan 3 (0007): conditions (DELETE for the prune), series registry, normals, read views.
  ('public.cell_daily_conditions', 'SELECT'), ('public.cell_daily_conditions', 'INSERT'),
  ('public.cell_daily_conditions', 'UPDATE'), ('public.cell_daily_conditions', 'DELETE'),
  ('public.grid_bucket_series', 'SELECT'), ('public.grid_bucket_series', 'INSERT'),
  ('public.grid_bucket_series', 'UPDATE'),
  ('public.cell_normals_status', 'SELECT'), ('public.cell_normals_status', 'INSERT'),
  ('public.cell_normals_status', 'UPDATE'),
  ('public.cell_climate_normals', 'SELECT'), ('public.cell_climate_normals', 'INSERT'),
  ('public.cell_climate_normals', 'UPDATE'),
  ('public.cell_climatology', 'SELECT'), ('public.cell_climatology', 'INSERT'),
  ('public.cell_climatology', 'UPDATE'),
  ('public.accidents_clean', 'SELECT'), ('public.accidents_clean_daily', 'SELECT'),
  ('public.accident_conditions', 'SELECT'),
  -- Plan 3 (R6 verify): the legacy weather table is read-only input to r6_verify.
  ('public.weather', 'SELECT');

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

-- ingest's column grants from plan 2: UPDATE on accidents exactly the repair columns (never
-- description, source, source_id or accident_id), plus plan 3's tz; SELECT on accidents_raw only
-- the R1-R4 inputs.
WITH cols AS (
  SELECT a.attnum, a.attname,
         a.attname IN ('date', 'year', 'latitude', 'longitude', 'coordinates', 'country', 'date_precision',
                       'year_source', 'year_lo', 'year_hi', 'geocode_precision', 'geocode_method',
                       'activity_class', 'activity_rule_version', 'inclusion_flag', 'incident_group_id',
                       'is_canonical', 'severity_scale', 'excluded_reason', 'source_url', 'updated_at',
                       'exp_years_climbing', 'exp_stated_level', 'exp_first_season', 'guided', 'exp_rule_version',
                       'tz')
           AS may_update
  FROM pg_attribute a
  WHERE a.attrelid = 'public.accidents'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest accidents column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.accidents', attnum, 'UPDATE') = may_update
       AND NOT has_column_privilege('ingest', 'public.accidents', attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', 'public.accidents', attnum, 'REFERENCES')
FROM cols;

WITH cols AS (
  SELECT a.attnum, a.attname
  FROM pg_attribute a
  WHERE a.attrelid = 'public.mp_locations'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest mp_locations column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.mp_locations', attnum, 'SELECT') = (attname IN ('mp_id', 'latitude', 'longitude'))
       AND NOT has_column_privilege('ingest', 'public.mp_locations', attnum, 'UPDATE')
       AND NOT has_column_privilege('ingest', 'public.mp_locations', attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', 'public.mp_locations', attnum, 'REFERENCES')
FROM cols;

-- By oid from rels, not 'internal.accidents_raw'::regclass: the name lookup needs USAGE on
-- internal, which the owner running this script may not hold.
WITH cols AS (
  SELECT r.oid, a.attnum, a.attname
  FROM rels r JOIN pg_attribute a ON a.attrelid = r.oid
  WHERE r.tbl = 'internal.accidents_raw' AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest accidents_raw column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', oid, attnum, 'SELECT') = (attname IN ('accident_id', 'source', 'source_id', 'date', 'year',
                                                                     'mountain', 'state', 'latitude', 'longitude', 'mp_route_id',
                                                                     'activity'))
       AND NOT has_column_privilege('ingest', oid, attnum, 'UPDATE')
       AND NOT has_column_privilege('ingest', oid, attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', oid, attnum, 'REFERENCES')
FROM cols;
INSERT INTO role_checks VALUES
  ('accidents_raw column checks ran', EXISTS (SELECT 1 FROM rels WHERE tbl = 'internal.accidents_raw'));

-- Plan 2 (0006) and plan 3 (0007): app and analyst read the clean views, the conditions tables
-- and accident_conditions through migrator's default privileges and hold nothing else on them
-- (a simple view is auto-updatable, so writes are checked too). ingest's SELECT is in the exact
-- set above; trainer holds nothing until Phase 3 (D13), per the trainer check above.
INSERT INTO role_checks
SELECT 'clean view exists: ' || v, EXISTS (SELECT 1 FROM rels WHERE tbl = v AND relkind = 'v')
FROM unnest(ARRAY['public.accidents_clean', 'public.accidents_clean_daily', 'public.accident_conditions']) AS v
UNION ALL
SELECT 'conditions table exists: ' || t, EXISTS (SELECT 1 FROM rels WHERE tbl = t AND relkind = 'r')
FROM unnest(ARRAY['public.cell_daily_conditions', 'public.grid_bucket_series', 'public.cell_normals_status',
                  'public.cell_climate_normals', 'public.cell_climatology']) AS t
UNION ALL
SELECT g || ' can SELECT ' || r.tbl || ' and nothing else',
       has_table_privilege(g, r.oid, 'SELECT')
       AND NOT (has_table_privilege(g, r.oid, 'INSERT') OR has_table_privilege(g, r.oid, 'UPDATE')
                OR has_table_privilege(g, r.oid, 'DELETE') OR has_table_privilege(g, r.oid, 'TRUNCATE')
                OR has_table_privilege(g, r.oid, 'REFERENCES') OR has_table_privilege(g, r.oid, 'TRIGGER'))
FROM rels r CROSS JOIN unnest(ARRAY['app', 'analyst']) AS g
WHERE r.tbl IN ('public.accidents_clean', 'public.accidents_clean_daily', 'public.accident_conditions',
                'public.cell_daily_conditions', 'public.grid_bucket_series', 'public.cell_normals_status',
                'public.cell_climate_normals', 'public.cell_climatology');

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN RAISE EXCEPTION 'PHASE 2 ROLE CHECKS FAILED: %', failed; END IF;
END $$;

\echo 'ALL PHASE 2 ROLE CHECKS PASSED'
