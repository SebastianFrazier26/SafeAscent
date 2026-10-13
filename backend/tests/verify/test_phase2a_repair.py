"""Phase 2a acceptance cells (spec R-table "Verification" column), as analyst. Connections go
through plan 1's tests/verify/_db.fetch (connect_args_for, verify-full), never a local connect."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_r1_2023_aac_count_is_at_most_twice_the_2015_2018_mean():
    [row] = fetch(
        "WITH y AS (SELECT extract(year FROM date)::int AS yr, count(*) AS n FROM accidents "
        "WHERE source ILIKE 'aac%' AND excluded_reason IS NULL GROUP BY 1) "
        "SELECT coalesce((SELECT n FROM y WHERE yr = 2023), 0) AS y2023, "
        "(SELECT avg(n) FROM y WHERE yr BETWEEN 2015 AND 2018) AS base"
    )
    print(dict(row))
    assert row["y2023"] <= 2 * row["base"]


# The sampled pools mirror severity.r1_check_rows / r9_check_pool; the import refuses ids
# outside them, so rows_in counts real checks of the pool the rate describes.
R1V2_IDS = "SELECT accident_id FROM internal.accident_revisions WHERE rule_version = 'r1-v2'"
R1_REDATED = (
    "SELECT count(*) FROM accidents WHERE year_source = 'aac_source_id_neighbours' "
    "AND excluded_reason IS NULL AND date IS NOT NULL"
)
R1_POOL = f"{R1_REDATED} AND accident_id NOT IN ({R1V2_IDS})"
R1V2_POOL = f"{R1_REDATED} AND accident_id IN ({R1V2_IDS})"
R9_POOL = "SELECT count(*) FROM accidents WHERE lower(trim(source)) LIKE 'aac%'"


def _latest_check(source: str, pool_sql: str, sample: int) -> float:
    [row] = fetch(
        f"SELECT rows_in, (validation_report->>'agreement')::float AS a, ({pool_sql}) AS eligible "
        f"FROM source_ingest_log WHERE source = '{source}' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1"
    )
    print({"source": source, "rows_in": row["rows_in"], "eligible": row["eligible"], "agreement": row["a"]})
    assert row["rows_in"] >= min(sample, row["eligible"]) > 0
    assert row["a"] is not None and 0.0 <= row["a"] <= 1.0
    return float(row["a"])


def test_r1_hand_check_at_least_90_percent_within_one_year():
    assert _latest_check("repair:r1-check", R1_POOL, 40) >= 0.90


def test_r1v2_hand_check_at_least_90_percent_within_one_year():
    assert _latest_check("repair:r1v2-check", R1V2_POOL, 40) >= 0.90


def test_r1v2_rows_are_ranges_of_at_most_two_years_holding_their_point():
    rows = fetch(
        "SELECT r.method, count(*) AS n, "
        "count(*) FILTER (WHERE a.year_lo IS NULL OR a.year_hi IS NULL OR a.year_hi - a.year_lo > 2 "
        "OR a.year NOT BETWEEN a.year_lo AND a.year_hi OR extract(year FROM a.date) <> a.year "
        "OR (a.date_precision IN ('day', 'month') AND a.year_lo <> a.year_hi) "
        "OR a.date_precision NOT IN ('day', 'month', 'year')) AS bad "
        "FROM accidents a JOIN (SELECT DISTINCT accident_id, method FROM internal.accident_revisions "
        "WHERE rule_version = 'r1-v2' AND field = 'date_precision') r USING (accident_id) "
        "WHERE a.excluded_reason IS NULL GROUP BY 1 ORDER BY 1"
    )
    print({"r1v2_dated": {r["method"]: r["n"] for r in rows}})
    assert rows and all(r["bad"] == 0 for r in rows)


def test_r1_day_precision_rows_are_single_year():
    # accidents_clean_daily (0006) keeps date_precision = 'day'; for a range-dated row that
    # must coincide with year_lo IS NOT DISTINCT FROM year_hi, or a guessed day would leak in.
    [row] = fetch(
        "SELECT count(*) AS n FROM accidents WHERE year_source = 'aac_source_id_neighbours' "
        "AND date_precision IN ('day', 'month') AND year_lo IS DISTINCT FROM year_hi"
    )
    assert row["n"] == 0


def test_r1_unverified_year_count_is_reported():
    # D10: excluded, never deleted; the PR records these counts as a Phase 3 recency-bias note.
    rows = fetch(
        "SELECT source, extract(year FROM date)::int AS yr, count(*) AS n FROM accidents "
        "WHERE excluded_reason = 'year_unverified' GROUP BY 1, 2 ORDER BY 1, 2"
    )
    print({"year_unverified_total": sum(r["n"] for r in rows)})
    print([dict(r) for r in rows])
    [row] = fetch(
        "SELECT count(*) AS n FROM accidents WHERE excluded_reason = 'year_unverified' "
        "AND date_precision IS DISTINCT FROM 'unknown'"
    )
    assert row["n"] == 0


def test_r2_counts_are_near_the_audit():
    # G7: the audit figures describe R2's rule alone, so rows R1 re-dated (year_source set)
    # are counted separately, not against the R2 bounds.
    rows = fetch(
        "SELECT year_source IS NOT NULL AS r1, date_precision AS p, count(*) AS n FROM accidents "
        "WHERE source ILIKE 'aac%' AND accident_id IN (SELECT accident_id FROM internal.accidents_raw) GROUP BY 1, 2"
    )
    counts = {r["p"]: r["n"] for r in rows if not r["r1"]}
    print({"r2": counts, "r1": {r["p"]: r["n"] for r in rows if r["r1"]}})
    assert 1400 <= counts.get("month", 0) <= 1750
    assert 100 <= counts.get("year", 0) <= 200


def test_every_row_has_a_precision_and_every_unknown_row_is_excluded():
    # Any exclusion counts: R1/R2 leave another step's excluded_reason in place (Task 3 fix round 3).
    [row] = fetch(
        "SELECT count(*) FILTER (WHERE date_precision IS NULL) AS missing, "
        "count(*) FILTER (WHERE date_precision = 'unknown' AND excluded_reason IS NULL) AS unexcluded "
        "FROM accidents"
    )
    assert (row["missing"], row["unexcluded"]) == (0, 0)


def test_r9_severity_scale_set_and_agreement_recorded():
    [row] = fetch("SELECT count(*) FILTER (WHERE severity_scale IS NULL) AS missing FROM accidents")
    assert row["missing"] == 0
    _latest_check("repair:r9-check", R9_POOL, 50)
