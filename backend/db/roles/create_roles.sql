-- Run as the database owner. MIGRATOR_PASSWORD / APP_PASSWORD come via \getenv, never argv,
-- and reach the server as plaintext over verify-full TLS; the server stores SCRAM-SHA-256.
-- Create roles only through this file: neonctl / Console / API roles join neon_superuser.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never

-- Secret-free, so a rerun stops here instead of sending a CREATE ROLE ... PASSWORD that
-- fails and lands in the server log via log_min_error_statement.
SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('migrator', 'app')) AS roles_absent \gset
\if :roles_absent
\else
  DO $$ BEGIN RAISE EXCEPTION 'migrator/app already exist; do not rerun create_roles.sql'; END $$;
\endif

\getenv migrator_password MIGRATOR_PASSWORD
\getenv app_password APP_PASSWORD
\if :{?migrator_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'MIGRATOR_PASSWORD is not set'; END $$;
\endif
\if :{?app_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'APP_PASSWORD is not set'; END $$;
\endif

-- Postgres stores a value shaped like a SCRAM or md5 hash as that hash rather than hashing
-- it, so a leftover verifier would become a password nobody knows (plain Postgres) or be
-- rejected at COMMIT (Neon). write_role_url --generate-password makes 64 hex chars.
SELECT bool_and(length(pw) >= 32 AND pw !~ '^SCRAM-SHA-256\$' AND pw !~ '^md5[0-9a-f]{32}$') AS passwords_ok
  FROM (VALUES (:'migrator_password'), (:'app_password')) AS v(pw) \gset
\if :passwords_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'MIGRATOR_PASSWORD / APP_PASSWORD must be plaintext passwords of at least 32 characters, not SCRAM or md5 verifiers'; END $$;
\endif

SELECT current_setting('server_version_num')::int >= 160000 AS pg16_or_newer \gset
\if :pg16_or_newer
\else
  DO $$ BEGIN RAISE EXCEPTION 'GRANT ... WITH SET below needs PostgreSQL 16+'; END $$;
\endif

BEGIN;

CREATE ROLE migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'migrator_password';
CREATE ROLE app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'app_password';

-- ALTER ... OWNER TO migrator needs SET on migrator. INHERIT keeps services that still
-- connect as the owner working until the relaunch gate revokes this membership.
GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT TRUE;

GRANT USAGE, CREATE ON SCHEMA public TO migrator;
GRANT USAGE ON SCHEMA public TO app;

-- Prod's owner carries an older default SELECT grant to analyst (seen 2026-09-28), which
-- verify_roles.sql rejects: migrator's default grant below replaces it. CURRENT_USER, not
-- neondb_owner, so this is a no-op wherever no such grant exists (local, CI).
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public REVOKE ALL ON TABLES FROM analyst;

DO $$
DECLARE
  obj record;
BEGIN
  FOR obj IN
    SELECT c.oid::regclass AS name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO migrator',
      CASE obj.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
      obj.name);
  END LOOP;

  -- Sequences owned by a column follow their table; only standalone ones move here.
  FOR obj IN
    SELECT c.oid::regclass AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('a', 'i', 'e'))
  LOOP
    EXECUTE format('ALTER SEQUENCE %s OWNER TO migrator', obj.name);
  END LOOP;

  FOR obj IN
    SELECT p.oid::regprocedure AS name
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO migrator', obj.name);
  END LOOP;

  FOR obj IN
    SELECT t.oid::regtype AS name
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public'
      AND t.typtype IN ('e', 'd')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER TYPE %s OWNER TO migrator', obj.name);
  END LOOP;
END $$;

SET ROLE migrator;

-- The app only reads, except for the nightly historical_predictions upsert and purge
-- (app/tasks/safety_computation_optimized.py). nextval needs USAGE on the id sequence.
DO $$
DECLARE
  obj record;
BEGIN
  FOR obj IN
    SELECT c.oid::regclass AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm')
      AND c.relname <> 'alembic_version'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('GRANT SELECT ON %s TO app', obj.name);
  END LOOP;
END $$;

GRANT INSERT, UPDATE, DELETE ON public.historical_predictions TO app;
GRANT USAGE ON SEQUENCE public.historical_predictions_id_seq TO app;

-- New tables are read-only to app; the migration that needs app writes grants them per table.
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO analyst;

RESET ROLE;

COMMIT;

\echo 'roles migrator and app created'
