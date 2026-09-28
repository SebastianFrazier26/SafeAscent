-- Run as the database owner. Passwords come from the environment (\getenv), never argv.
-- Create roles only through this file: neonctl / Console / API roles join neon_superuser.
\set ON_ERROR_STOP on
-- terse + no context keeps failing statements (which contain passwords) out of the output.
\set VERBOSITY terse
\set SHOW_CONTEXT never

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
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO app', obj.name);
  END LOOP;

  FOR obj IN
    SELECT c.oid::regclass AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO app', obj.name);
  END LOOP;
END $$;

ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO analyst;

RESET ROLE;

COMMIT;

\echo 'roles migrator and app created'
