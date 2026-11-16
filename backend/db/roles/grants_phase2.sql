-- Idempotent Phase 2 grants, run after every Phase 2 migration and then
-- verify_roles_phase2.sql. Run it as migrator directly, or as the owner while it holds SET on
-- migrator (SET ROLE migrator is a no-op for migrator itself): GRANT on objects migrator owns
-- must come from migrator. Cumulative: later plans append their grants inside the transaction.
-- Revision-aware (runbook K1, 2026-11-16): each migration's grants apply only once its relations
-- exist, so this runs at 0004 and at every later revision; verify_roles_phase2.sql mirrors it.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never

SELECT pg_has_role(current_user, 'migrator', 'SET') AS can_set_migrator \gset
\if :can_set_migrator
\else
  DO $$ BEGIN RAISE EXCEPTION 'run as migrator, or as an owner with SET on migrator: GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT FALSE'; END $$;
\endif

BEGIN;
SET ROLE migrator;

-- The relations each migration creates, which gate that migration's grants below. A block is applied
-- only when all of its relations exist. A partial block, or a block present after an absent one,
-- STOPs the run: alembic applies each migration in one transaction and they form a single linear
-- chain, so neither state arises at any revision; one means hand-made drift, and granting half a
-- block (or skipping it) would leave privileges no reviewer expects. verify_roles_phase2.sql keeps
-- the same list (psql cannot share a VALUES list across files without string-building SQL, so
-- tests/test_roles_phase2.py pins the two copies equal). to_regclass is safe here: migrator owns
-- schema internal.
SELECT trim(leading '; ' from problems) = '' AS blocks_ok, problems AS block_problems,
       at_0005, at_0007, at_0008, at_0009, at_0010, at_0011
FROM (
  WITH objects (rev, rel) AS (VALUES
    ('0004', 'public.source_ingest_log'), ('0004', 'internal.accident_revisions'),
    ('0004', 'internal.ingest_quarantine'), ('0004', 'internal.mp_tick_aggregates'),
    ('0004', 'internal.accidents_raw'),
    ('0005', 'public.gnis_summits'), ('0005', 'internal.duplicate_decisions'),
    ('0006', 'public.accidents_clean'), ('0006', 'public.accidents_clean_daily'),
    ('0007', 'public.cell_daily_conditions'), ('0007', 'public.grid_bucket_series'),
    ('0007', 'public.cell_normals_status'), ('0007', 'public.cell_climate_normals'),
    ('0007', 'public.cell_climatology'), ('0007', 'public.accident_conditions'),
    ('0008', 'public.canonical_areas'), ('0008', 'public.canonical_routes'), ('0008', 'public.scorable_routes'),
    ('0008', 'internal.route_tick_scrapes'), ('0008', 'internal.route_tick_coverage'),
    ('0008', 'internal.route_tick_months'),
    ('0009', 'public.feature_points'), ('0009', 'public.route_static_features'),
    ('0009', 'public.scoring_unit_features'),
    ('0010', 'public.accident_legacy_links'), ('0010', 'public.accident_route_links'),
    ('0010', 'internal.r10_unresolved'), ('0010', 'internal.r10_llm_calls'), ('0010', 'internal.r10_llm_gates'),
    ('0011', 'internal.exposure_index')),
  blocks AS (
    SELECT rev, bool_and(to_regclass(rel) IS NOT NULL) AS complete, bool_or(to_regclass(rel) IS NOT NULL) AS present
    FROM objects GROUP BY rev)
  SELECT coalesce((SELECT string_agg(p, '; ' ORDER BY b.rev, p)
                    FROM blocks b CROSS JOIN LATERAL (VALUES
                      (CASE WHEN b.rev = '0004' AND NOT b.complete
                            THEN '0004 incomplete (run at 0004_phase2a_foundation or later)' END),
                      (CASE WHEN b.present AND NOT b.complete THEN b.rev || ' partial' END),
                      (CASE WHEN b.present AND EXISTS (SELECT 1 FROM blocks e WHERE e.rev < b.rev AND NOT e.complete)
                            THEN b.rev || ' present after an absent block' END)) AS v(p)), '')
         -- A rev dropped from the list above would leave its flag NULL, which \gset unsets and \if
         -- reads as false with only a warning: so the list itself must hold all eight.
         || CASE WHEN (SELECT array_agg(rev ORDER BY rev) FROM blocks)
                      = ARRAY['0004', '0005', '0006', '0007', '0008', '0009', '0010', '0011'] THEN ''
                 ELSE '; block list does not hold exactly 0004-0011' END
           AS problems,
         coalesce(bool_or(complete) FILTER (WHERE rev = '0005'), false) AS at_0005,
         coalesce(bool_or(complete) FILTER (WHERE rev = '0007'), false) AS at_0007,
         coalesce(bool_or(complete) FILTER (WHERE rev = '0008'), false) AS at_0008,
         coalesce(bool_or(complete) FILTER (WHERE rev = '0009'), false) AS at_0009,
         coalesce(bool_or(complete) FILTER (WHERE rev = '0010'), false) AS at_0010,
         coalesce(bool_or(complete) FILTER (WHERE rev = '0011'), false) AS at_0011
  FROM blocks
) s \gset
\if :blocks_ok
\else
  \warn 'grants_phase2.sql: migration blocks inconsistent with the schema:' :block_problems
  DO $$ BEGIN RAISE EXCEPTION 'grants_phase2.sql: STOP, no grant applied (see the line above)'; END $$;
