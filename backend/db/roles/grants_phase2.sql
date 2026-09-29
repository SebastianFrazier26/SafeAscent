-- Idempotent Phase 2 grants. Run as the owner after every Phase 2 migration, then
-- verify_roles_phase2.sql. GRANT on objects migrator owns needs SET ROLE migrator.
-- Cumulative: later plans append their grants inside the transaction.
\set ON_ERROR_STOP on
BEGIN;
SET ROLE migrator;

GRANT USAGE ON SCHEMA internal TO ingest, analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA internal TO analyst;

-- Plan 1 (0004)
GRANT SELECT ON public.accidents, public.mp_routes, public.mp_locations, public.mp_ticks TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.source_ingest_log TO ingest;
GRANT UPDATE (quarantine_reason, quarantine_rule_version) ON public.mp_ticks TO ingest;
-- INSERT only (spec): accepted months are closed, so a reload never needs to change them.
GRANT SELECT, INSERT ON internal.mp_tick_aggregates TO ingest;
GRANT SELECT, INSERT ON internal.ingest_quarantine TO ingest;
-- trainer: nothing until Phase 3 (D13).

RESET ROLE;
COMMIT;
\echo 'phase 2 grants applied'
