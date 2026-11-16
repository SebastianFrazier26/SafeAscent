-- Read-only exact-privilege checks for the Phase 2 roles. Exits non-zero on any failure.
-- Run as the same owner that ran create_roles_phase2.sql (membership checks compare to
-- CURRENT_USER). Cumulative: later plans append their expected rows. Revision-aware like
-- grants_phase2.sql (runbook K1, 2026-11-16): a migration's checks are skipped only while none of its
-- relations exist; once any do, all must, and every expectation for that block applies in full.
\set ON_ERROR_STOP on

CREATE TEMP TABLE role_checks (check_name text PRIMARY KEY, ok boolean NOT NULL);

-- PostGIS grants PUBLIC SELECT on spatial_ref_sys and its views, so extension-owned
-- relations are skipped throughout, as verify_roles.sql does.
CREATE TEMP TABLE rels AS
SELECT c.oid, n.nspname || '.' || c.relname AS tbl, n.nspname AS schema, c.relkind
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e');

-- The relations each migration creates, with their kind: the same list as grants_phase2.sql, which
-- explains why a partial block, or one present after an absent one, is a failure and not a skip.
-- Looked up in rels (the catalog), never by to_regclass: that needs USAGE on internal.
CREATE TEMP TABLE block_objects (rev text NOT NULL, tbl text NOT NULL, relkind "char" NOT NULL);
INSERT INTO block_objects VALUES
  ('0004', 'public.source_ingest_log', 'r'), ('0004', 'internal.accident_revisions', 'r'),
  ('0004', 'internal.ingest_quarantine', 'r'), ('0004', 'internal.mp_tick_aggregates', 'r'),
  ('0004', 'internal.accidents_raw', 'r'),
  ('0005', 'public.gnis_summits', 'r'), ('0005', 'internal.duplicate_decisions', 'r'),
  ('0006', 'public.accidents_clean', 'v'), ('0006', 'public.accidents_clean_daily', 'v'),
  ('0007', 'public.cell_daily_conditions', 'r'), ('0007', 'public.grid_bucket_series', 'r'),
  ('0007', 'public.cell_normals_status', 'r'), ('0007', 'public.cell_climate_normals', 'r'),
  ('0007', 'public.cell_climatology', 'r'), ('0007', 'public.accident_conditions', 'v'),
  ('0008', 'public.canonical_areas', 'r'), ('0008', 'public.canonical_routes', 'r'),
  ('0008', 'public.scorable_routes', 'v'), ('0008', 'internal.route_tick_scrapes', 'r'),
  ('0008', 'internal.route_tick_coverage', 'v'), ('0008', 'internal.route_tick_months', 'v'),
  ('0009', 'public.feature_points', 'r'), ('0009', 'public.route_static_features', 'r'),
  ('0009', 'public.scoring_unit_features', 'v'),
  ('0010', 'public.accident_legacy_links', 'r'), ('0010', 'public.accident_route_links', 'v'),
  ('0010', 'internal.r10_unresolved', 'r'), ('0010', 'internal.r10_llm_calls', 'r'),
  ('0010', 'internal.r10_llm_gates', 'r'),
  ('0011', 'internal.exposure_index', 'r');

-- active: every relation of the block exists, so all of its expectations below apply.
CREATE TEMP TABLE blocks AS
SELECT o.rev, bool_and(r.tbl IS NOT NULL) AS active, bool_or(r.tbl IS NOT NULL) AS present
FROM block_objects o LEFT JOIN rels r ON r.tbl = o.tbl
GROUP BY o.rev;

INSERT INTO role_checks
SELECT 'migration block 0004 is present (run at 0004_phase2a_foundation or later)',
       (SELECT active FROM blocks WHERE rev = '0004')
UNION ALL
SELECT 'migration block ' || b.rev || ' is complete or absent', b.active OR NOT b.present
FROM blocks b
UNION ALL
SELECT 'migration block ' || b.rev || ' is absent, or every earlier block is present',
       NOT b.present OR NOT EXISTS (SELECT 1 FROM blocks e WHERE e.rev < b.rev AND NOT e.active)
FROM blocks b
UNION ALL
SELECT 'block ' || o.rev || ' relation exists with its expected kind: ' || o.tbl,
       EXISTS (SELECT 1 FROM rels r WHERE r.tbl = o.tbl AND r.relkind = o.relkind)
FROM block_objects o JOIN blocks b ON b.rev = o.rev
WHERE b.present
UNION ALL
SELECT 'block list holds exactly 0004-0011',
       (SELECT array_agg(rev ORDER BY rev) FROM blocks)
         = ARRAY['0004', '0005', '0006', '0007', '0008', '0009', '0010', '0011'];

