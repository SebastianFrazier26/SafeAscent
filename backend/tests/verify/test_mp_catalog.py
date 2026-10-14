import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_every_mp_route_and_location_has_a_catalog_row():
    [row] = fetch("SELECT (SELECT count(*) FROM mp_routes r WHERE NOT EXISTS "
                  "  (SELECT 1 FROM canonical_routes c WHERE c.route_id = r.mp_route_id)) AS routes_missing, "
                  "(SELECT count(*) FROM mp_locations l WHERE NOT EXISTS "
                  "  (SELECT 1 FROM canonical_areas a WHERE a.area_id = l.mp_id)) AS areas_missing")
    assert (row["routes_missing"], row["areas_missing"]) == (0, 0)


def test_scorable_routes_have_a_us_path_a_point_and_a_known_type():
    [row] = fetch("SELECT count(*) FILTER (WHERE a.tree_status <> 'us' OR a.point_key IS NULL "
                  "  OR r.type_group IN ('unknown') OR r.type_group IS NULL) AS bad, count(*) AS n "
                  "FROM canonical_routes r JOIN canonical_areas a ON a.area_id = r.area_id WHERE r.scorable")
    print(row)
    assert row["n"] > 0 and row["bad"] == 0


def test_unscorable_reasons_and_unknown_share():
    rows = fetch("SELECT coalesce(unscorable_reason, 'scorable') AS r, count(*) AS n FROM canonical_routes "
                 "GROUP BY 1 ORDER BY 1")
    counts = {r["r"]: r["n"] for r in rows}
    print(counts)
    roped_us = counts.get("scorable", 0) + counts.get("unknown_type", 0) + counts.get("no_coordinates", 0)
    assert roped_us > 0, "no scorable, unknown_type or no_coordinates routes: has mp_catalog refresh run?"
    assert counts.get("unknown_type", 0) / roped_us <= 0.25, "unknown share above the P2-4 25% target: owner review"


def test_tick_seam_never_reads_unscraped_routes_as_counted():
    [row] = fetch("SELECT count(*) FILTER (WHERE tick_status <> 'complete' AND tick_total IS NOT NULL) AS bad_total, "
                  "count(*) FILTER (WHERE tick_status = 'complete' AND complete_through IS NULL) AS bad_through, "
                  "count(*) FILTER (WHERE tick_status = 'missing') AS missing, count(*) AS n "
                  "FROM internal.route_tick_coverage")
    print(row)
    assert (row["bad_total"], row["bad_through"]) == (0, 0)
    [months] = fetch("SELECT count(*) AS n FROM internal.route_tick_months m JOIN internal.route_tick_coverage c "
                     "USING (route_id) WHERE c.tick_status <> 'complete'")
    assert months["n"] == 0


def test_last_refresh_ok_and_quarantine_small():
    [row] = fetch("SELECT status, rows_quarantined, (SELECT count(*) FROM mp_locations) AS n_locations "
                  "FROM source_ingest_log WHERE source = 'mp_catalog' ORDER BY started_at DESC LIMIT 1")
    print(row)
    assert row["status"] == "ok"
    # The 1% bound is the owner's to move.
    assert row["rows_quarantined"] <= 0.01 * row["n_locations"]
