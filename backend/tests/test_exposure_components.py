from collections import defaultdict
from datetime import date, timedelta

import pytest

from app.pipelines.exposure_components import keys, route_density, scope, season
from app.pipelines.exposure_math import ICE_WINDOW, alpine_day, ice_day, rock_day
from tests.pgtest import migrated_db, requires_pg, run_on_conn

TODAY = date(2026, 10, 15)
CRAG, ICE_AREA, BARE = 900000110, 900000120, 900000130
KEY, ICE_KEY, BARE_KEY = "40.20000:-105.30000", "40.30000:-105.40000", "40.40000:-105.50000"
SEED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), ({CRAG}, 'Fixture Crag', 900000100, 40.2, -105.3),
  ({ICE_AREA}, 'Fixture Ice', 900000100, 40.3, -105.4), ({BARE}, 'Fixture Bare', 900000100, 40.4, -105.5);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (900000001, 'Fixture One', {CRAG}, 'Trad'), (900000002, 'Fixture Two', {CRAG}, 'Trad'),
  (900000003, 'Fixture Three', {CRAG}, 'Trad'), (900000004, 'Fixture Ice', {ICE_AREA}, 'Ice'),
  (900000005, 'Fixture Bare', {BARE}, 'Sport'), (900000006, 'Fixture Unknown', {CRAG}, 'unknown');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  ({CRAG}, 900000100, '900000100.{CRAG}', 2, 'us', 'CO', 40.2, -105.3, 'own', {CRAG}, '{KEY}', 'v'),
  ({ICE_AREA}, 900000100, '900000100.{ICE_AREA}', 2, 'us', 'CO', 40.3, -105.4, 'own', {ICE_AREA}, '{ICE_KEY}', 'v'),
  ({BARE}, 900000100, '900000100.{BARE}', 2, 'us', 'CO', 40.4, -105.5, 'own', {BARE}, '{BARE_KEY}', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000001, {CRAG}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000002, {CRAG}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000003, {CRAG}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000004, {ICE_AREA}, 'ice', false, '{{ice}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000005, {BARE}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000006, {CRAG}, 'unknown', false, '{{}}', 'rt-mp-v3', false, 'unknown_type', 'h', 'v');
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version) VALUES
  ('{KEY}', 40.2, -105.3, 11, 'a', 'b', 'America/Denver', 'f-v1'),
  ('{ICE_KEY}', 40.3, -105.4, 12, 'a', 'b', 'America/Denver', 'f-v1'),
  ('{BARE_KEY}', 40.4, -105.5, 13, 'a', 'b', NULL, 'f-v1');
"""
# Crag: every day 20 °C and dry, except every 7th day wet; one NULL-tmax day a month.
# Ice area: frozen Dec–Feb, thawed otherwise; a few NULL days inside the frozen spell.
START = TODAY - timedelta(days=3 * 365)


def _climate() -> tuple[str, list[tuple[int, date, float | None, float | None]]]:
    days = []
    for i in range((TODAY - START).days):
        d = START + timedelta(days=i)
        crag_tmax = None if d.day == 15 else 20.0
        days.append((11, d, crag_tmax, 5.0 if i % 7 == 0 else 0.0))
        frozen = d.month in (12, 1, 2)
        ice_tmax = None if frozen and d.day in (3, 4) else (-5.0 if frozen else 6.0)
        days.append((12, d, ice_tmax, 0.0))
    values = ", ".join(
        f"({b}, 'America/Denver', DATE '{d}', {'NULL' if t is None else t}, {p}, 'open_meteo_archive', 'era5')"
        for b, d, t, p in days)
    return ("INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, precip_mm, source, record_kind) "
            f"VALUES {values};"), days


def _expected(days, bucket: int, rule: str) -> dict[int, tuple[int, float | None]]:
    series = {d: (t, p) for b, d, t, p in days if b == bucket}
    per_month: dict[int, list[bool]] = defaultdict(list)
    for d, (t, p) in series.items():
        window = [series.get(d - timedelta(days=k), (None, None))[0] for k in range(ICE_WINDOW)]
        known = sum(1 for x in window if x is not None)
        frozen = sum(1 for x in window if x is not None and x < 0)
        r, i = rock_day(t, p), ice_day(frozen, known)
        ok = {"rock": r, "ice": i, "alpine": alpine_day(r, i)}[rule]
        if ok is not None:
            per_month[d.month].append(ok)
    return {m: (len(v), sum(v) / len(v)) for m, v in per_month.items()}


@requires_pg
def test_scope_counts_directly_attached_scorable_routes_only():
    with migrated_db(seed_sql=SEED) as name:
        s = run_on_conn(name, scope)
    assert s == {(CRAG, "trad"): 3, (ICE_AREA, "ice"): 1, (BARE, "sport"): 1}
    assert len(keys(s)) == 36
    assert route_density(s)[(CRAG, "trad", 7)].to_json() == {"value": 3.0, "missing": False}


@requires_pg
def test_season_sql_matches_python_rules():
    sql, days = _climate()
    with migrated_db(seed_sql=SEED + sql) as name:
        async def go(conn):
            return await season(conn, await scope(conn), today=TODAY)

        got = run_on_conn(name, go)
    rock, ice = _expected(days, 11, "rock"), _expected(days, 12, "ice")
    for m in range(1, 13):
        n, share = rock[m]
        assert got[(CRAG, "trad", m)].value == pytest.approx(share) and got[(CRAG, "trad", m)].get("n_days") == n
        n, share = ice[m]
        assert got[(ICE_AREA, "ice", m)].value == pytest.approx(share) and got[(ICE_AREA, "ice", m)].get("n_days") == n
    assert got[(ICE_AREA, "ice", 7)].value == 0.0
    assert got[(BARE, "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "no_tz"}


@requires_pg
def test_no_history_is_missing_never_zero():
    with migrated_db(seed_sql=SEED) as name:
        async def go(conn):
            return await season(conn, await scope(conn), today=TODAY)

        got = run_on_conn(name, go)
    assert got[(CRAG, "trad", 1)].to_json() == {"value": None, "missing": True, "reason": "insufficient_history",
                                                "n_days": 0.0}


@requires_pg
def test_an_unexpected_scorable_type_group_is_refused():
    with migrated_db(seed_sql=SEED) as name:
        with pytest.raises(ValueError, match="outside"):
            run_on_conn(name, lambda conn: _scope_with_unknown(conn))


async def _scope_with_unknown(conn):
    from sqlalchemy import text

    await conn.execute(text("UPDATE canonical_routes SET scorable = true, unscorable_reason = NULL "
                            "WHERE route_id = 900000006"))
    return await scope(conn)


CHILD, CHILD_KEY = 900000140, "40.25000:-105.35000"
NESTED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  ({CHILD}, 'Fixture Child', {CRAG}, 40.25, -105.35);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000007, 'Fixture Nested', {CHILD}, 'Sport');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  ({CHILD}, {CRAG}, '900000100.{CRAG}.{CHILD}', 3, 'us', 'CO', 40.25, -105.35, 'own', {CHILD}, '{CHILD_KEY}', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000007, {CHILD}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v');
"""