-- An absent trailing block looks like an earlier revision, so the schema alone cannot tell "at 0010"
-- from "0011 recorded but its table missing" (a bad stamp, a hand drop). alembic_version settles it:
-- exactly the blocks at or below the recorded revision must be present (revision ids start NNNN_).
-- migrator owns alembic_version; an owner holding SET but not INHERIT on migrator (Phase 1 relaunch
-- step 3 and its re-grant) reads it only after SET ROLE, which is reset before any other check.
SELECT has_table_privilege('public.alembic_version', 'SELECT') AS alembic_readable,
       pg_has_role('migrator', 'SET') AS can_set_migrator \gset
\if :alembic_readable
\elif :can_set_migrator
SET ROLE migrator;
\endif
SELECT count(*) AS recorded_count, coalesce(min(version_num), '') AS recorded_version FROM public.alembic_version \gset
RESET ROLE;

INSERT INTO role_checks
SELECT 'alembic_version holds exactly one NNNN_ revision',
       :recorded_count = 1 AND :'recorded_version' ~ '^[0-9]{4}_'
UNION ALL
SELECT 'migration block ' || b.rev || ' presence matches alembic_version',
       coalesce(b.present = (b.rev::int <= CASE WHEN :'recorded_version' ~ '^[0-9]{4}_'
                                                THEN left(:'recorded_version', 4)::int END), false)
FROM blocks b;

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
  ('ingest can log in', (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'ingest')),
  ('schema internal is owned by migrator',
     (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'internal') = 'migrator'),
  -- PG16 lets CREATE without USAGE create tables in a schema, so both are checked.
  ('app has no USAGE on schema internal', NOT has_schema_privilege('app', 'internal', 'USAGE')),
  ('app has no CREATE on schema internal', NOT has_schema_privilege('app', 'internal', 'CREATE')),
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
-- (checked below), and a column grant leaves has_table_privilege false. A row for a relation that
-- does not exist yet checks nothing; rows for older relations that block 0007 grants on (0006's
-- clean views, area_weekly_weather) are added after, only once that block is active.
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
  ('public.accident_conditions', 'SELECT'),
  -- MP catalog plan (0008): the catalog tables are upserted, never deleted; mp_locations SELECT
  -- is column-level, checked below.
  ('public.canonical_areas', 'SELECT'), ('public.canonical_areas', 'INSERT'), ('public.canonical_areas', 'UPDATE'),
  ('public.canonical_routes', 'SELECT'), ('public.canonical_routes', 'INSERT'), ('public.canonical_routes', 'UPDATE'),
  ('public.scorable_routes', 'SELECT'),
  -- The tick-source seam (DP9) and its two read views.
  ('internal.route_tick_scrapes', 'SELECT'), ('internal.route_tick_scrapes', 'INSERT'),
  ('internal.route_tick_scrapes', 'UPDATE'),
  ('internal.route_tick_coverage', 'SELECT'), ('internal.route_tick_months', 'SELECT'),
  -- MP similarity features (0009): points are upserted, never deleted; route rows are rebuilt
  -- (DELETE for routes that became boulders); the scoring-unit view is read only.
  ('public.feature_points', 'SELECT'), ('public.feature_points', 'INSERT'), ('public.feature_points', 'UPDATE'),
  ('public.route_static_features', 'SELECT'), ('public.route_static_features', 'INSERT'),
  ('public.route_static_features', 'UPDATE'), ('public.route_static_features', 'DELETE'),
  ('public.scoring_unit_features', 'SELECT'),
  -- R10 (0010): legacy links are rebuilt, the unresolved residue is refreshed (DELETE only for
  -- undecided rows a rule resolved); the LLM audit and gates are append-only. routes and
  -- mountains SELECT is column-level, checked below.
  ('public.accident_legacy_links', 'SELECT'), ('public.accident_legacy_links', 'INSERT'),
  ('public.accident_legacy_links', 'UPDATE'), ('public.accident_legacy_links', 'DELETE'),
  ('public.accident_route_links', 'SELECT'),
  ('internal.r10_unresolved', 'SELECT'), ('internal.r10_unresolved', 'INSERT'),
  ('internal.r10_unresolved', 'UPDATE'), ('internal.r10_unresolved', 'DELETE'),
  ('internal.r10_llm_calls', 'SELECT'), ('internal.r10_llm_calls', 'INSERT'),
  ('internal.r10_llm_gates', 'SELECT'), ('internal.r10_llm_gates', 'INSERT'),
  -- Exposure (0011): snapshots are cleared, reinserted and pruned, never updated.
  ('internal.exposure_index', 'SELECT'), ('internal.exposure_index', 'INSERT'),
  ('internal.exposure_index', 'DELETE');
