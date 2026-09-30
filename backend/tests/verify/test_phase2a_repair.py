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


def test_r1_hand_check_at_least_90_percent_within_one_year():
    [row] = fetch(
        "SELECT (validation_report->>'agreement')::float AS a FROM source_ingest_log "
        "WHERE source = 'repair:r1-check' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1"
    )
    assert row["a"] >= 0.90


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
        "WHERE source ILIKE 'aac%' GROUP BY 1, 2"
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
    [check] = fetch(
        "SELECT (validation_report->>'agreement')::float AS a FROM source_ingest_log "
        "WHERE source = 'repair:r9-check' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1"
    )
    print({"r9_agreement": check["a"]})
