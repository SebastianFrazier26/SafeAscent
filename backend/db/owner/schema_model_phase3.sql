-- Phase 3 (DP1): the model schema. Run once as the owner before `alembic upgrade 0012_model_registry`;
-- migrator cannot CREATE schemas (verify_roles.sql asserts it).
-- The owner must be able to SET ROLE migrator: GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT FALSE.
\set ON_ERROR_STOP on
CREATE SCHEMA IF NOT EXISTS model AUTHORIZATION migrator;
SET ROLE migrator;
REVOKE ALL ON SCHEMA model FROM PUBLIC;
RESET ROLE;
\echo 'schema model owned by migrator'