-- Plan 3 reads the 0006 clean views. R7: area_weekly_weather SELECT is table-level; UPDATE is
-- column-level (longitude only), checked below.
INSERT INTO ingest_privs
SELECT t, 'SELECT'
FROM unnest(ARRAY['public.accidents_clean', 'public.accidents_clean_daily', 'public.area_weekly_weather']) AS t
WHERE (SELECT active FROM blocks WHERE rev = '0007');

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

-- R11 inserts accidents through accident_id's nextval() default, which needs USAGE on its
-- sequence: that one sequence is exempt for USAGE only (from block 0007), never SELECT or UPDATE (setval).
INSERT INTO role_checks
SELECT 'ingest has nothing on sequence ' || tbl,
       has_sequence_privilege('ingest', oid, 'USAGE')
         = (tbl = 'public.accidents_accident_id_seq' AND (SELECT active FROM blocks WHERE rev = '0007'))
       AND NOT (has_sequence_privilege('ingest', oid, 'SELECT') OR has_sequence_privilege('ingest', oid, 'UPDATE'))
FROM rels WHERE relkind = 'S'
UNION ALL
SELECT 'accidents id sequence check ran', EXISTS (SELECT 1 FROM rels WHERE tbl = 'public.accidents_accident_id_seq')
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
-- Phase 3 grants these (verify_roles_phase3.sql owns trainer from here).
FROM rels WHERE relkind <> 'S' AND tbl NOT IN ('public.accidents_clean', 'public.accident_route_links',
  'public.canonical_areas', 'public.canonical_routes', 'public.scorable_routes', 'public.scoring_unit_features',
  'public.historical_predictions', 'internal.exposure_index');

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
-- description, source, source_id or accident_id), plus plan 3's tz and R11's mp_route_id; SELECT
-- on accidents_raw only the R1-R4 inputs. INSERT (R11) is exactly accident_facts.INSERT_COLUMNS:
-- never accident_id, location, tags or elevation_meters. tz, mp_route_id and INSERT are block 0007's.
WITH at7 AS (SELECT active FROM blocks WHERE rev = '0007'),
cols AS (
  SELECT a.attnum, a.attname,
         a.attname IN ('date', 'year', 'latitude', 'longitude', 'coordinates', 'country', 'date_precision',
                       'year_source', 'year_lo', 'year_hi', 'geocode_precision', 'geocode_method',
                       'activity_class', 'activity_rule_version', 'inclusion_flag', 'incident_group_id',
                       'is_canonical', 'severity_scale', 'excluded_reason', 'source_url', 'updated_at',
                       'exp_years_climbing', 'exp_stated_level', 'exp_first_season', 'guided', 'exp_rule_version')
           OR ((SELECT active FROM at7) AND a.attname IN ('tz', 'mp_route_id'))
           AS may_update,
         (SELECT active FROM at7) AND a.attname IN ('source', 'source_id', 'date', 'year', 'date_precision', 'state', 'mountain', 'route',
                       'mp_route_id', 'latitude', 'longitude', 'geocode_precision', 'geocode_method', 'country',
                       'tz', 'activity', 'activity_class', 'activity_rule_version', 'inclusion_flag',
                       'accident_type', 'injury_severity', 'severity_scale', 'exp_years_climbing',
                       'exp_stated_level', 'exp_first_season', 'guided', 'exp_rule_version', 'source_url',
                       'description', 'updated_at')
           AS may_insert
  FROM pg_attribute a
  WHERE a.attrelid = 'public.accidents'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest accidents column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.accidents', attnum, 'UPDATE') = may_update
       AND has_column_privilege('ingest', 'public.accidents', attnum, 'INSERT') = may_insert
       AND NOT has_column_privilege('ingest', 'public.accidents', attnum, 'REFERENCES')
FROM cols;

WITH cols AS (
  SELECT a.attnum, a.attname
  FROM pg_attribute a
  WHERE a.attrelid = 'public.mp_locations'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest mp_locations column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.mp_locations', attnum, 'SELECT')
         = (attname IN ('mp_id', 'latitude', 'longitude')
            OR ((SELECT active FROM blocks WHERE rev = '0008') AND attname IN ('parent_id', 'name')))
       AND NOT has_column_privilege('ingest', 'public.mp_locations', attnum, 'UPDATE')
       AND NOT has_column_privilege('ingest', 'public.mp_locations', attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', 'public.mp_locations', attnum, 'REFERENCES')
FROM cols;

-- R10 (0010): ingest reads only the legacy link columns of routes and mountains, never writes;
-- before block 0010, nothing at all.
WITH cols AS (
  SELECT a.attnum, a.attname
  FROM pg_attribute a
  WHERE a.attrelid = 'public.routes'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest routes column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.routes', attnum, 'SELECT')
         = ((SELECT active FROM blocks WHERE rev = '0010')
            AND attname IN ('route_id', 'name', 'grade', 'type', 'latitude', 'longitude', 'mp_route_id'))
       AND NOT has_column_privilege('ingest', 'public.routes', attnum, 'UPDATE')
       AND NOT has_column_privilege('ingest', 'public.routes', attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', 'public.routes', attnum, 'REFERENCES')
FROM cols;

WITH cols AS (
  SELECT a.attnum, a.attname
  FROM pg_attribute a
  WHERE a.attrelid = 'public.mountains'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest mountains column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.mountains', attnum, 'SELECT')
         = ((SELECT active FROM blocks WHERE rev = '0010') AND attname IN ('mountain_id', 'name', 'latitude', 'longitude'))
       AND NOT has_column_privilege('ingest', 'public.mountains', attnum, 'UPDATE')
       AND NOT has_column_privilege('ingest', 'public.mountains', attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', 'public.mountains', attnum, 'REFERENCES')
FROM cols;

-- Plan 3 (R7): ingest's only write on area_weekly_weather is UPDATE longitude (from block 0007);
-- SELECT is table-level (checked in ingest_privs above), so every column already has it.
WITH cols AS (
  SELECT a.attnum, a.attname
  FROM pg_attribute a
  WHERE a.attrelid = 'public.area_weekly_weather'::regclass AND a.attnum > 0 AND NOT a.attisdropped
)
INSERT INTO role_checks
SELECT 'ingest area_weekly_weather column privileges match the expected set: ' || attname,
       has_column_privilege('ingest', 'public.area_weekly_weather', attnum, 'UPDATE')
         = ((SELECT active FROM blocks WHERE rev = '0007') AND attname = 'longitude')
       AND NOT has_column_privilege('ingest', 'public.area_weekly_weather', attnum, 'INSERT')
       AND NOT has_column_privilege('ingest', 'public.area_weekly_weather', attnum, 'REFERENCES')
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

-- Plan 2 (0006), plan 3 (0007) and the MP catalog plan (0008, 0009): app and analyst read the clean
-- views, the conditions tables, accident_conditions, the catalog and its features through migrator's default
-- privileges and hold nothing else on them
-- (a simple view is auto-updatable, so writes are checked too). ingest's SELECT is in the exact
-- set above; trainer's reads are verify_roles_phase3.sql's. That each exists is checked per block at the top.
INSERT INTO role_checks
SELECT g || ' can SELECT ' || r.tbl || ' and nothing else',
       has_table_privilege(g, r.oid, 'SELECT')
       AND NOT (has_table_privilege(g, r.oid, 'INSERT') OR has_table_privilege(g, r.oid, 'UPDATE')
                OR has_table_privilege(g, r.oid, 'DELETE') OR has_table_privilege(g, r.oid, 'TRUNCATE')
                OR has_table_privilege(g, r.oid, 'REFERENCES') OR has_table_privilege(g, r.oid, 'TRIGGER'))
FROM rels r CROSS JOIN unnest(ARRAY['app', 'analyst']) AS g
WHERE r.tbl IN ('public.accidents_clean', 'public.accidents_clean_daily', 'public.accident_conditions',
                'public.cell_daily_conditions', 'public.grid_bucket_series', 'public.cell_normals_status',
                'public.cell_climate_normals', 'public.cell_climatology', 'public.canonical_areas',
                'public.canonical_routes', 'public.scorable_routes', 'public.feature_points',
                'public.route_static_features', 'public.scoring_unit_features', 'public.accident_legacy_links',
                'public.accident_route_links');

-- 0010: owner decisions on internal.r10_unresolved are final only while this trigger exists and
-- fires ('O' = enabled in origin mode); ingest holds UPDATE/DELETE on the table (final review S7).
INSERT INTO role_checks
SELECT 'r10_unresolved keep-decisions trigger exists and is enabled',
       EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               -- Catalog joins, not to_regclass: the verifying role may hold no USAGE on internal.
               WHERE n.nspname = 'internal' AND c.relname = 'r10_unresolved'
               AND t.tgname = 'r10_unresolved_keep_decisions' AND t.tgenabled = 'O' AND NOT t.tgisinternal)
FROM blocks WHERE rev = '0010' AND present;

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN RAISE EXCEPTION 'PHASE 2 ROLE CHECKS FAILED: %', failed; END IF;
END $$;

\echo 'ALL PHASE 2 ROLE CHECKS PASSED'
