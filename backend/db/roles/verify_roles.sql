-- Read-only checks. Exits non-zero (ON_ERROR_STOP) if any check fails.
\set ON_ERROR_STOP on

CREATE TEMP TABLE role_checks (check_name text PRIMARY KEY, ok boolean NOT NULL);

INSERT INTO role_checks
SELECT 'role exists: ' || r, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
FROM unnest(ARRAY['analyst', 'migrator', 'app']) AS r;

INSERT INTO role_checks
SELECT 'no superuser/createdb/createrole: ' || rolname, NOT (rolsuper OR rolcreatedb OR rolcreaterole)
FROM pg_roles WHERE rolname IN ('analyst', 'migrator', 'app');

INSERT INTO role_checks
SELECT 'noinherit: ' || rolname, NOT rolinherit
FROM pg_roles WHERE rolname IN ('migrator', 'app');

INSERT INTO role_checks
SELECT 'no role memberships (pg_auth_members empty): ' || r,
       NOT EXISTS (
         SELECT 1 FROM pg_auth_members m JOIN pg_roles pr ON pr.oid = m.member WHERE pr.rolname = r)
FROM unnest(ARRAY['analyst', 'migrator', 'app']) AS r;

INSERT INTO role_checks VALUES
  ('migrator can CREATE in public', has_schema_privilege('migrator', 'public', 'CREATE')),
  ('app cannot CREATE in public', NOT has_schema_privilege('app', 'public', 'CREATE')),
  ('analyst cannot CREATE in public', NOT has_schema_privilege('analyst', 'public', 'CREATE')),
  ('app cannot CREATE schemas in the database', NOT has_database_privilege('app', current_database(), 'CREATE')),
  ('app cannot write alembic_version',
     CASE WHEN to_regclass('public.alembic_version') IS NULL THEN true
          ELSE NOT has_table_privilege('app', to_regclass('public.alembic_version'), 'UPDATE') END);

WITH app_tables AS (
  SELECT c.oid, c.oid::regclass::text AS name
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND c.relkind IN ('r', 'p')
    AND c.relname <> 'alembic_version'
    AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
)
INSERT INTO role_checks
SELECT 'migrator owns ' || name, pg_get_userbyid(c.relowner) = 'migrator'
  FROM app_tables t JOIN pg_class c ON c.oid = t.oid
UNION ALL
-- has_table_privilege with a list is true if ANY is held, so each privilege is separate.
SELECT 'app has DML on ' || name,
       has_table_privilege('app', oid, 'SELECT') AND has_table_privilege('app', oid, 'INSERT')
       AND has_table_privilege('app', oid, 'UPDATE') AND has_table_privilege('app', oid, 'DELETE')
  FROM app_tables
UNION ALL
SELECT 'app lacks TRUNCATE/REFERENCES/TRIGGER on ' || name,
       NOT has_table_privilege('app', oid, 'TRUNCATE') AND NOT has_table_privilege('app', oid, 'REFERENCES')
       AND NOT has_table_privilege('app', oid, 'TRIGGER')
  FROM app_tables
UNION ALL
SELECT 'analyst cannot INSERT into ' || name, NOT has_table_privilege('analyst', oid, 'INSERT')
  FROM app_tables;

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE
  failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN
    RAISE EXCEPTION 'ROLE CHECKS FAILED: %', failed;
  END IF;
END $$;

\echo 'ALL ROLE CHECKS PASSED'
