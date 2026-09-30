-- Run as the database owner, once, after create_roles.sql. Same mechanism as
-- create_roles.sql (PR #6): INGEST_PASSWORD comes via \getenv, never argv, and reaches the
-- server as plaintext over verify-full TLS; the server stores SCRAM-SHA-256.
-- Create roles only through this file: neonctl / Console / API roles join neon_superuser.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never

-- Secret-free, so a rerun stops here instead of sending a CREATE ROLE ... PASSWORD that
-- fails and lands in the server log via log_min_error_statement.
SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('ingest', 'trainer')) AS roles_absent \gset
\if :roles_absent
\else
  DO $$ BEGIN RAISE EXCEPTION 'ingest/trainer already exist; do not rerun create_roles_phase2.sql'; END $$;
\endif

SELECT to_regrole('migrator') IS NOT NULL AND to_regrole('app') IS NOT NULL AND to_regrole('analyst') IS NOT NULL
  AS phase1_roles_ok \gset
\if :phase1_roles_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'run create_roles.sql first (migrator, app, analyst must exist)'; END $$;
\endif

-- CREATE SCHEMA ... AUTHORIZATION migrator needs SET on migrator, which the Phase 1 relaunch
-- step (REVOKE migrator FROM CURRENT_USER) removes; INHERIT FALSE restores it without
-- handing the owner migrator's table access again.
SELECT pg_has_role(current_user, 'migrator', 'SET') AS owner_can_set_migrator \gset
\if :owner_can_set_migrator
\else
  DO $$ BEGIN RAISE EXCEPTION 'owner needs SET on migrator: GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT FALSE'; END $$;
\endif

\getenv ingest_password INGEST_PASSWORD
\if :{?ingest_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'INGEST_PASSWORD is not set'; END $$;
\endif

-- A value shaped like a SCRAM or md5 hash is stored as that hash (Neon rejects it at
-- COMMIT); write_role_url --generate-password makes 64 hex chars.
SELECT length(:'ingest_password') >= 32
   AND :'ingest_password' !~ '^SCRAM-SHA-256\$'
   AND :'ingest_password' !~ '^md5[0-9a-f]{32}$' AS password_ok \gset
\if :password_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'INGEST_PASSWORD must be a plaintext password of at least 32 characters, not a SCRAM or md5 verifier'; END $$;
\endif

BEGIN;

CREATE ROLE ingest LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'ingest_password';
-- D13: a placeholder with no login and no grants. default_transaction_read_only is a session
-- default any client can override, so it is not a boundary; Phase 3 adds LOGIN and SELECT on
-- training views only.
CREATE ROLE trainer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

-- migrator cannot create schemas (verify_roles.sql asserts it), so the owner does it here.
CREATE SCHEMA IF NOT EXISTS internal AUTHORIZATION migrator;
-- As migrator: an owner holding SET but not INHERIT (post-relaunch) is not treated as the
-- schema owner for REVOKE.
SET ROLE migrator;
REVOKE ALL ON SCHEMA internal FROM PUBLIC;
RESET ROLE;

GRANT USAGE ON SCHEMA public TO ingest;

COMMIT;

\echo 'roles ingest and trainer created; schema internal owned by migrator'
