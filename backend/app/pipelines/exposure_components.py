"""Exposure component readers: one set-based query per component over the MP catalog (cells are
areas with directly attached scorable routes, DP5). Each returns a Component for every key of the
scope, so assembly fails loudly on a gap instead of filling one."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.exposure_math import (
    ICE_MIN_FROZEN, ICE_WINDOW, MIN_SEASON_DAYS, MONTHS, ROCK_MAX_PRECIP, ROCK_TMAX_HI, ROCK_TMAX_LO,
    RULE_BY_GROUP, SEASON_YEARS, TICK_WINDOW_MONTHS, TYPE_GROUPS, Component, tick_component,
)

Key = tuple[int, str, int]
Scope = dict[tuple[int, str], int]
SERIES_CHUNK = 500

SCOPE_SQL = text("SELECT area_id, type_group, count(*) FROM scorable_routes GROUP BY 1, 2")
AREA_SERIES_SQL = text(
    "SELECT a.area_id, f.grid_bucket, f.tz, g.first_era5_date, g.last_era5_date "
    "FROM canonical_areas a JOIN feature_points f ON f.point_key = a.point_key "
    "LEFT JOIN grid_bucket_series g ON g.grid_bucket = f.grid_bucket AND g.tz = f.tz "
    "WHERE a.area_id = ANY(CAST(:areas AS bigint[]))")
# Window frame by calendar days: a day absent from the series is unknown, and ice_day's
# (ICE_WINDOW - known) term counts it so, exactly as exposure_math.ice_day does. Rows from
# ICE_WINDOW - 1 days before the season window are read only as lookback for the window's first days.
SEASON_SQL = text(f"""
WITH s AS (
  SELECT * FROM unnest(CAST(:buckets AS integer[]), CAST(:tzs AS text[]), CAST(:sinces AS date[]),
                       CAST(:untils AS date[])) AS s(grid_bucket, tz, since, until)
), d AS (
  SELECT c.grid_bucket, c.tz, c.date, s.since,
         CASE WHEN c.tmax IS NULL OR c.precip_mm IS NULL THEN NULL
              ELSE c.tmax BETWEEN {ROCK_TMAX_LO} AND {ROCK_TMAX_HI} AND c.precip_mm < {ROCK_MAX_PRECIP} END AS rock,
         count(*) FILTER (WHERE c.tmax < 0) OVER w AS frozen,
         count(c.tmax) OVER w AS known
  FROM cell_daily_conditions c JOIN s ON s.grid_bucket = c.grid_bucket AND s.tz = c.tz
  WHERE c.record_kind = 'era5' AND c.date >= s.since - {ICE_WINDOW - 1} AND c.date <= s.until
  WINDOW w AS (PARTITION BY c.grid_bucket, c.tz ORDER BY c.date
               RANGE BETWEEN INTERVAL '{ICE_WINDOW - 1} days' PRECEDING AND CURRENT ROW)
), k AS (
  SELECT grid_bucket, tz, extract(month FROM date)::int AS month, rock,
         CASE WHEN frozen >= {ICE_MIN_FROZEN} THEN true
              WHEN frozen + ({ICE_WINDOW} - known) < {ICE_MIN_FROZEN} THEN false END AS ice
  FROM d WHERE date >= since
)
SELECT k.grid_bucket, k.tz, k.month, r.rule, count(r.ok) AS n_days, avg(r.ok::int) AS share
FROM k CROSS JOIN LATERAL (VALUES ('rock', k.rock), ('ice', k.ice), ('alpine', k.rock OR k.ice)) AS r(rule, ok)
GROUP BY 1, 2, 3, 4
""")
# A month with no known days must stay missing; with a minimum of 0 an empty month could pass.
if MIN_SEASON_DAYS < 1:
    raise RuntimeError(f"MIN_SEASON_DAYS must be >= 1, got {MIN_SEASON_DAYS}")


async def scope(conn: AsyncConnection) -> Scope:
    out = {(int(a), str(tg)): int(n) for a, tg, n in (await conn.execute(SCOPE_SQL)).all()}
    bad = sorted({tg for _, tg in out} - set(TYPE_GROUPS))
    if bad:
        raise ValueError(f"scorable type groups outside {TYPE_GROUPS}: {bad}")
    return out


def keys(scope_: Scope) -> list[Key]:
    return sorted((a, tg, m) for (a, tg) in scope_ for m in MONTHS)


def route_density(scope_: Scope) -> dict[Key, Component]:
    return {(a, tg, m): Component.present(n) for (a, tg), n in scope_.items() for m in MONTHS}


def season_window(first: date, last: date, *, today: date) -> tuple[date, date] | None:
    """The SEASON_YEARS complete calendar years ending at the last Dec 31 on or before the series'
    last ERA5 day (capped at today). ERA5 is bought once a year, so the window moves only when a
    new year lands, and builds on any day between purchases read identical shares (DP4). None when
    the contiguous ERA5 span [first, last] does not hold that many complete years."""
    edge = min(last, today)
    end_year = edge.year if (edge.month, edge.day) == (12, 31) else edge.year - 1
    since = date(end_year - SEASON_YEARS + 1, 1, 1)
    return (since, date(end_year, 12, 31)) if first <= since else None


async def season(conn: AsyncConnection, scope_: Scope, *, today: date) -> dict[Key, Component]:
    areas = sorted({a for a, _ in scope_})
    points: dict[int, tuple[int, str] | None] = {}
    spans: dict[tuple[int, str], tuple[date, date] | None] = {}
    for a, b, tz, first, last in (await conn.execute(AREA_SERIES_SQL, {"areas": areas})).all():
        if tz is None:
            points[int(a)] = None
            continue
        points[int(a)] = (int(b), str(tz))
        spans[(int(b), str(tz))] = (first, last) if first is not None and last is not None else None
    windows = {k: season_window(v[0], v[1], today=today) for k, v in spans.items() if v is not None}
    wanted = sorted((k, w) for k, w in windows.items() if w is not None)
    shares: dict[tuple[int, str, int, str], tuple[int, float | None]] = {}
    for i in range(0, len(wanted), SERIES_CHUNK):
        chunk = wanted[i : i + SERIES_CHUNK]
        params = {"buckets": [k[0] for k, _ in chunk], "tzs": [k[1] for k, _ in chunk],
                  "sinces": [w[0] for _, w in chunk], "untils": [w[1] for _, w in chunk]}
        for b, tz, m, rule, n, share in (await conn.execute(SEASON_SQL, params)).all():
            shares[(int(b), str(tz), int(m), str(rule))] = (int(n), float(share) if share is not None else None)
    out: dict[Key, Component] = {}
    for a, tg in scope_:
        for m in MONTHS:
            out[(a, tg, m)] = _season_cell(a, points, spans, windows, shares, m, RULE_BY_GROUP[tg])
    return out


def _season_cell(
    area: int,
    points: dict[int, tuple[int, str] | None],
    spans: dict[tuple[int, str], tuple[date, date] | None],
    windows: dict[tuple[int, str], tuple[date, date] | None],
    shares: dict[tuple[int, str, int, str], tuple[int, float | None]],
    month: int,
    rule: str,
) -> Component:
    if area not in points:
        return Component.absent("no_point")
    point = points[area]
    if point is None:
        return Component.absent("no_tz")
    if spans[point] is None:
        return Component.absent("no_era5")
    if windows[point] is None:
        return Component.absent("insufficient_history", n_days=0)
    n, share = shares.get((point[0], point[1], month, rule), (0, None))
    # MIN_SEASON_DAYS counts known days for this calendar month summed across all SEASON_YEARS of
    # the window, not per year. n == 0 or a NULL share is an empty month: missing, never a 0 share.
    if n == 0 or share is None or n < MIN_SEASON_DAYS:
        return Component.absent("insufficient_history", n_days=n)
    return Component.present(share, n_days=n)


STATUS_SQL = text(
    "SELECT s.area_id, s.type_group, c.tick_status, count(*) FROM scorable_routes s "
    "JOIN internal.route_tick_coverage c ON c.route_id = s.route_id GROUP BY 1, 2, 3")
# Each complete route's window is the TICK_WINDOW_MONTHS months ending at the last month that is both
# within complete_through and fully before the cutoff (DP3); route_tick_months already hides every
# month after complete_through, so an absent row inside the window is a real zero.
WINDOW_TICKS_SQL = text(f"""
WITH lim AS (
  SELECT s.route_id, s.area_id, s.type_group,
         LEAST(c.complete_through, (date_trunc('month', CAST(:cutoff AS date)) - interval '1 day')::date) AS last_day
  FROM scorable_routes s JOIN internal.route_tick_coverage c ON c.route_id = s.route_id
  WHERE c.tick_status = 'complete'
), m AS (
  SELECT route_id, to_date(period || '-01', 'YYYY-MM-DD') AS first_day, tick_count FROM internal.route_tick_months
)
SELECT l.area_id, l.type_group, extract(month FROM m.first_day)::int AS month, sum(m.tick_count) AS ticks
FROM lim l JOIN m ON m.route_id = l.route_id
WHERE m.first_day <= l.last_day
  AND m.first_day > (date_trunc('month', l.last_day) - interval '{TICK_WINDOW_MONTHS} months')::date
