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
GRANT SELECT ON public.accidents, public.mp_routes, public.mp_locations TO ingest;
-- Exactly the columns R8 (mp_ticks_quarantine) reads: never climber_name.
GRANT SELECT (tick_id, route_id, tick_date, created_at, quarantine_reason, quarantine_rule_version)
  ON public.mp_ticks TO ingest;
GRANT UPDATE (quarantine_reason, quarantine_rule_version) ON public.mp_ticks TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.source_ingest_log TO ingest;
-- INSERT only (spec): accepted months are closed, so a reload never needs to change them.
GRANT SELECT, INSERT ON internal.mp_tick_aggregates TO ingest;
GRANT SELECT, INSERT ON internal.ingest_quarantine TO ingest;
-- trainer: nothing until Phase 3 (D13).

RESET ROLE;
COMMIT;
\echo 'phase 2 grants applied'
