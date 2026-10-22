from collections import defaultdict
from datetime import date, timedelta

import pytest

from app.pipelines.exposure_components import keys, mp_ticks, route_density, scope, season, season_window
from app.pipelines.exposure_math import ICE_WINDOW, alpine_day, ice_day, rock_day
from tests.pgtest import migrated_db, requires_pg, run_on_conn, run_sql

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
# ERA5 is bought once a year: the span ends in early January, so the season window is 2023-2025.
# The rest of the year is stopgap (Forecast-API past days) then forecast, and both are written to
# change every share if they were read: perfect rock days at the crag, deep frost at the ice area.
ERA5_FIRST, ERA5_LAST = date(2022, 12, 20), date(2026, 1, 5)
WIN_START, WIN_END = date(2023, 1, 1), date(2025, 12, 31)
FORECAST_END = TODAY + timedelta(days=15)
Day = tuple[int, str, date, float | None, float | None, str]


def _row(b: int, tz: str, d: date, t: float | None, p: float | None, kind: str) -> str:
    source = "open_meteo_archive" if kind == "era5" else "open_meteo_forecast"
    return (f"({b}, '{tz}', DATE '{d}', {'NULL' if t is None else t}, {'NULL' if p is None else p}, "
            f"'{source}', '{kind}', {'true' if kind == 'forecast' else 'false'})")


def _insert(days: list[Day]) -> str:
    return ("INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, precip_mm, source, record_kind, "
            f"is_forecast) VALUES {', '.join(_row(*x) for x in days)};")


def _series(*rows: tuple[int, str, date | None, date | None]) -> str:
    vals = ", ".join(f"({b}, '{tz}', {'NULL' if f is None else repr(str(f))}, {'NULL' if last is None else repr(str(last))})"
                     for b, tz, f, last in rows)
    return f"INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) VALUES {vals};"


def _kind(d: date) -> str:
    return "era5" if d <= ERA5_LAST else ("stopgap" if d < TODAY else "forecast")


def _climate() -> tuple[str, list[Day]]:
    # Crag: 20 °C and dry, every 7th day wet, one NULL-tmax day a month. Ice area: frozen Dec–Feb,
    # thawed otherwise, a few NULL days inside the frozen spell.
    days: list[Day] = []
    for i in range((FORECAST_END - ERA5_FIRST).days + 1):
        d = ERA5_FIRST + timedelta(days=i)
        kind = _kind(d)
        if kind != "era5":
            days.append((11, "America/Denver", d, 20.0, 0.0, kind))
            days.append((12, "America/Denver", d, -10.0, 0.0, kind))
            continue
        days.append((11, "America/Denver", d, None if d.day == 15 else 20.0, 5.0 if i % 7 == 0 else 0.0, kind))
        frozen = d.month in (12, 1, 2)
        days.append((12, "America/Denver", d, None if frozen and d.day in (3, 4) else (-5.0 if frozen else 6.0),
                     0.0, kind))
    series = _series((11, "America/Denver", ERA5_FIRST, ERA5_LAST), (12, "America/Denver", ERA5_FIRST, ERA5_LAST))
    return _insert(days) + series, days


def _expected(days: list[Day], bucket: int, rule: str, tz: str = "America/Denver",
              start: date = WIN_START, end: date = WIN_END) -> dict[int, tuple[int, float]]:
    series = {d: (t, p) for b, z, d, t, p, k in days if b == bucket and z == tz and k == "era5"}
    per_month: dict[int, list[bool]] = defaultdict(list)
    for d, (t, p) in series.items():
        if not start <= d <= end:
            continue
        window = [series.get(d - timedelta(days=k), (None, None))[0] for k in range(ICE_WINDOW)]
        known = sum(1 for x in window if x is not None)
        frozen = sum(1 for x in window if x is not None and x < 0)
        r, i = rock_day(t, p), ice_day(frozen, known)
        ok = {"rock": r, "ice": i, "alpine": alpine_day(r, i)}[rule]
        if ok is not None:
            per_month[d.month].append(ok)
    return {m: (len(v), sum(v) / len(v)) for m, v in per_month.items()}


def _season_at(today: date):
    async def go(conn):
        return await season(conn, await scope(conn), today=today)

    return go


