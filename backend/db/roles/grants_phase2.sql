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
-- trainer: nothing until Phase 3 (D13).

RESET ROLE;
COMMIT;
\echo 'phase 2 grants applied'
