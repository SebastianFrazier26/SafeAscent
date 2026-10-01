-- Task 13 pre-check before r3 --apply, read-only, as analyst. Values state_code cannot map are
-- flagged by `python -m app.data.repair r3-states` (same rule as R3: blank / us:XX / foreign:XX /
-- unmapped); this raw count is the saved evidence it is diffed against.
SELECT 'state', state, count(*) FROM accidents GROUP BY 2 ORDER BY 2;
SELECT 'gnis_denali', gnis_id, name, state_code FROM gnis_summits WHERE name_key IN ('denali', 'mckinley') ORDER BY 2;