def test_a_zero_day_minimum_is_refused_at_import(monkeypatch):
    import importlib

    import app.pipelines.exposure_components as mod
    import app.pipelines.exposure_math as math_mod

    monkeypatch.setattr(math_mod, "MIN_SEASON_DAYS", 0)
    with pytest.raises(RuntimeError, match="MIN_SEASON_DAYS"):
        importlib.reload(mod)
    monkeypatch.undo()
    importlib.reload(mod)


def test_season_window_is_complete_calendar_years():
    assert season_window(ERA5_FIRST, ERA5_LAST, today=TODAY) == (WIN_START, WIN_END)
    assert season_window(date(2023, 1, 1), date(2025, 12, 31), today=TODAY) == (WIN_START, WIN_END)
    assert season_window(date(2023, 1, 1), date(2025, 12, 30), today=TODAY) is None
    assert season_window(date(2023, 1, 2), date(2026, 1, 5), today=TODAY) is None
    assert season_window(date(2020, 1, 1), date(2026, 1, 5), today=date(2025, 12, 30)) == (date(2022, 1, 1),
                                                                                            date(2024, 12, 31))


@requires_pg
def test_scope_counts_directly_attached_scorable_routes_only():
    with migrated_db(seed_sql=SEED) as name:
        s = run_on_conn(name, scope)
    assert s == {(CRAG, "trad"): 3, (ICE_AREA, "ice"): 1, (BARE, "sport"): 1}
    assert len(keys(s)) == 36
    assert route_density(s)[(CRAG, "trad", 7)].to_json() == {"value": 3.0, "missing": False}


