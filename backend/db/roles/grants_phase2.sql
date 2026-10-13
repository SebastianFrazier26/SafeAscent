-- Idempotent Phase 2 grants, run after every Phase 2 migration and then
-- verify_roles_phase2.sql. Run it as migrator directly, or as the owner while it holds SET on
-- migrator (SET ROLE migrator is a no-op for migrator itself): GRANT on objects migrator owns
-- must come from migrator. Cumulative: later plans append their grants inside the transaction.
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
GRANT SELECT, INSERT, UPDATE, DELETE ON public.gnis_summits TO ingest;
GRANT SELECT, INSERT, UPDATE ON internal.duplicate_decisions TO ingest;

-- Plan 2 (0006): no grant here. app and analyst read accidents_clean[_daily] through
-- migrator's default privileges (they cover views); ingest gets them in plan 3 (below).
-- trainer: nothing until Phase 3 (D13), which grants it the views together with LOGIN.

-- Plan 3 (0007). DELETE on cell_daily_conditions is only for the 3-year prune of non-incident
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

-- Plan 3 (R6 verify): r6_verify's CLI logs the ERA5-vs-legacy agreement as ingest, reading the
-- legacy weather table; never written.
GRANT SELECT ON public.weather TO ingest;

-- Plan 3 (R7): the one-time longitude sign fix reads and flips area_weekly_weather.longitude
-- only; never any other column.
GRANT SELECT ON public.area_weekly_weather TO ingest;
GRANT UPDATE (longitude) ON public.area_weekly_weather TO ingest;

-- Plan 3 (R11, PR 2a-4): the facts-only loader inserts new incidents, column by column, exactly
-- the columns accident_facts.INSERT_COLUMNS names (never accident_id, location, tags or
-- elevation_meters). description takes only the loader's own one-line summary (<= 200
-- characters, enforced by FactRow), never report text. The REVOKE first narrows a stray
-- table-level INSERT, as for mp_ticks above. UPDATE (mp_route_id) is --apply-route-ids: the
-- owner's later route link, only where it is NULL on an R11 row. No DELETE: incidents are never
-- deleted.
REVOKE INSERT ON public.accidents FROM ingest;
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

-- MP catalog plan (0008). The refresh reads mp_locations' tree and coordinates, and the root
-- areas' names to recognise US states (never url); Phase 4 Task 24 makes the same widening.
-- The catalog is upserted, never deleted (FKs pin it to the MP rows). app and analyst read the
-- new tables and views through migrator's default privileges; trainer gets nothing (D13).
GRANT SELECT (parent_id, name) ON public.mp_locations TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.canonical_areas, public.canonical_routes TO ingest;
GRANT SELECT ON public.scorable_routes TO ingest;
-- Tick-source seam (DP9): loaders upsert it, never delete.
GRANT SELECT, INSERT, UPDATE ON internal.route_tick_scrapes TO ingest;
GRANT SELECT ON internal.route_tick_coverage, internal.route_tick_months TO ingest;

RESET ROLE;
COMMIT;
\echo 'phase 2 grants applied'
