-- Run as the database owner. The environment carries SCRAM-SHA-256 verifiers
-- (python -m scripts.write_role_url --role <r> --scram), never plaintext passwords, so no
-- password reaches the server, its logs, or pg_stat_statements. Values come via \getenv, never argv.
-- Create roles only through this file: neonctl / Console / API roles join neon_superuser.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never

\getenv migrator_scram MIGRATOR_PASSWORD_SCRAM
\getenv app_scram APP_PASSWORD_SCRAM
\if :{?migrator_scram}
\else
  DO $$ BEGIN RAISE EXCEPTION 'MIGRATOR_PASSWORD_SCRAM is not set'; END $$;
\endif
\if :{?app_scram}
\else
  DO $$ BEGIN RAISE EXCEPTION 'APP_PASSWORD_SCRAM is not set'; END $$;
\endif

-- Postgres hashes anything that is not a verifier as a plaintext password, so a
-- password pasted into these variables would be sent (and stored) as one.
SELECT :'migrator_scram' ~ '^SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$'
   AND :'app_scram' ~ '^SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$'
   AS verifiers_ok \gset
\if :verifiers_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'MIGRATOR_PASSWORD_SCRAM / APP_PASSWORD_SCRAM must be SCRAM-SHA-256 verifiers'; END $$;
\endif

SELECT current_setting('server_version_num')::int >= 160000 AS pg16_or_newer \gset
\if :pg16_or_newer
\else
  DO $$ BEGIN RAISE EXCEPTION 'GRANT ... WITH SET below needs PostgreSQL 16+'; END $$;
\endif

BEGIN;

CREATE ROLE migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'migrator_scram';
CREATE ROLE app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'app_scram';

-- ALTER ... OWNER TO migrator needs SET on migrator. INHERIT keeps services that still
-- connect as the owner working until the relaunch gate revokes this membership.
GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT TRUE;

GRANT USAGE, CREATE ON SCHEMA public TO migrator;
GRANT USAGE ON SCHEMA public TO app;

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
