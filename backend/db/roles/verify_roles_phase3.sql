-- Read-only exact-privilege checks for Phase 3: trainer everywhere, and every grant in schema model.
-- Exits non-zero on any failure. Run as the owner after grants_phase3.sql, alongside verify_roles.sql
-- and verify_roles_phase2.sql (which still owns ingest, and app/analyst outside model). The grant
-- script, this file and tests/test_roles_phase3.py change together.
\set ON_ERROR_STOP on

CREATE TEMP TABLE role_checks (check_name text PRIMARY KEY, ok boolean NOT NULL);

-- Mirrors grants_phase3.sql exactly. Table-level only: trainer holds no column grant anywhere,
-- and no PUBLIC grant may widen it.
CREATE TEMP TABLE trainer_privs (tbl text, priv text);
INSERT INTO trainer_privs
SELECT t, 'SELECT' FROM unnest(ARRAY[
  'public.accidents_clean', 'public.accident_route_links', 'public.canonical_areas', 'public.canonical_routes',
  'public.scorable_routes', 'public.scoring_unit_features', 'public.historical_predictions',
  'internal.exposure_index', 'model.registry', 'model.route_scores']) AS t;
INSERT INTO trainer_privs VALUES
  ('model.registry', 'INSERT'), ('model.registry', 'UPDATE'),
  ('model.route_scores', 'INSERT'), ('model.route_scores', 'DELETE');

