"""Exposure component readers: one set-based query per component over the MP catalog (cells are
areas with directly attached scorable routes, DP5). Each returns a Component for every key of the
scope, so assembly fails loudly on a gap instead of filling one."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.exposure_math import (
    ICE_MIN_FROZEN, ICE_WINDOW, MIN_SEASON_DAYS, MONTHS, ROCK_MAX_PRECIP, ROCK_TMAX_HI, ROCK_TMAX_LO,
    RULE_BY_GROUP, SEASON_YEARS, TYPE_GROUPS, Component,
)

Key = tuple[int, str, int]
Scope = dict[tuple[int, str], int]
SERIES_CHUNK = 500

SCOPE_SQL = text("SELECT area_id, type_group, count(*) FROM scorable_routes GROUP BY 1, 2")
AREA_SERIES_SQL = text(
    "SELECT a.area_id, f.grid_bucket, f.tz FROM canonical_areas a JOIN feature_points f ON f.point_key = a.point_key "
    "WHERE a.area_id = ANY(CAST(:areas AS bigint[]))")
# Window frame by calendar days: a day absent from the series is unknown, and ice_day's
# (ICE_WINDOW - known) term counts it so, exactly as exposure_math.ice_day does.
SEASON_SQL = text(f"""
WITH s AS (
  SELECT * FROM unnest(CAST(:buckets AS integer[]), CAST(:tzs AS text[])) AS s(grid_bucket, tz)
), d AS (
  SELECT c.grid_bucket, c.tz, c.date,
         CASE WHEN c.tmax IS NULL OR c.precip_mm IS NULL THEN NULL
              ELSE c.tmax BETWEEN {ROCK_TMAX_LO} AND {ROCK_TMAX_HI} AND c.precip_mm < {ROCK_MAX_PRECIP} END AS rock,
         count(*) FILTER (WHERE c.tmax < 0) OVER w AS frozen,
         count(c.tmax) OVER w AS known
  FROM cell_daily_conditions c JOIN s ON s.grid_bucket = c.grid_bucket AND s.tz = c.tz
  WHERE c.record_kind = 'era5' AND c.date >= :since AND c.date < :until
  WINDOW w AS (PARTITION BY c.grid_bucket, c.tz ORDER BY c.date
               RANGE BETWEEN INTERVAL '{ICE_WINDOW - 1} days' PRECEDING AND CURRENT ROW)
), k AS (
  SELECT grid_bucket, tz, extract(month FROM date)::int AS month, rock,
         CASE WHEN frozen >= {ICE_MIN_FROZEN} THEN true
              WHEN frozen + ({ICE_WINDOW} - known) < {ICE_MIN_FROZEN} THEN false END AS ice
  FROM d
)
SELECT k.grid_bucket, k.tz, k.month, r.rule, count(r.ok) AS n_days, avg(r.ok::int) AS share
FROM k CROSS JOIN LATERAL (VALUES ('rock', k.rock), ('ice', k.ice), ('alpine', k.rock OR k.ice)) AS r(rule, ok)
GROUP BY 1, 2, 3, 4
""")


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


async def season(conn: AsyncConnection, scope_: Scope, *, today: date) -> dict[Key, Component]:
    """Time-invariant climate covariate (DP4): the latest SEASON_YEARS of ERA5 days before today,
    whatever the snapshot's data cutoff, because era5_window prunes older non-incident rows."""
    areas = sorted({a for a, _ in scope_})
    series: dict[int, tuple[int, str] | None] = {
        int(a): ((int(b), str(tz)) if tz is not None else None)
        for a, b, tz in (await conn.execute(AREA_SERIES_SQL, {"areas": areas})).all()
    }
    wanted = sorted({s for s in series.values() if s is not None})
    since = today - timedelta(days=SEASON_YEARS * 365)
    shares: dict[tuple[int, str, int, str], tuple[int, float | None]] = defaultdict(lambda: (0, None))
    for i in range(0, len(wanted), SERIES_CHUNK):
        chunk = wanted[i : i + SERIES_CHUNK]
        rows = (await conn.execute(SEASON_SQL, {"buckets": [b for b, _ in chunk], "tzs": [t for _, t in chunk],
                                                 "since": since, "until": today})).all()
        for b, tz, m, rule, n, share in rows:
            shares[(int(b), str(tz), int(m), str(rule))] = (int(n), float(share) if share is not None else None)
    out: dict[Key, Component] = {}
    for a, tg in scope_:
        point = series.get(a)
        for m in MONTHS:
            if a not in series:
                out[(a, tg, m)] = Component.absent("no_point")
            elif point is None:
                out[(a, tg, m)] = Component.absent("no_tz")
            else:
                n, share = shares[(point[0], point[1], m, RULE_BY_GROUP[tg])]
                out[(a, tg, m)] = (Component.present(share, n_days=n) if share is not None and n >= MIN_SEASON_DAYS
                                   else Component.absent("insufficient_history", n_days=n))
    return out
