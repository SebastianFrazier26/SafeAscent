-- Phase 3 grants (idempotent). Run as migrator (or an owner holding SET on migrator) after every Phase 3
-- migration and after grants_phase2.sql, then verify_roles_phase3.sql. Cumulative, like grants_phase2.sql.
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

-- Narrow first so a rerun removes stray grants on model tables. Strays elsewhere are not
-- narrowed here; verify_roles_phase3.sql fails on them.
REVOKE ALL ON ALL TABLES IN SCHEMA model FROM PUBLIC, trainer, app, analyst, ingest;
REVOKE ALL ON SCHEMA model FROM PUBLIC, ingest;
GRANT USAGE ON SCHEMA model TO trainer, app, analyst;
GRANT SELECT ON model.registry, model.route_scores TO app, analyst;
-- registry rows are inserted and moved through the state machine, never deleted (prune nulls the
-- bundle, keeps the row); a version's scores are inserted whole and pruned, never updated.
GRANT SELECT, INSERT, UPDATE ON model.registry TO trainer;
GRANT SELECT, INSERT, DELETE ON model.route_scores TO trainer;

-- Training reads (allowlist, DP17): views without narratives, the catalog, static features, the
-- kernel's history for the prospective backtest, and the exposure snapshots (X-conflict 1).
GRANT USAGE ON SCHEMA public, internal TO trainer;
GRANT SELECT ON public.accidents_clean, public.accident_route_links, public.canonical_areas,
  public.canonical_routes, public.scorable_routes, public.scoring_unit_features,
  public.historical_predictions TO trainer;
GRANT SELECT ON internal.exposure_index TO trainer;

RESET ROLE;
COMMIT;
\echo 'phase 3 grants applied'
