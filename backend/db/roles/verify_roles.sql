-- Read-only checks. Exits non-zero (ON_ERROR_STOP) if any check fails.
-- Run as the same owner that ran create_roles.sql (membership checks compare to CURRENT_USER).
\set ON_ERROR_STOP on

CREATE TEMP TABLE role_checks (check_name text PRIMARY KEY, ok boolean NOT NULL);

INSERT INTO role_checks
SELECT 'role exists: ' || r, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
FROM unnest(ARRAY['analyst', 'migrator', 'app']) AS r;

INSERT INTO role_checks
SELECT 'no superuser/createdb/createrole/replication/bypassrls: ' || rolname,
       NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
FROM pg_roles WHERE rolname IN ('analyst', 'migrator', 'app');

INSERT INTO role_checks
SELECT 'noinherit: ' || rolname, NOT rolinherit
FROM pg_roles WHERE rolname IN ('migrator', 'app');

INSERT INTO role_checks
SELECT 'no role memberships (pg_auth_members empty): ' || r,
       NOT EXISTS (
         SELECT 1 FROM pg_auth_members m JOIN pg_roles pr ON pr.oid = m.member WHERE pr.rolname = r)
FROM unnest(ARRAY['analyst', 'migrator', 'app']) AS r;

-- PG16 gives a CREATEROLE creator an ADMIN grant with SET and INHERIT off, which confers
-- no privileges; any grant that can SET or INHERIT the role counts as membership here.
INSERT INTO role_checks VALUES
  ('only CURRENT_USER is a member of migrator',
     NOT EXISTS (
       SELECT 1 FROM pg_auth_members m
       WHERE m.roleid = 'migrator'::regrole AND m.member <> CURRENT_USER::regrole)),
  ('nobody can SET or INHERIT app',
     NOT EXISTS (
       SELECT 1 FROM pg_auth_members m
       WHERE m.roleid = 'app'::regrole AND (m.set_option OR m.inherit_option))),
  ('nobody but CURRENT_USER holds ADMIN on app',
     NOT EXISTS (
       SELECT 1 FROM pg_auth_members m
       WHERE m.roleid = 'app'::regrole AND m.member <> CURRENT_USER::regrole));

INSERT INTO role_checks VALUES
  ('migrator can CREATE in public', has_schema_privilege('migrator', 'public', 'CREATE')),
  ('app cannot CREATE in public', NOT has_schema_privilege('app', 'public', 'CREATE')),
  ('analyst cannot CREATE in public', NOT has_schema_privilege('analyst', 'public', 'CREATE')),
  ('app cannot CREATE schemas in the database', NOT has_database_privilege('app', current_database(), 'CREATE')),
  ('migrator cannot CREATE schemas in the database',
     NOT has_database_privilege('migrator', current_database(), 'CREATE')),
  ('analyst cannot CREATE schemas in the database',
     NOT has_database_privilege('analyst', current_database(), 'CREATE')),
  ('app cannot read or write alembic_version',
     CASE WHEN to_regclass('public.alembic_version') IS NULL THEN true
          ELSE NOT (has_table_privilege('app', to_regclass('public.alembic_version'), 'SELECT')
                    OR has_table_privilege('app', to_regclass('public.alembic_version'), 'INSERT')
                    OR has_table_privilege('app', to_regclass('public.alembic_version'), 'UPDATE')
                    OR has_table_privilege('app', to_regclass('public.alembic_version'), 'DELETE')) END);

WITH app_tables AS (
  SELECT c.oid, c.oid::regclass::text AS name, c.relname = 'historical_predictions' AS app_writes
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
SELECT 'app can SELECT ' || name, has_table_privilege('app', oid, 'SELECT')
  FROM app_tables
UNION ALL
-- has_table_privilege with a list is true if ANY is held, so each privilege is separate.
SELECT CASE WHEN app_writes THEN 'app has INSERT/UPDATE/DELETE on ' ELSE 'app has no INSERT/UPDATE/DELETE on ' END || name,
       (has_table_privilege('app', oid, 'INSERT') AND has_table_privilege('app', oid, 'UPDATE')
        AND has_table_privilege('app', oid, 'DELETE')) = app_writes
       AND (has_table_privilege('app', oid, 'INSERT') OR has_table_privilege('app', oid, 'UPDATE')
            OR has_table_privilege('app', oid, 'DELETE')) = app_writes
  FROM app_tables
UNION ALL
SELECT 'app lacks TRUNCATE/REFERENCES/TRIGGER on ' || name,
       NOT has_table_privilege('app', oid, 'TRUNCATE') AND NOT has_table_privilege('app', oid, 'REFERENCES')
       AND NOT has_table_privilege('app', oid, 'TRIGGER')
  FROM app_tables
UNION ALL
SELECT 'analyst cannot INSERT/UPDATE/DELETE/TRUNCATE ' || name,
       NOT (has_table_privilege('analyst', oid, 'INSERT') OR has_table_privilege('analyst', oid, 'UPDATE')
            OR has_table_privilege('analyst', oid, 'DELETE') OR has_table_privilege('analyst', oid, 'TRUNCATE'))
  FROM app_tables;

WITH app_sequences AS (
  SELECT c.oid, c.oid::regclass::text AS name, c.relname = 'historical_predictions_id_seq' AS app_uses
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND c.relkind = 'S'
    AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
)
INSERT INTO role_checks
SELECT CASE WHEN app_uses THEN 'app has only USAGE on ' ELSE 'app has nothing on ' END || name,
       has_sequence_privilege('app', oid, 'USAGE') = app_uses
       AND NOT has_sequence_privilege('app', oid, 'SELECT')
       AND NOT has_sequence_privilege('app', oid, 'UPDATE')
  FROM app_sequences
UNION ALL
SELECT 'analyst cannot UPDATE sequence ' || name, NOT has_sequence_privilege('analyst', oid, 'UPDATE')
  FROM app_sequences;

WITH granted AS (
  SELECT d.defaclobjtype AS objtype, pg_get_userbyid(a.grantee) AS grantee, a.privilege_type AS priv
  FROM pg_default_acl d
  CROSS JOIN LATERAL aclexplode(d.defaclacl) a
  WHERE d.defaclrole = 'migrator'::regrole
    AND d.defaclnamespace = 'public'::regnamespace
    AND a.grantee <> d.defaclrole
),
expected (objtype, grantee, priv) AS (
  VALUES ('r'::"char", 'app', 'SELECT'), ('r'::"char", 'analyst', 'SELECT')
)
INSERT INTO role_checks VALUES
  ('default privileges for migrator in public are exactly SELECT to app and analyst',
     NOT EXISTS (SELECT * FROM granted EXCEPT SELECT * FROM expected)
     AND NOT EXISTS (SELECT * FROM expected EXCEPT SELECT * FROM granted)),
  ('no other default privileges grant app or analyst anything',
     NOT EXISTS (
       SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
       WHERE a.grantee IN ('app'::regrole, 'analyst'::regrole)
         AND NOT (d.defaclrole = 'migrator'::regrole AND d.defaclnamespace = 'public'::regnamespace)));

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