\endif

GRANT USAGE ON SCHEMA internal TO ingest, analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA internal TO analyst;

-- Plan 1 (0004)
-- Only what plan 1's jobs read (R8 checks route ids; the loader reads route types).
GRANT SELECT ON public.mp_routes TO ingest;
-- Narrows a stray table-level SELECT (e.g. granted by hand, or by an older version of this
-- script) back down to the column set below on every rerun; REVOKE on a column grant alone is
-- a no-op, so this must run first.
REVOKE SELECT ON public.mp_ticks FROM ingest;
-- Exactly the columns R8 (mp_ticks_quarantine) reads: never climber_name.
GRANT SELECT (tick_id, route_id, tick_date, created_at, quarantine_reason, quarantine_rule_version)
  ON public.mp_ticks TO ingest;
GRANT UPDATE (quarantine_reason, quarantine_rule_version) ON public.mp_ticks TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.source_ingest_log TO ingest;
-- INSERT only (spec): accepted months are closed, so a reload never needs to change them.
GRANT SELECT, INSERT ON internal.mp_tick_aggregates TO ingest;
GRANT SELECT, INSERT ON internal.ingest_quarantine TO ingest;

-- Plan 2 (accident repair, PR 2a-1)
GRANT SELECT ON public.accidents TO ingest;
-- Narrows a table-level SELECT back to the columns the repair steps read, as for mp_ticks above.
REVOKE SELECT ON public.mp_locations FROM ingest;
GRANT SELECT (mp_id, latitude, longitude) ON public.mp_locations TO ingest;
-- Only the repair columns plus the coordinate trio and updated_at: never description, source,
-- source_id or accident_id, so a repair cannot rewrite a narrative or an incident's identity.
GRANT UPDATE (date, year, latitude, longitude, coordinates, country, date_precision, year_source, year_lo, year_hi,
              geocode_precision, geocode_method, activity_class, activity_rule_version, inclusion_flag,
              incident_group_id, is_canonical, severity_scale, excluded_reason, source_url, updated_at,
              exp_years_climbing, exp_stated_level, exp_first_season, guided, exp_rule_version)
  ON public.accidents TO ingest;
GRANT SELECT, INSERT ON internal.accident_revisions TO ingest;
-- R1/R2, R3 and R4 read their inputs from the frozen snapshot, so a repaired or hand-edited
-- live row never feeds a rule; never description or any other narrative column.
GRANT SELECT (accident_id, source, source_id, date, year, mountain, state, latitude, longitude, mp_route_id,
              activity)
  ON internal.accidents_raw TO ingest;

-- Plan 2 (0005, PR 2a-2)
-- The GNIS loader upserts and then deletes features GNIS dropped (SELECT for ON CONFLICT and
-- the DELETE's WHERE). R5 upserts its decisions and never deletes one: owner decisions stay.
\if :at_0005
GRANT SELECT, INSERT, UPDATE, DELETE ON public.gnis_summits TO ingest;
GRANT SELECT, INSERT, UPDATE ON internal.duplicate_decisions TO ingest;
\endif

-- Plan 2 (0006): no grant here. app and analyst read accidents_clean[_daily] through
-- migrator's default privileges (they cover views); ingest gets them in plan 3 (below).
-- trainer: nothing until Phase 3 (D13), which grants it the views together with LOGIN.

-- Plan 3 (0007), R7 and R11 are one block: tz is a 0007 column, and R7 (2a-3) and R11 (2a-4) run
-- at 0007, so their grants on the older accidents, area_weekly_weather and the id sequence wait for
-- 0007 too, never earlier.
\if :at_0007
-- DELETE on cell_daily_conditions is only for the 3-year prune of non-incident
-- series (era5_window); the registry and normals are upserted, never deleted. app and analyst
-- read the new tables and view through migrator's default privileges; trainer gets nothing.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cell_daily_conditions TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.grid_bucket_series, public.cell_normals_status,
  public.cell_climate_normals, public.cell_climatology TO ingest;
GRANT SELECT ON public.accidents_clean, public.accidents_clean_daily, public.accident_conditions TO ingest;

-- Plan 3 (local days): localday writes each accident's crag timezone. It reads accidents (table
-- SELECT, plan 2), mp_routes (plan 1) and mp_locations through the three-column grant above, so
-- no SELECT is widened here.
GRANT UPDATE (tz) ON public.accidents TO ingest;
\endif

-- 0013 drops the legacy weather table; this revoke clears a database that still has it (a fresh
-- one drops it at head, so the grant and the table are both gone).
DO $$
BEGIN
  IF to_regclass('public.weather') IS NOT NULL THEN
    REVOKE ALL ON public.weather FROM ingest;
  END IF;
