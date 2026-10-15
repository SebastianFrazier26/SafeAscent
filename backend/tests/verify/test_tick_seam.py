"""Read-only seam cells (exposure plan X-1): run with VERIFY against a branch or prod."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_complete_totals_equal_their_counted_months():
    [row] = fetch(
        "SELECT count(*) AS bad FROM internal.route_tick_coverage c "
        "LEFT JOIN (SELECT route_id, sum(tick_count) AS s FROM internal.route_tick_months GROUP BY 1) m "
        "ON m.route_id = c.route_id WHERE c.tick_status = 'complete' AND c.tick_total <> coalesce(m.s, 0)")
    assert row["bad"] == 0


def test_status_counts_by_type_group_are_reported():
    rows = fetch(
        "SELECT r.type_group, c.tick_status, count(*) AS n FROM scorable_routes r "
        "JOIN internal.route_tick_coverage c ON c.route_id = r.route_id GROUP BY 1, 2 ORDER BY 1, 2")
    assert rows
    # F15: a real invariant, not just a printer — every scorable type group appears, and every
    # status is one the seam can actually produce.
    groups = fetch("SELECT DISTINCT type_group FROM scorable_routes")
    assert {r["type_group"] for r in rows} == {g["type_group"] for g in groups}
    assert {r["tick_status"] for r in rows} <= {"missing", "scraped", "complete"}
    for r in rows:
        print(r["type_group"], r["tick_status"], r["n"])