@requires_pg
def test_nested_cells_do_not_overlap_and_a_point_not_yet_featured_is_missing():
    with migrated_db(seed_sql=SEED + NESTED) as name:
        async def go(conn):
            s = await scope(conn)
            return s, await season(conn, s, today=TODAY)

        s, got = run_on_conn(name, go)
    assert s == {(CRAG, "trad"): 3, (CHILD, "sport"): 1, (ICE_AREA, "ice"): 1, (BARE, "sport"): 1}
    assert sum(s.values()) == 6
    assert got[(CHILD, "sport", 1)].to_json() == {"value": None, "missing": True, "reason": "no_point"}


ALPINE, ALPINE_KEY = 900000150, "40.50000:-105.60000"
ALPINE_SEED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  ({ALPINE}, 'Fixture Alpine', 900000100, 40.5, -105.6);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000008, 'Fixture Alpine', {ALPINE}, 'Alpine');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  ({ALPINE}, 900000100, '900000100.{ALPINE}', 2, 'us', 'CO', 40.5, -105.6, 'own', {ALPINE}, '{ALPINE_KEY}', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000008, {ALPINE}, 'alpine', false, '{{alpine}}', 'rt-mp-v3', true, NULL, 'h', 'v');
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version) VALUES
  ('{ALPINE_KEY}', 40.5, -105.6, 14, 'a', 'b', 'America/Denver', 'f-v1');
"""


@requires_pg
def test_alpine_unknown_days_are_not_counted_and_a_thin_month_is_missing():
    # January: dry 20 °C except three NULL-tmax days (rock unknown, ice false, so alpine unknown).
    # February: only ten days, below MIN_SEASON_DAYS, so missing with its count, never a share.
    days = [(14, date(2026, 1, d), None if 10 <= d <= 12 else 20.0, 0.0) for d in range(1, 32)]
    days += [(14, date(2026, 2, d), 20.0, 0.0) for d in range(1, 11)]
    values = ", ".join(f"(14, 'America/Denver', DATE '{d}', {'NULL' if t is None else t}, {p}, "
                       "'open_meteo_archive', 'era5')" for _, d, t, p in days)
    sql = ("INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, precip_mm, source, record_kind) "
           f"VALUES {values};")
    with migrated_db(seed_sql=SEED + ALPINE_SEED + sql) as name:
        async def go(conn):
            return await season(conn, await scope(conn), today=TODAY)

        got = run_on_conn(name, go)
    n, share = _expected(days, 14, "alpine")[1]
    assert n == 28
    assert got[(ALPINE, "alpine", 1)].to_json() == {"value": pytest.approx(share), "missing": False, "n_days": 28.0}
    assert got[(ALPINE, "alpine", 2)].to_json() == {"value": None, "missing": True, "reason": "insufficient_history",
                                                    "n_days": 10.0}
    assert got[(ALPINE, "alpine", 3)].get("n_days") == 0.0