END $$;

\if :at_0007
-- Plan 3 (R7): the one-time longitude sign fix reads and flips area_weekly_weather.longitude
-- only; never any other column.
GRANT SELECT ON public.area_weekly_weather TO ingest;
GRANT UPDATE (longitude) ON public.area_weekly_weather TO ingest;
\endif

-- Plan 3 (R11, PR 2a-4): the facts-only loader inserts new incidents, column by column, exactly
-- the columns accident_facts.INSERT_COLUMNS names (never accident_id, location, tags or
-- elevation_meters). description takes only the loader's own one-line summary (<= 200
-- characters, enforced by FactRow), never report text. The REVOKE first narrows a stray
-- table-level INSERT, as for mp_ticks above. UPDATE (mp_route_id) is --apply-route-ids: the
-- owner's later route link, only where it is NULL on an R11 row. No DELETE: incidents are never
-- deleted. The narrowing REVOKEs on tables older than their block (here and for routes/mountains
-- below) run at every revision, so a rerun clears a stray grant even before the block applies.
REVOKE INSERT ON public.accidents FROM ingest;
\if :at_0007
GRANT INSERT (source, source_id, date, year, date_precision, state, mountain, route, mp_route_id, latitude,
              longitude, geocode_precision, geocode_method, country, tz, activity, activity_class,
              activity_rule_version, inclusion_flag, accident_type, injury_severity, severity_scale,
              exp_years_climbing, exp_stated_level, exp_first_season, guided, exp_rule_version, source_url,
              description, updated_at)
  ON public.accidents TO ingest;
GRANT UPDATE (mp_route_id) ON public.accidents TO ingest;
-- accident_id comes from its column default; nextval() there needs USAGE (verified: the INSERT
-- fails "permission denied for sequence" without it). USAGE only: no SELECT/UPDATE (setval).
GRANT USAGE ON SEQUENCE public.accidents_accident_id_seq TO ingest;
\endif

-- MP catalog plan (0008). The refresh reads mp_locations' tree and coordinates, and the root
-- areas' names to recognise US states (never url); Phase 4 Task 24 makes the same widening.
-- The catalog is upserted, never deleted (FKs pin it to the MP rows). app and analyst read the
-- new tables and views through migrator's default privileges; trainer gets nothing (D13).
\if :at_0008
GRANT SELECT (parent_id, name) ON public.mp_locations TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.canonical_areas, public.canonical_routes TO ingest;
GRANT SELECT ON public.scorable_routes TO ingest;
-- Tick-source seam (DP9): loaders upsert it, never delete.
GRANT SELECT, INSERT, UPDATE ON internal.route_tick_scrapes TO ingest;
GRANT SELECT ON internal.route_tick_coverage, internal.route_tick_months TO ingest;
\endif

-- MP similarity features (0009). The points step inserts points and sets their timezone; the
-- elevation step updates them; the route step rebuilds route_static_features (DELETE only for
-- routes that became boulders). Reads the catalog and mp_routes through grants above.
\if :at_0009
GRANT SELECT, INSERT, UPDATE ON public.feature_points TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.route_static_features TO ingest;
GRANT SELECT ON public.scoring_unit_features TO ingest;
\endif

-- R10 on the MP catalog (0010). Reads the legacy link columns it needs, never anything else
-- of routes/mountains; never writes accidents. Owner decisions are kept: the unresolved table
-- has DELETE only for undecided rows a rule has since resolved (the job's WHERE).
-- Narrow any stray table-level grant back to the column sets below on every rerun (the mp_ticks /
-- mp_locations pattern; a column REVOKE alone does not remove a table-level grant).
REVOKE SELECT ON public.routes, public.mountains FROM ingest;
\if :at_0010
GRANT SELECT (route_id, name, grade, type, latitude, longitude, mp_route_id) ON public.routes TO ingest;
GRANT SELECT (mountain_id, name, latitude, longitude) ON public.mountains TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.accident_legacy_links TO ingest;
GRANT SELECT ON public.accident_route_links TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON internal.r10_unresolved TO ingest;
-- LLM pass (Task 15): the audit and the gates are append-only; a paid call is never rewritten.
GRANT SELECT, INSERT ON internal.r10_llm_calls, internal.r10_llm_gates TO ingest;
\endif

-- Exposure plan (0011). The exposure job clears, reinserts and prunes snapshots, never updates a
-- row; the REVOKE removes the UPDATE an earlier version of this script granted (2026-10-22).
-- analyst reads it through the schema-wide SELECT above; app holds nothing in internal; trainer
-- gets it in Phase 3 (D13), together with USAGE on internal.
\if :at_0011
REVOKE UPDATE ON internal.exposure_index FROM ingest;
GRANT SELECT, INSERT, DELETE ON internal.exposure_index TO ingest;
\endif

RESET ROLE;
COMMIT;
\echo 'phase 2 grants applied'