-- Extension-owned relations (PostGIS's PUBLIC SELECT on spatial_ref_sys) are skipped, as in
-- verify_roles.sql. By oid throughout: the owner running this may hold no USAGE on internal or model.
CREATE TEMP TABLE rels AS
SELECT c.oid, n.nspname || '.' || c.relname AS tbl, n.nspname AS schema, c.relkind, c.relowner, c.relacl
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('public', 'internal', 'model') AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e');

INSERT INTO role_checks VALUES
  ('trainer can log in', (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer')),
  ('trainer connection limit is 4', (SELECT rolconnlimit = 4 FROM pg_roles WHERE rolname = 'trainer')),
  ('trainer has USAGE on schema public', has_schema_privilege('trainer', 'public', 'USAGE')),
  ('trainer has USAGE on schema internal', has_schema_privilege('trainer', 'internal', 'USAGE')),
  ('trainer has USAGE on schema model', has_schema_privilege('trainer', 'model', 'USAGE')),
  ('app has USAGE on schema model', has_schema_privilege('app', 'model', 'USAGE')),
  ('analyst has USAGE on schema model', has_schema_privilege('analyst', 'model', 'USAGE')),
  ('ingest has no USAGE on schema model', NOT has_schema_privilege('ingest', 'model', 'USAGE')),
  -- PG16 lets CREATE without USAGE create tables in a schema, so CREATE is checked for every role.
  ('trainer cannot CREATE in model', NOT has_schema_privilege('trainer', 'model', 'CREATE')),
  ('app cannot CREATE in model', NOT has_schema_privilege('app', 'model', 'CREATE')),
  ('analyst cannot CREATE in model', NOT has_schema_privilege('analyst', 'model', 'CREATE')),
  ('ingest cannot CREATE in model', NOT has_schema_privilege('ingest', 'model', 'CREATE')),
  ('schema model is owned by migrator',
     (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'model') = 'migrator'),
  ('schema model grants only USAGE to trainer, app and analyst',
     NOT EXISTS (
       SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
       WHERE n.nspname = 'model' AND a.grantee <> n.nspowner
         AND NOT (a.grantee IN (to_regrole('trainer'), to_regrole('app'), to_regrole('analyst'))
                  AND a.privilege_type = 'USAGE' AND NOT a.is_grantable))),
  ('PUBLIC holds nothing on schema model or its relations',
     NOT EXISTS (SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
                 WHERE n.nspname = 'model' AND a.grantee = 0)
     AND NOT EXISTS (SELECT 1 FROM rels r CROSS JOIN LATERAL aclexplode(r.relacl) a
                     WHERE r.schema = 'model' AND a.grantee = 0)),
  -- A default ACL would hand the next model table to someone without a line in grants_phase3.sql.
  ('no default privileges apply to new model relations',
     NOT EXISTS (SELECT 1 FROM pg_default_acl d
                 WHERE d.defaclnamespace = 'model'::regnamespace
                    OR (d.defaclnamespace = 0 AND d.defaclrole = to_regrole('migrator')))),
  ('no default privileges grant trainer anything',
     NOT EXISTS (SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
                 WHERE a.grantee = to_regrole('trainer')));

INSERT INTO role_checks
SELECT 'trainer allowlist relation exists: ' || t, EXISTS (SELECT 1 FROM rels WHERE tbl = t)
FROM (SELECT DISTINCT tbl AS t FROM trainer_privs) e
UNION ALL
SELECT 'model relation exists: ' || t, EXISTS (SELECT 1 FROM rels WHERE tbl = t)
FROM unnest(ARRAY['model.registry', 'model.route_scores']) AS t;

-- trainer, per relation across public, internal and model: privileges exactly the allowlist.
-- has_table_privilege misses column grants, so the column-capable privileges are also checked with
-- has_any_column_privilege, which counts trainer's own, PUBLIC's and table-level grants alike.
WITH privs AS (
  SELECT r.tbl, p.priv, has_table_privilege('trainer', r.oid, p.priv) AS held
  FROM rels r
  CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']) AS p(priv)
  WHERE r.relkind <> 'S'
  UNION ALL
  SELECT r.tbl, p.priv, has_any_column_privilege('trainer', r.oid, p.priv)
  FROM rels r CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) AS p(priv)
  WHERE r.relkind <> 'S'
)
INSERT INTO role_checks
SELECT 'trainer privileges match the allowlist on ' || tbl,
       bool_and(held = EXISTS (SELECT 1 FROM trainer_privs e WHERE e.tbl = privs.tbl AND e.priv = privs.priv))
FROM privs GROUP BY tbl;

INSERT INTO role_checks
SELECT 'trainer has nothing on sequence ' || tbl,
       NOT (has_sequence_privilege('trainer', oid, 'USAGE') OR has_sequence_privilege('trainer', oid, 'SELECT')
            OR has_sequence_privilege('trainer', oid, 'UPDATE'))
FROM rels WHERE relkind = 'S';

-- Every model relation: app and analyst read only, ingest nothing (has_any_column_privilege also
-- sees column grants), owned by migrator, and an ACL holding no grantee outside the expected set.
INSERT INTO role_checks
SELECT g || ' holds only SELECT on ' || r.tbl,
       has_table_privilege(g, r.oid, 'SELECT')
       AND NOT (has_table_privilege(g, r.oid, 'INSERT') OR has_table_privilege(g, r.oid, 'UPDATE')
                OR has_table_privilege(g, r.oid, 'DELETE') OR has_table_privilege(g, r.oid, 'TRUNCATE')
                OR has_table_privilege(g, r.oid, 'REFERENCES') OR has_table_privilege(g, r.oid, 'TRIGGER')
                OR has_any_column_privilege(g, r.oid, 'INSERT') OR has_any_column_privilege(g, r.oid, 'UPDATE')
                OR has_any_column_privilege(g, r.oid, 'REFERENCES'))
FROM rels r CROSS JOIN unnest(ARRAY['app', 'analyst']) AS g
WHERE r.schema = 'model' AND r.relkind <> 'S'
UNION ALL
SELECT 'ingest holds nothing on ' || r.tbl,
       NOT (has_any_column_privilege('ingest', r.oid, 'SELECT') OR has_any_column_privilege('ingest', r.oid, 'INSERT')
            OR has_any_column_privilege('ingest', r.oid, 'UPDATE') OR has_any_column_privilege('ingest', r.oid, 'REFERENCES')
            OR has_table_privilege('ingest', r.oid, 'DELETE') OR has_table_privilege('ingest', r.oid, 'TRUNCATE')
            OR has_table_privilege('ingest', r.oid, 'TRIGGER'))
FROM rels r WHERE r.schema = 'model' AND r.relkind <> 'S'
UNION ALL
SELECT 'model relation owned by migrator: ' || r.tbl, pg_get_userbyid(r.relowner) = 'migrator'
FROM rels r WHERE r.schema = 'model'
UNION ALL
SELECT r.tbl || ' grants only the expected privileges',
       NOT EXISTS (
         SELECT 1 FROM aclexplode(coalesce(r.relacl, acldefault('r', r.relowner))) a
         WHERE a.grantee <> r.relowner
           AND NOT (NOT a.is_grantable
                    AND ((a.grantee IN (to_regrole('app'), to_regrole('analyst')) AND a.privilege_type = 'SELECT')
                         OR (a.grantee = to_regrole('trainer')
                             AND EXISTS (SELECT 1 FROM trainer_privs e
                                         WHERE e.tbl = r.tbl AND e.priv = a.privilege_type)))))
       AND NOT EXISTS (SELECT 1 FROM pg_attribute att CROSS JOIN LATERAL aclexplode(att.attacl) x
                       WHERE att.attrelid = r.oid)
FROM rels r WHERE r.schema = 'model';

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN RAISE EXCEPTION 'PHASE 3 ROLE CHECKS FAILED: %', failed; END IF;
END $$;

\echo 'ALL PHASE 3 ROLE CHECKS PASSED'