@requires_pg
def test_season_sql_matches_python_rules_over_era5_only():
    sql, days = _climate()
    with migrated_db(seed_sql=SEED + sql) as name:
        got = run_on_conn(name, _season_at(TODAY))
    rock, ice = _expected(days, 11, "rock"), _expected(days, 12, "ice")
    for m in range(1, 13):
        n, share = rock[m]
        assert got[(CRAG, "trad", m)].value == pytest.approx(share) and got[(CRAG, "trad", m)].get("n_days") == n
        n, share = ice[m]
        assert got[(ICE_AREA, "ice", m)].value == pytest.approx(share) and got[(ICE_AREA, "ice", m)].get("n_days") == n
    # January 1 2023 reads the nine December ERA5 days before the window as ice lookback.
    assert got[(ICE_AREA, "ice", 1)].get("n_days") == 3 * 31
    assert got[(ICE_AREA, "ice", 7)].value == 0.0
    assert got[(BARE, "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "no_tz"}


@requires_pg
def test_builds_on_different_days_between_purchases_are_identical():
    sql, _ = _climate()
    with migrated_db(seed_sql=SEED + sql) as name:
        runs = [run_on_conn(name, _season_at(d)) for d in (date(2026, 1, 20), TODAY, date(2026, 12, 30))]
    assert runs[0] == runs[1] == runs[2]


@requires_pg
def test_stopgap_and_forecast_rows_inside_the_window_are_never_read():
    sql, _ = _climate()
    hole = [date(2024, 6, d) for d in range(1, 31)]
    filler = [(11, "America/Denver", d, 20.0, 0.0, "stopgap" if d.day <= 15 else "forecast") for d in hole]
    with migrated_db(seed_sql=SEED + sql) as name:
        async def go(conn):
            from sqlalchemy import text

            before = await _season_at(TODAY)(conn)
            await conn.execute(text("DELETE FROM cell_daily_conditions WHERE grid_bucket = 11 "
                                    "AND date BETWEEN '2024-06-01' AND '2024-06-30'"))
            gap = await _season_at(TODAY)(conn)
            await conn.execute(text(_insert(filler)))
            return before, gap, await _season_at(TODAY)(conn)

        before, gap, filled = run_on_conn(name, go)
    assert gap[(CRAG, "trad", 6)] != before[(CRAG, "trad", 6)]
    assert filled == gap


TZ_A, TZ_B = 900000190, 900000191
TZ_SEED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  ({TZ_A}, 'Fixture Line East', 900000100, 41.0, -111.0), ({TZ_B}, 'Fixture Line West', 900000100, 41.01, -111.01);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (900000020, 'Fixture East', {TZ_A}, 'Sport'), (900000021, 'Fixture West', {TZ_B}, 'Sport'),
  (900000022, 'Fixture East Ice', {TZ_A}, 'Ice'), (900000023, 'Fixture West Ice', {TZ_B}, 'Ice');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  ({TZ_A}, 900000100, '900000100.{TZ_A}', 2, 'us', 'CO', 41.0, -111.0, 'own', {TZ_A}, '41.00000:-111.00000', 'v'),
  ({TZ_B}, 900000100, '900000100.{TZ_B}', 2, 'us', 'CO', 41.01, -111.01, 'own', {TZ_B}, '41.01000:-111.01000', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000020, {TZ_A}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000021, {TZ_B}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000022, {TZ_A}, 'ice', false, '{{ice}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000023, {TZ_B}, 'ice', false, '{{ice}}', 'rt-mp-v3', true, NULL, 'h', 'v');
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version) VALUES
  ('41.00000:-111.00000', 41.0, -111.0, 18, 'a', 'b', 'America/Denver', 'f-v1'),
  ('41.01000:-111.01000', 41.01, -111.01, 18, 'a', 'b', 'America/Boise', 'f-v1');
"""


@requires_pg
def test_two_timezone_series_in_one_bucket_stay_separate():
    span = [WIN_START + timedelta(days=i) for i in range((WIN_END - WIN_START).days + 1)]
    days: list[Day] = [(18, "America/Denver", d, 20.0, 0.0, "era5") for d in span]
    # Boise is frozen and wet: no rock days and all ice days, the reverse of Denver, so any mixing
    # of the two series, in the join or in the ice window's partition, moves every share.
    days += [(18, "America/Boise", d, -5.0, 9.0, "era5") for d in span]
    sql = _insert(days) + _series((18, "America/Denver", WIN_START, WIN_END), (18, "America/Boise", WIN_START, WIN_END))
    with migrated_db(seed_sql=SEED + TZ_SEED + sql) as name:
        got = run_on_conn(name, _season_at(TODAY))
    assert got[(TZ_A, "sport", 7)].to_json() == {"value": 1.0, "missing": False, "n_days": 93.0}
    assert got[(TZ_B, "sport", 7)].to_json() == {"value": 0.0, "missing": False, "n_days": 93.0}
    assert got[(TZ_A, "ice", 7)].to_json() == {"value": 0.0, "missing": False, "n_days": 93.0}
    assert got[(TZ_B, "ice", 7)].to_json() == {"value": 1.0, "missing": False, "n_days": 93.0}


ROCKY, THIN, NO_SPAN, NO_ROW = 900000160, 900000170, 900000180, 900000181
EDGE_SEED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  ({ROCKY}, 'Fixture Rocky', 900000100, 40.6, -105.7), ({THIN}, 'Fixture Thin', 900000100, 40.7, -105.8),
  ({NO_SPAN}, 'Fixture No Span', 900000100, 40.8, -105.9), ({NO_ROW}, 'Fixture No Row', 900000100, 40.9, -106.0);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (900000010, 'Fixture Rocky', {ROCKY}, 'Trad, Sport'), (900000011, 'Fixture Thin', {THIN}, 'Sport'),
  (900000012, 'Fixture No Span', {NO_SPAN}, 'Sport'), (900000013, 'Fixture No Row', {NO_ROW}, 'Sport');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  ({ROCKY}, 900000100, '900000100.{ROCKY}', 2, 'us', 'CO', 40.6, -105.7, 'own', {ROCKY}, '40.60000:-105.70000', 'v'),
  ({THIN}, 900000100, '900000100.{THIN}', 2, 'us', 'CO', 40.7, -105.8, 'own', {THIN}, '40.70000:-105.80000', 'v'),
  ({NO_SPAN}, 900000100, '900000100.{NO_SPAN}', 2, 'us', 'CO', 40.8, -105.9, 'own', {NO_SPAN}, '40.80000:-105.90000',
   'v'),
  ({NO_ROW}, 900000100, '900000100.{NO_ROW}', 2, 'us', 'CO', 40.9, -106.0, 'own', {NO_ROW}, '40.90000:-106.00000',
   'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000010, {ROCKY}, 'rock', false, '{{}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000011, {THIN}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000012, {NO_SPAN}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000013, {NO_ROW}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v');
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version) VALUES
  ('40.60000:-105.70000', 40.6, -105.7, 15, 'a', 'b', 'America/Denver', 'f-v1'),
  ('40.70000:-105.80000', 40.7, -105.8, 16, 'a', 'b', 'America/Denver', 'f-v1'),
  ('40.80000:-105.90000', 40.8, -105.9, 17, 'a', 'b', 'America/Denver', 'f-v1'),
  ('40.90000:-106.00000', 40.9, -106.0, 19, 'a', 'b', 'America/Denver', 'f-v1');
"""


@requires_pg
def test_rock_cell_window_bounds_and_missing_reasons():
    # Rock cell: every July day of the window, plus single days on and just outside each bound.
    july = [d for y in (2023, 2024, 2025) for d in (date(y, 7, k) for k in range(1, 32))]
    edges = [date(2022, 12, 31), date(2023, 1, 1), date(2025, 12, 31), date(2026, 1, 1)]
    days: list[Day] = [(15, "America/Denver", d, 20.0, 0.0, "era5") for d in july + edges]
    # Frozen Februaries are ice days but never rock days: the rock rule reads a measured 0, where
    # alpine would read 1.
    feb = [d for y in (2023, 2024, 2025) for k in range(29) if (d := date(y, 2, 1) + timedelta(days=k)).month == 2]
    days += [(15, "America/Denver", d, -5.0, 0.0, "era5") for d in feb]
    # Thin: a span two complete years long is not enough, however full each month is.
    thin = [date(2024, 1, 1) + timedelta(days=i) for i in range(731)]
    days += [(16, "America/Denver", d, 20.0, 0.0, "era5") for d in thin]
    sql = _insert(days) + _series((15, "America/Denver", date(2022, 12, 1), date(2026, 1, 5)),
                                  (16, "America/Denver", date(2024, 1, 1), date(2026, 1, 5)),
                                  (17, "America/Denver", None, None))
    with migrated_db(seed_sql=SEED + EDGE_SEED + sql) as name:
        got = run_on_conn(name, _season_at(TODAY))
    assert got[(ROCKY, "rock", 7)].to_json() == {"value": 1.0, "missing": False, "n_days": 93.0}
    assert got[(ROCKY, "rock", 2)].to_json() == {"value": 0.0, "missing": False, "n_days": 85.0}
    assert got[(ROCKY, "rock", 1)].to_json() == {"value": None, "missing": True, "reason": "insufficient_history",
                                                 "n_days": 1.0}
    assert got[(ROCKY, "rock", 12)].get("n_days") == 1.0
    assert got[(ROCKY, "rock", 3)].to_json() == {"value": None, "missing": True, "reason": "insufficient_history",
                                                 "n_days": 0.0}
    assert got[(THIN, "sport", 7)].reason == "insufficient_history" and got[(THIN, "sport", 7)].value is None
    assert got[(NO_SPAN, "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "no_era5"}
    assert got[(NO_ROW, "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "no_era5"}


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
    assert got[(CRAG, "trad", 1)].to_json() == {"value": None, "missing": True, "reason": "no_era5"}


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
    # January (one year): dry 20 °C except three NULL-tmax days, where rock is unknown and ice
    # false, so alpine is unknown. February: ten days, below MIN_SEASON_DAYS summed over the window.
    days: list[Day] = [(14, "America/Denver", date(2025, 1, d), None if 10 <= d <= 12 else 20.0, 0.0, "era5")
                       for d in range(1, 32)]
    days += [(14, "America/Denver", date(2025, 2, d), 20.0, 0.0, "era5") for d in range(1, 11)]
    sql = _insert(days) + _series((14, "America/Denver", WIN_START, ERA5_LAST))
    with migrated_db(seed_sql=SEED + ALPINE_SEED + sql) as name:
        got = run_on_conn(name, _season_at(TODAY))
    n, share = _expected(days, 14, "alpine")[1]
    assert n == 28
    assert got[(ALPINE, "alpine", 1)].to_json() == {"value": pytest.approx(share), "missing": False, "n_days": 28.0}
    assert got[(ALPINE, "alpine", 2)].to_json() == {"value": None, "missing": True, "reason": "insufficient_history",
                                                    "n_days": 10.0}
    assert got[(ALPINE, "alpine", 3)].get("n_days") == 0.0


# Crag: 900000001 complete through 2026-08-31, 900000002 scraped (incomplete), 900000003 never scraped.
# Ice: 900000004 complete through 2026-01-31.
TICKS = """
INSERT INTO internal.route_tick_scrapes (mp_route_id, source, scraped_at, complete, complete_through, total_ticks) VALUES
  (900000001, 'mp_scrape', '2026-09-10T00:00:00+00', true, DATE '2026-08-31', 18),
  (900000002, 'mp_scrape', '2026-09-10T00:00:00+00', false, NULL, NULL),
  (900000004, 'mp_scrape', '2026-02-10T00:00:00+00', true, DATE '2026-01-31', 6);
INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES
  (900000001, '2021-07', 'lead', 1), (900000001, '2025-07', 'lead', 4), (900000001, '2025-07', 'follow', 5),
  (900000001, '2026-07', 'lead', 3), (900000001, '2026-08', 'lead', 2), (900000001, '2026-09', 'lead', 7),
  (900000001, '2021-08', 'lead', 3),
  (900000002, '2025-07', 'lead', 50),
  (900000004, '2021-02', 'lead', 5), (900000004, '2025-01', 'lead', 4), (900000004, '2026-01', 'lead', 2);
"""


def _ticks(name, *, enabled=True, cutoff=TODAY):
    async def go(conn):
        return await mp_ticks(conn, await scope(conn), enabled=enabled, cutoff=cutoff)

    return run_on_conn(name, go)


@requires_pg
def test_partial_coverage_is_raw_and_counted():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        got = _ticks(name)
    july = got[(CRAG, "trad", 7)].to_json()
    # 900000001's window is 2021-09 .. 2026-08: July occurs as 2022..2026 -> (4 + 5 + 3) / 5; 2021-07 is outside.
    assert july == {"value": 12 / 5, "missing": False, "coverage": 1 / 3, "n_complete": 1.0, "n_scraped": 1.0,
                    "n_missing": 1.0, "per_route_mean": 12 / 5, "window_months": 60.0}
    assert got[(CRAG, "trad", 8)].value == 2 / 5
    assert got[(CRAG, "trad", 1)].value == 0.0


@requires_pg
def test_no_complete_route_is_missing_not_zero():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        got = _ticks(name)
    assert got[(BARE, "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "no_complete_routes",
                                                 "coverage": 0.0, "n_complete": 0.0, "n_scraped": 0.0,
                                                 "n_missing": 1.0}


@requires_pg
def test_cutoff_and_complete_through_bound_the_window():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        mid = _ticks(name, cutoff=date(2026, 7, 20))
        ice = _ticks(name)
    # Cutoff 2026-07-20: the last complete month before it is 2026-06, so the window is 2021-07..2026-06.
    assert mid[(CRAG, "trad", 7)].value == (1 + 4 + 5) / 5
    assert mid[(CRAG, "trad", 8)].value == 3 / 5
    # The ice route's window ends at its complete_through (2026-01), not at today's cutoff.
    assert ice[(ICE_AREA, "ice", 1)].value == (4 + 2) / 5 and ice[(ICE_AREA, "ice", 1)].get("coverage") == 1.0
    # So its window starts at 2021-02; a window anchored on the cutoff (2021-10..2026-09) would drop
    # those ticks and divide 52 observed months by five years.
    assert ice[(ICE_AREA, "ice", 2)].value == 5 / 5


@requires_pg
def test_disabled_and_dropped_are_missing_with_no_coverage():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        off = _ticks(name, enabled=False)
        run_sql(name, "DROP VIEW internal.route_tick_months")
        gone = _ticks(name)
    assert off[(CRAG, "trad", 7)].to_json() == {"value": None, "missing": True, "reason": "disabled"}
    assert gone[(CRAG, "trad", 7)].to_json() == {"value": None, "missing": True, "reason": "table_dropped"}
