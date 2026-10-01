-- Task 12 Step 2 / 6b pre-check, read-only, as analyst. The output is saved on the branch and
-- diffed against prod, so nothing here may depend on the run date (the future-date check
-- stays inline in the runbook). regexp_replace '^\s+|\s+$' strips all whitespace, as
-- Python's str.strip() does in sources.source_family / dates.source_scheme; btrim strips
-- spaces only. tests/test_p2a1_precheck_sql.py holds both sides to the same answer.
SELECT 'source', source, count(*) FROM accidents GROUP BY 2 ORDER BY 2;
SELECT 'live_raw', (SELECT count(*) FROM accidents), (SELECT count(*) FROM internal.accidents_raw);
SELECT 'aac_total', count(*), count(source_id) FROM internal.accidents_raw WHERE lower(regexp_replace(coalesce(source, ''), '^\s+|\s+$', '', 'g')) LIKE 'aac%';
WITH k AS (SELECT lower(regexp_replace(coalesce(source_id, ''), '^\s+|\s+$', '', 'g')) AS key FROM internal.accidents_raw WHERE lower(regexp_replace(coalesce(source, ''), '^\s+|\s+$', '', 'g')) LIKE 'aac%') SELECT 'scheme', CASE WHEN key ~ '^[0-9]{8}$' THEN 'eight_digit' WHEN key ~ '[0-9]+[a-z]*$' THEN 'sequence' ELSE 'unparseable' END AS scheme, count(*), count(*) FILTER (WHERE key !~ '^[0-9]{8}$' AND substring(key from '([0-9]+)[a-z]*$')::numeric > 2685) FROM k GROUP BY 2 ORDER BY 2;
SELECT 'severity', source, lower(regexp_replace(coalesce(injury_severity, ''), '^\s+|\s+$', '', 'g')), count(*) FROM accidents GROUP BY 2, 3 ORDER BY 2, 3;
