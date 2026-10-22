"""P3 Task 25 acceptance checks for the stored model scores, run by the owner against prod or a Neon
branch as the read-only analyst role: VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify.
Without that URL every test skips."""

import json

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db

ACTIVE = "(SELECT version FROM model.registry WHERE status = 'active')"
CONF_LEVELS = ("high", "medium", "low", "insufficient")


def test_exactly_one_active_version():
    [row] = fetch("SELECT count(*) AS n FROM model.registry WHERE status = 'active'")
    assert row["n"] == 1


def test_every_scorable_known_route_has_twelve_months():
    # A route in an area without coordinates has no cell (app.ml.dataset.AREAS_SQL), so no row.
    rows = fetch(
        "SELECT s.route_id, count(DISTINCT r.month) AS months "
        "FROM scorable_routes s JOIN canonical_areas a ON a.area_id = s.area_id "
        f"LEFT JOIN model.route_scores r ON r.route_id = s.route_id AND r.model_version = {ACTIVE} "
        "WHERE s.type_group <> 'unknown' AND a.lat IS NOT NULL "
        "GROUP BY s.route_id HAVING count(DISTINCT r.month) <> 12 LIMIT 20")
    assert not rows, f"routes without all 12 months: {[(r['route_id'], r['months']) for r in rows]}"


def test_no_insufficient_row_carries_a_number():
    [row] = fetch(
        "SELECT count(*) AS n FROM model.route_scores "
        f"WHERE model_version = {ACTIVE} AND (insufficient OR conf_level = 'insufficient') AND ("
        "NOT insufficient OR conf_level <> 'insufficient' OR rr_mean IS NOT NULL OR rr_q05 IS NOT NULL "
        "OR rr_q95 IS NOT NULL OR percentile IS NOT NULL OR pct_q05 IS NOT NULL OR pct_q95 IS NOT NULL)")
    assert row["n"] == 0


def test_every_scored_row_has_a_number_and_a_level():
    [row] = fetch(
        "SELECT count(*) AS n FROM model.route_scores "
        f"WHERE model_version = {ACTIVE} AND NOT insufficient AND (percentile IS NULL OR rr_mean IS NULL "
        "OR conf_level NOT IN ('high', 'medium', 'low'))")
    assert row["n"] == 0


def test_conf_level_counts_match_the_model_card():
    [card] = fetch(f"SELECT model_card->'conf_counts' AS counts FROM model.registry WHERE version = {ACTIVE}")
    if card["counts"] is None:
        pytest.skip("the active version's model_card records no conf_counts yet")
    expected = json.loads(card["counts"])
    got = {r["conf_level"]: r["n"] for r in fetch(
        "SELECT conf_level, count(*) AS n FROM model.route_scores "
        f"WHERE model_version = {ACTIVE} GROUP BY conf_level")}
    assert {k: got.get(k, 0) for k in CONF_LEVELS} == {k: int(expected.get(k, 0)) for k in CONF_LEVELS}
