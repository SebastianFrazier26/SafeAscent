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
R1_POOL = (
    "SELECT count(*) FROM accidents WHERE year_source = 'aac_source_id_neighbours' "
    "AND excluded_reason IS NULL AND date IS NOT NULL"
)
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


# place_key -> (GNIS name_key, state) of the summit the spec names. Rows are checked only when
# their own state is that state or blank: "Washington", "Hood" and "McKinley" are common summit
# names elsewhere, and a mis-stated row naming a nationally unique summit was already moved.
# "washington" is checked for NH rows only (the bare name is also WA's and OR's).
# "denali" is held to the one aliased feature; "mckinley" may be either AK summit of that name.
NAMED = {"denali": ("#denali", "AK"), "mckinley": ("mckinley", "AK"), "rainier": ("rainier", "WA"),
         "hood": ("hood", "OR"), "shasta": ("shasta", "CA"), "grand teton": ("grand teton", "WY"),
         "washington": ("washington", "NH")}


def test_r3a_named_peaks_are_near_their_summit():
    from app.data.repair.geocode import DENALI_GNIS_ID, TRUSTED_POINT_PRECISIONS
    from app.pipelines.geo import haversine_km
    from app.pipelines.gnis import state_code
    from app.pipelines.textsim import place_key

    summits = fetch(
        "SELECT gnis_id, name_key, state_code, lat, lon FROM gnis_summits WHERE (name_key, state_code) IN "
        "(('denali','AK'),('mckinley','AK'),('rainier','WA'),('hood','OR'),('shasta','CA'),('grand teton','WY'),"
        f"('washington','NH')) OR gnis_id = {DENALI_GNIS_ID}"
    )
    targets: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for s in summits:
        # G19: GNIS names the AK summit "Mount McKinley"; the alias table pins it by feature id.
        key = ("#denali", "AK") if s["gnis_id"] == DENALI_GNIS_ID else (s["name_key"], s["state_code"])
        targets.setdefault(key, []).append((s["lat"], s["lon"]))
        if s["gnis_id"] == DENALI_GNIS_ID:
            targets.setdefault((s["name_key"], s["state_code"]), []).append((s["lat"], s["lon"]))
    assert set(NAMED.values()) <= set(targets), f"GNIS lookup incomplete: {sorted(targets)}"
    trusted = ", ".join(f"'{p}'" for p in sorted(TRUSTED_POINT_PRECISIONS))
    far, checked = [], 0
    for r in fetch(
        "SELECT accident_id, mountain, state, latitude, longitude FROM accidents "
        f"WHERE latitude IS NOT NULL AND geocode_precision IN ({trusted})"
    ):
        target = NAMED.get(place_key(r["mountain"]))
        if target is None:
            continue
        row_state = state_code(r["state"])
        if row_state != target[1] and (row_state is not None or target == ("washington", "NH")):
            continue
        checked += 1
        if min(haversine_km(r["latitude"], r["longitude"], lat, lon) for lat, lon in targets[target]) > 50:
            far.append(r["accident_id"])
    print({"named_rows_checked": checked, "far": far[:20]})
    assert checked > 0 and far == []


def test_r3_no_trusted_rows_at_raw_fallback_points_and_no_alaska_text_below_55n():
    from app.data.repair.geocode import TRUSTED_POINT_PRECISIONS, GeoRow, find_fallback_points

    # G6: fallback points come from the pre-repair snapshot with the step's own rule; rebuilt
    # from live coordinates, R3a's summit moves would form "clusters" of area rows by design.
    raw = fetch("SELECT accident_id, mountain, latitude, longitude FROM internal.accidents_raw")
    points = find_fallback_points(
        [GeoRow(r["accident_id"], "aac", r["mountain"], None, r["latitude"], r["longitude"], None) for r in raw]
    )
    live = fetch("SELECT accident_id, latitude, longitude, geocode_precision FROM accidents WHERE latitude IS NOT NULL")
    at_fallback = [
        r["accident_id"] for r in live
        if (round(r["latitude"], 3), round(r["longitude"], 3)) in points
        and r["geocode_precision"] in TRUSTED_POINT_PRECISIONS
    ]
    [ak] = fetch(
        "SELECT count(*) AS n FROM accidents WHERE lower(trim(state)) IN ('ak', 'alaska') AND latitude < 55 "
        "AND geocode_precision IN ('exact','crag','area','park_centroid')"
    )
    print({"raw_fallback_points": len(points), "trusted_at_fallback": at_fallback[:20], "ak_below_55": ak["n"]})
    # Fallback points keep their coordinates (A7/D10), so the spec's "no rows remain" reads
    # as "no row there is labelled usable as a point".
    assert (at_fallback, ak["n"]) == ([], 0)


def test_r3_ambiguous_rows_pending_owner_review_are_reported():
    rows = fetch(
        "SELECT country IS NULL AS no_country, count(*) AS n FROM accidents "
        "WHERE geocode_method IN ('gnis_ambiguous', 'gnis_state_unmatched') GROUP BY 1 ORDER BY 1"
    )
    # Untrusted and never moved; the PR records the count the owner still has to settle.
    print({"summit_review_pending": [dict(r) for r in rows]})
    [row] = fetch(
        "SELECT count(*) AS n FROM accidents WHERE geocode_method IN ('gnis_ambiguous', 'gnis_state_unmatched') "
        "AND geocode_precision IS DISTINCT FROM 'unknown'"
    )
    assert row["n"] == 0


R3A_POOL = "SELECT count(*) FROM accidents WHERE geocode_method LIKE 'gnis\\_summit%'"
R3_PLOT_POOL = "SELECT count(*) FROM accidents WHERE latitude IS NOT NULL AND longitude IS NOT NULL"


def test_r3a_hand_check_covers_every_move_at_95_percent():
    [row] = fetch(f"SELECT ({R3A_POOL}) AS n")
    # Every move is checked, not a sample (rows_in >= the whole pool).
    assert _latest_check("repair:r3a-check", R3A_POOL, row["n"]) >= 0.95


def test_r3_plot_check_at_95_percent():
    assert _latest_check("repair:r3-plot-check", R3_PLOT_POOL, 50) >= 0.95