GROUP BY 1, 2, 3
""")


async def _exists(conn: AsyncConnection, name: str) -> bool:
    return (await conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})).scalar() is True


async def mp_ticks(conn: AsyncConnection, scope_: Scope, *, enabled: bool, cutoff: date) -> dict[Key, Component]:
    if not enabled:
        return {k: Component.absent("disabled") for k in keys(scope_)}
    if not (await _exists(conn, "internal.route_tick_months") and await _exists(conn, "internal.route_tick_coverage")):
        return {k: Component.absent("table_dropped") for k in keys(scope_)}
    statuses: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for a, tg, status, n in (await conn.execute(STATUS_SQL)).all():
        statuses[(int(a), str(tg))][str(status)] = int(n)
    sums = {(int(a), str(tg), int(m)): float(n)
            for a, tg, m, n in (await conn.execute(WINDOW_TICKS_SQL, {"cutoff": cutoff})).all()}
    out: dict[Key, Component] = {}
    for (a, tg), n_routes in scope_.items():
        st = statuses[(a, tg)]
        # A cell with no complete route gets missing from tick_component; the 0.0 default is read
        # only when n_complete >= 1, where an absent sum is the seam's measured zero.
        for m in MONTHS:
            out[(a, tg, m)] = tick_component(
                n_routes=n_routes, n_complete=st.get("complete", 0), n_scraped=st.get("scraped", 0),
                n_missing=st.get("missing", 0), window_ticks=sums.get((a, tg, m), 0.0))
    return out
