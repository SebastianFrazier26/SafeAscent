"""Monthly climate normals and day-of-year climatology per 0.1° bucket (amendment §3.1, D6/D7)
from the trailing 10 complete ERA5 years, recomputed in every January Professional window. The
period moves with the run date; a year is complete once ERA5's ~5-day lag past 31 December has
passed. Every value is on the series' local calendar days (the request's or the stored series'
IANA zone), the same days cell_daily_conditions holds.

Missing is never 0. Monthly: a year's value for one variable needs >= 90% of that month's days
observed for it; a few missing days scale a total to the full month (sum x days / observed),
more drop that year for that variable; a normal needs >= 8 usable years per variable, else it
is NULL. Temperatures and freeze-thaw count only days with both tmax and tmin. Day of year: each
row pools its calendar day +-7 days in every year; a year is usable for a variable when >= 90%
of its in-period window days are observed, and a statistic needs >= 8 usable years, else NULL.

OQ6: a bucket whose stored series already holds every day of a year as era5 rows (stopgap and
forecast rows never count) derives that year from cell_daily_conditions; only the remaining years
are bought, as one contiguous range per bucket, and the bought days are used for the normals
only, never written as conditions rows. A normal built from stored rows alone is
'derived_cell_daily_conditions'; one that needed any bought year is 'open_meteo_era5_seamless'.
"""

from __future__ import annotations

import calendar
import statistics
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.cell_conditions import day_rows
from app.pipelines.era5_fill import MAX_QUARANTINE_SHARE
from app.pipelines.grid import bucket_center
from app.pipelines.ingest_log import RunStatus, finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.open_meteo import ARCHIVE_MODEL, DAILY_VARS, ArchiveClient, Location, LocationResponse, cost_units
from app.pipelines.pro_window import (
    Budget,
    Clock,
    PaidFetch,
    ProWindow,
    paid_fetch,
    record_spend,
    units_spent,
    window_lock,
)
from app.pipelines.validate import ValidationReport, batch_gate

NORMALS_VERSION = "n-v2"
CLIMATOLOGY_VERSION = "c-v1"
SOURCE = "era5_normals"
BOUGHT_SOURCE = "open_meteo_era5_seamless"
DERIVED_SOURCE = "derived_cell_daily_conditions"
YEARS = 10
MIN_YEARS = 8
MIN_DAY_SHARE = 0.9
ERA5_LAG_DAYS = 5
DOY_HALF_WINDOW = 7

_VALUE_FIELDS = ("tmax_mean", "tmin_mean", "precip_mm", "snowfall_cm", "freeze_thaw_days")


@dataclass(frozen=True)
class MonthNormal:
    month: int
    tmax_mean: float | None
    tmin_mean: float | None
    precip_mm: float | None
    snowfall_cm: float | None
    freeze_thaw_days: float | None
    n_years: int


def window(today: date) -> tuple[int, int]:
    last_complete = today.year - 1 if today - timedelta(days=ERA5_LAG_DAYS) >= date(today.year, 1, 1) else today.year - 2
    return last_complete - YEARS + 1, last_complete


def calendar_doy(day: date) -> int:
    """Day of year on a leap-year calendar: 1 March is 61 in every year, so a cell_climatology
    row is always the same calendar date and doy 60 (29 February) pools leap years' own day.
    Phase 3 must look rows up with this function, not timetuple().tm_yday."""
    return date(2000, day.month, day.day).timetuple().tm_yday


Dense = tuple[list[date], dict[str, list[float | None]]]


def _dense(response: LocationResponse) -> Dense:
    """Every day from the first to the last returned, None where the response skipped a day:
    an absent day must count against the 90% rule, not shrink what is expected."""
    d = response.daily
    if not d.time:
        return [], {var: [] for var in DAILY_VARS}
    first = d.time[0]
    days = [first + timedelta(i) for i in range((d.time[-1] - first).days + 1)]
    values: dict[str, list[float | None]] = {var: [None] * len(days) for var in DAILY_VARS}
    for i, day in enumerate(d.time):
        for var in DAILY_VARS:
            values[var][(day - first).days] = getattr(d, var)[i]
    return days, values


def _scaled_total(values: list[float | None], days_in_month: int) -> float | None:
    present = [v for v in values if v is not None]
    if len(present) < MIN_DAY_SHARE * days_in_month:
        return None
    return sum(present) * days_in_month / len(present)


def _normal(per_year: list[float]) -> float | None:
    return statistics.fmean(per_year) if len(per_year) >= MIN_YEARS else None


def monthly_normals(response: LocationResponse) -> list[MonthNormal]:
    days, v = _dense(response)
    per: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, day in enumerate(days):
        per[(day.year, day.month)].append(i)
    tmax_v, tmin_v = v["temperature_2m_max"], v["temperature_2m_min"]
    out: list[MonthNormal] = []
    for month in range(1, 13):
        tmax_y: list[float] = []
        tmin_y: list[float] = []
        ft_y: list[float] = []
        precip_y: list[float] = []
        snow_y: list[float] = []
        for (year, m), idx in sorted(per.items()):
            if m != month:
                continue
            days_in_month = calendar.monthrange(year, month)[1]
            # A month cut by the period's edge is not a usable year.
            if len(idx) != days_in_month:
                continue
            pairs = [(a, b) for i in idx if (a := tmax_v[i]) is not None and (b := tmin_v[i]) is not None]
            if len(pairs) >= MIN_DAY_SHARE * days_in_month:
                tmax_y.append(statistics.fmean(a for a, _ in pairs))
                tmin_y.append(statistics.fmean(b for _, b in pairs))
                ft_y.append(sum(1 for a, b in pairs if a > 0 and b < 0) * days_in_month / len(pairs))
            if (p := _scaled_total([v["precipitation_sum"][i] for i in idx], days_in_month)) is not None:
                precip_y.append(p)
            if (s := _scaled_total([v["snowfall_sum"][i] for i in idx], days_in_month)) is not None:
                snow_y.append(s)
        out.append(MonthNormal(month, _normal(tmax_y), _normal(tmin_y), _normal(precip_y), _normal(snow_y),
                               _normal(ft_y), len(tmax_y)))
    return out


def bucket_status(normals: Sequence[MonthNormal]) -> str:
    complete = all(None not in tuple(getattr(n, f) for f in _VALUE_FIELDS) for n in normals)
    return "complete" if complete and len(normals) == 12 else "insufficient"


_CLIM_SPEC: tuple[tuple[str, str | None, tuple[str, ...]], ...] = (
    ("tmax", "temperature_2m_max", ("mean", "p10", "p90")),
    ("tmin", "temperature_2m_min", ("mean", "p10", "p90")),
    ("precip", "precipitation_sum", ("mean", "p90")),
    ("snowfall", "snowfall_sum", ("mean",)),
    ("gust", "wind_gusts_10m_max", ("p90",)),
    ("freeze_thaw", None, ("freq",)),
)


def _stat(sample: NDArray[np.float64], kind: str) -> float:
    if kind in ("mean", "freq"):
        return float(sample.mean())
    # numpy's default linear interpolation (Hyndman-Fan type 7).
    return float(np.percentile(sample, 10 if kind == "p10" else 90))


def _empty_clim_row(doy: int) -> dict[str, float | int | None]:
    row: dict[str, float | int | None] = {"doy": doy, "lightning_day_freq": None, "n_years": 0}
    for key, _, kinds in _CLIM_SPEC:
        row.update({f"{key}_{kind}": None for kind in kinds})
    return row


def doy_climatology(response: LocationResponse) -> list[dict[str, float | int | None]]:
    """Per calendar day (calendar_doy), pool that day +-7 days in every year (~150 samples for 10
    years) so the 10th/90th percentiles are stable. Missing days are NaN and excluded, never 0.
    Rows near the new year pool across it; an edge year contributes the window days inside the
    period. Statistics: arithmetic mean; p10/p90 by linear interpolation; freeze_thaw_freq is the
    share of pooled days with tmax > 0 and tmin < 0. n_years counts tmax's usable years."""
    days, v = _dense(response)
    if not days:
        return [_empty_clim_row(doy) for doy in range(1, 367)]
    first_year = days[0].year
    span = days[-1].year - first_year + 1
    cdoy = np.array([calendar_doy(t) for t in days])
    years = np.array([t.year for t in days])
    series = {key: np.array([np.nan if x is None else x for x in v[var]], dtype=np.float64)
              for key, var, _ in _CLIM_SPEC if var is not None}
    tmax, tmin = series["tmax"], series["tmin"]
    series["freeze_thaw"] = np.where(np.isnan(tmax) | np.isnan(tmin), np.nan,
                                     ((tmax > 0) & (tmin < 0)).astype(np.float64))
    rows: list[dict[str, float | int | None]] = []
    for doy in range(1, 367):
        delta = cdoy - doy
        in_window = np.abs((delta + 183) % 366 - 183) <= DOY_HALF_WINDOW
        # 28 December in a 3 January window belongs to the next year's instance of that window.
        slot = years + np.where(delta > 183, 1, np.where(delta < -183, -1, 0)) - first_year
        selected = in_window & (slot >= 0) & (slot < span)
        safe_slot = np.clip(slot, 0, span - 1)
        expected = np.bincount(slot[selected], minlength=span)
        row = _empty_clim_row(doy)
        for key, _, kinds in _CLIM_SPEC:
            values = series[key]
            observed = selected & ~np.isnan(values)
            present = np.bincount(slot[observed], minlength=span)
            usable = (expected > 0) & (present >= MIN_DAY_SHARE * expected)
            if key == "tmax":
                row["n_years"] = int(usable.sum())
            if usable.sum() < MIN_YEARS:
                continue
            sample = values[observed & usable[safe_slot]]
            for kind in kinds:
                row[f"{key}_{kind}"] = _stat(sample, kind)
        rows.append(row)
    return rows


@dataclass(frozen=True)
class BucketPlan:
    grid_bucket: int
    tz: str
    buy: tuple[int, int] | None


def plan_buckets(
    due: Sequence[tuple[int, str]], complete_years: dict[int, set[int]], start_year: int, end_year: int
) -> list[BucketPlan]:
    """One contiguous bought range per bucket, covering every year not fully stored. A stored
    year inside that range is bought again: era5 spans are contiguous, so this is rare, and one
    request per bucket keeps the batching simple."""
    plans: list[BucketPlan] = []
    for b, tz in due:
        missing = [y for y in range(start_year, end_year + 1) if y not in complete_years.get(b, set())]
        plans.append(BucketPlan(b, tz, (missing[0], missing[-1]) if missing else None))
    return plans


async def stored_complete_years(
    conn: AsyncConnection, due: Sequence[tuple[int, str]], start_year: int, end_year: int
) -> dict[int, set[int]]:
    if not due:
        return {}
    rows = (await conn.execute(text(
        "SELECT c.grid_bucket, extract(year FROM c.date)::int AS y, count(*) FROM cell_daily_conditions c "
        "JOIN unnest(CAST(:bs AS int[]), CAST(:tzs AS text[])) AS d(b, tz) ON c.grid_bucket = d.b AND c.tz = d.tz "
        "WHERE c.record_kind = 'era5' AND c.date BETWEEN :s AND :e GROUP BY 1, 2"
    ), {"bs": [b for b, _ in due], "tzs": [tz for _, tz in due],
        "s": date(start_year, 1, 1), "e": date(end_year, 12, 31)})).all()
    out: dict[int, set[int]] = defaultdict(set)
    for b, y, n in rows:
        if n == (366 if calendar.isleap(y) else 365):
            out[int(b)].add(int(y))
    return out


_STORED_SQL = text(
    "SELECT date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, gust_max_ms FROM cell_daily_conditions "
    "WHERE grid_bucket = :b AND tz = :tz AND record_kind = 'era5' AND date BETWEEN :s AND :e"
)

DayValues = tuple[float | None, float | None, float | None, float | None, float | None, float | None]


def _series(b: int, tz: str, start: date, end: date, by_day: dict[date, DayValues], elevation: float | None) -> LocationResponse:
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    daily: dict[str, object] = {"time": days}
    for k, var in enumerate(DAILY_VARS):
        daily[var] = [by_day[d][k] if d in by_day else None for d in days]
    lat, lon = bucket_center(b)
    return LocationResponse.model_validate({"latitude": lat, "longitude": lon, "elevation": elevation,
                                            "timezone": tz, "daily": daily})


CLIM_UPSERT = text(
    "INSERT INTO cell_climatology (grid_bucket, doy, tmax_mean, tmax_p10, tmax_p90, tmin_mean, tmin_p10, tmin_p90, "
    "precip_mean, precip_p90, snowfall_mean, gust_p90, freeze_thaw_freq, n_years, period_start_year, period_end_year, "
    "climatology_version, run_id) VALUES (:b, :doy, :tmax_mean, :tmax_p10, :tmax_p90, :tmin_mean, :tmin_p10, :tmin_p90, "
    ":precip_mean, :precip_p90, :snowfall_mean, :gust_p90, :freeze_thaw_freq, :n_years, :ps, :pe, :cv, :run) "
    "ON CONFLICT (grid_bucket, doy) DO UPDATE SET tmax_mean = EXCLUDED.tmax_mean, tmax_p10 = EXCLUDED.tmax_p10, "
    "tmax_p90 = EXCLUDED.tmax_p90, tmin_mean = EXCLUDED.tmin_mean, tmin_p10 = EXCLUDED.tmin_p10, tmin_p90 = EXCLUDED.tmin_p90, "
    "precip_mean = EXCLUDED.precip_mean, precip_p90 = EXCLUDED.precip_p90, snowfall_mean = EXCLUDED.snowfall_mean, "
    "gust_p90 = EXCLUDED.gust_p90, freeze_thaw_freq = EXCLUDED.freeze_thaw_freq, n_years = EXCLUDED.n_years, "
    "period_start_year = EXCLUDED.period_start_year, period_end_year = EXCLUDED.period_end_year, "
    "climatology_version = EXCLUDED.climatology_version, run_id = EXCLUDED.run_id"
)
# lightning_day_freq is deliberately absent from the upsert: plan 7 owns that column.

# ref_elevation_m is Open-Meteo's DEM height at the bucket centre, the height its series is
# downscaled to; a derived normal has no response to read it from, so it keeps the last one known.
UPSERT = text(
    "INSERT INTO cell_climate_normals (grid_bucket, month, tmax_mean, tmin_mean, precip_mm, snowfall_cm, "
    "freeze_thaw_days, n_years, period_start_year, period_end_year, ref_elevation_m, source, normals_version, run_id) "
    "VALUES (:b, :month, :tmax_mean, :tmin_mean, :precip_mm, :snowfall_cm, :freeze_thaw_days, :n_years, :ps, :pe, "
    ":elev, :source, :v, :run) ON CONFLICT (grid_bucket, month) DO UPDATE SET "
    "tmax_mean = EXCLUDED.tmax_mean, tmin_mean = EXCLUDED.tmin_mean, precip_mm = EXCLUDED.precip_mm, "
    "snowfall_cm = EXCLUDED.snowfall_cm, freeze_thaw_days = EXCLUDED.freeze_thaw_days, n_years = EXCLUDED.n_years, "
    "period_start_year = EXCLUDED.period_start_year, period_end_year = EXCLUDED.period_end_year, "
    "ref_elevation_m = COALESCE(EXCLUDED.ref_elevation_m, cell_climate_normals.ref_elevation_m), "
    "source = EXCLUDED.source, normals_version = EXCLUDED.normals_version, run_id = EXCLUDED.run_id"
)

STATUS_UPSERT = text(
    "INSERT INTO cell_normals_status (grid_bucket, status, computed_at, n_years, run_id) "
    "VALUES (:b, :status, now(), :n, :run) ON CONFLICT (grid_bucket) DO UPDATE SET status = EXCLUDED.status, "
    "computed_at = EXCLUDED.computed_at, n_years = EXCLUDED.n_years, run_id = EXCLUDED.run_id"
)


async def normals_due(conn: AsyncConnection, *, today: date) -> list[tuple[int, str]]:
    """Buckets that are pending, or whose normals predate this period or version. A bucket on a
    timezone line takes the series with the longest stored ERA5 history (then the alphabetically
    first zone), so the stored-row path applies whenever any of its zones allows it; month-scale
    values barely move with a one-hour day boundary."""
    _, end_year = window(today)
    rows = (await conn.execute(text(
        "SELECT st.grid_bucket, (array_agg(s.tz ORDER BY s.first_era5_date NULLS LAST, s.tz))[1] "
        "FROM cell_normals_status st JOIN grid_bucket_series s ON s.grid_bucket = st.grid_bucket "
        "WHERE st.status = 'pending' OR NOT EXISTS (SELECT 1 FROM cell_climate_normals n "
        "WHERE n.grid_bucket = st.grid_bucket AND n.period_end_year = :pe AND n.normals_version = :v) "
        "GROUP BY st.grid_bucket ORDER BY st.grid_bucket"
    ), {"pe": end_year, "v": NORMALS_VERSION})).all()
    return [(int(b), str(tz)) for b, tz in rows]


async def _write_batch(
    engine: AsyncEngine,
    plans: Sequence[BucketPlan],
    *,
    run_id: uuid.UUID | None,
    fetched: PaidFetch | None,
    buy: tuple[date, date] | None,
    period: tuple[int, int],
    today: date,
    units: float,
) -> RunStatus:
    """Validate, compute and write one batch, its quarantine rows and its final log status in one
    transaction. A derived batch (run_id None) also opens its log row here; a paid batch's row
    was committed with its spend before the request went out."""
    start_year, end_year = period
    p_start, p_end = date(start_year, 1, 1), date(end_year, 12, 31)
    report = ValidationReport(SOURCE)
    problems: list[str] = []
    failed = False
    bought: dict[int, tuple[dict[date, DayValues], float | None]] = {}
    if fetched is not None and buy is not None:
        if fetched.problem is not None:
            failed, problems = True, [fetched.problem]
        else:
            want = (buy[1] - buy[0]).days + 1
            for plan, response in zip(plans, fetched.responses):
                if len(response.daily.time) != want:
                    failed = True
                    problems.append(f"bucket {plan.grid_bucket}: response does not cover every day of {buy[0]}..{buy[1]}")
                    continue
                location = ValidationReport(SOURCE)
                rows = day_rows(plan.grid_bucket, plan.tz, response, today=today, origin="archive",
                                model=ARCHIVE_MODEL, source="open_meteo_archive", report=location)
                if location.quarantined.get("timezone_mismatch"):
                    failed = True
                    problems.append(f"bucket {plan.grid_bucket}: response timezone differs from the series timezone")
                else:
                    problems.extend(f"bucket {plan.grid_bucket}: {p}" for p in batch_gate(
                        location, previous_rows_in=None, count_tolerance=0.0, max_quarantine_share=MAX_QUARANTINE_SHARE))
                report.rows_in += location.rows_in
                report.accepted += location.accepted
                report.quarantined.update(location.quarantined)
                report.issues.extend(location.issues)
                bought[plan.grid_bucket] = ({r.date: (r.tmax, r.tmin, r.precip_mm, r.snowfall_cm, r.wind_max_ms,
                                                      r.gust_max_ms) for r in rows}, response.elevation)
    status: RunStatus = "failed" if failed else "rejected" if problems else "ok"
    async with engine.begin() as conn:
        if run_id is None:
            run_id = await start_run(conn, source=SOURCE, window_start=p_start, window_end=p_end,
                                     content_sha256=sha256_rows([(p.grid_bucket, p.tz) for p in plans]
                                                                + [("v", NORMALS_VERSION, start_year, end_year)]))
        n = 0
        if status == "ok":
            normal_rows: list[dict[str, object]] = []
            clim_rows: list[dict[str, object]] = []
            statuses: list[dict[str, object]] = []
            for plan in plans:
                stored = (await conn.execute(_STORED_SQL, {"b": plan.grid_bucket, "tz": plan.tz, "s": p_start,
                                                            "e": p_end})).all()
                by_day: dict[date, DayValues] = {r[0]: (r[1], r[2], r[3], r[4], r[5], r[6]) for r in stored}
                elevation: float | None = None
                if plan.grid_bucket in bought:
                    days, elevation = bought[plan.grid_bucket]
                    assert buy is not None
                    by_day = {d: x for d, x in by_day.items() if not buy[0] <= d <= buy[1]} | days
                series = _series(plan.grid_bucket, plan.tz, p_start, p_end, by_day, elevation)
                normals = monthly_normals(series)
                source = BOUGHT_SOURCE if plan.buy is not None else DERIVED_SOURCE
                for m in normals:
                    missing = [f for f in _VALUE_FIELDS if getattr(m, f) is None]
                    if missing:
                        report.quarantine(f"{plan.grid_bucket}:{m.month}", "too_few_years", missing=missing,
                                          n_years=m.n_years)
                    else:
                        report.accept()
                    normal_rows.append({"b": plan.grid_bucket, "month": m.month, "tmax_mean": m.tmax_mean,
                                        "tmin_mean": m.tmin_mean, "precip_mm": m.precip_mm,
                                        "snowfall_cm": m.snowfall_cm, "freeze_thaw_days": m.freeze_thaw_days,
                                        "n_years": m.n_years, "ps": start_year, "pe": end_year, "elev": elevation,
                                        "source": source, "v": NORMALS_VERSION, "run": run_id})
                clim_rows += [{k: c[k] for k in c if k != "lightning_day_freq"}
                              | {"b": plan.grid_bucket, "ps": start_year, "pe": end_year, "cv": CLIMATOLOGY_VERSION,
                                 "run": run_id} for c in doy_climatology(series)]
                statuses.append({"b": plan.grid_bucket, "status": bucket_status(normals),
                                 "n": min(m.n_years for m in normals), "run": run_id})
            await conn.execute(UPSERT, normal_rows)
            await conn.execute(CLIM_UPSERT, clim_rows)
            await conn.execute(STATUS_UPSERT, statuses)
            n = len(normal_rows)
        await write_quarantine(conn, run_id, report)
        await finish_run(conn, run_id, status=status, report=report, rows_upserted=n,
                         problems=problems or None, cost_units=units)
    return status


async def run(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    due: Sequence[tuple[int, str]],
    *,
    today: date,
    batch: int,
    budget: Budget,
    pro_window: ProWindow,
    clock: Clock | None = None,
) -> dict[str, object]:
    """Recompute the due buckets under the window lock: stored-row buckets first (free), then
    the paid batches in order until the cap would be passed. Each paid request's units are
    recorded before it is sent and re-read from the log before every batch, exactly as
    era5_fill does; batches run one after another, never gathered. A failed or rejected batch
    writes no normals, so its buckets stay due for the next run."""
    if batch < 1:
        raise ValueError(f"batch must be at least 1, got {batch}")
    period = window(today)
    engine = engine_factory()
    derived = bought = failed = rejected = batches = 0
    billed_total = 0.0
    stopped = False
    try:
        async with window_lock(engine):
            async with engine.connect() as conn:
                budget.spent = max(budget.spent, await units_spent(conn, pro_window))
                complete = await stored_complete_years(conn, due, *period)
            plans = plan_buckets(due, complete, *period)
            free = [p for p in plans if p.buy is None]
            for i in range(0, len(free), batch):
                group = free[i : i + batch]
                batches += 1
                if await _write_batch(engine, group, run_id=None, fetched=None, buy=None, period=period,
                                      today=today, units=0) == "ok":
                    derived += len(group)
            paid: dict[tuple[str, tuple[int, int]], list[BucketPlan]] = defaultdict(list)
            for p in plans:
                if p.buy is not None:
                    paid[(p.tz, p.buy)].append(p)
            for (tz, (y0, y1)), members in sorted(paid.items()):
                members.sort(key=lambda p: p.grid_bucket)
                for i in range(0, len(members), batch):
                    group = members[i : i + batch]
                    # As in era5_fill: a silently dropped lock session lets another job in.
                    async with engine.connect() as conn:
                        budget.spent = max(budget.spent, await units_spent(conn, pro_window))
                    start, end = date(y0, 1, 1), date(y1, 12, 31)
                    units = cost_units(len(group), len(DAILY_VARS), (end - start).days + 1)
                    if not budget.fits(units):
                        stopped = True
                        break
                    batches += 1
                    at = clock() if clock else None
                    # Deliberately committed before the request, apart from finish_run (D5).
                    async with engine.begin() as conn:
                        run_id = await start_run(
                            conn, source=SOURCE, window_start=date(period[0], 1, 1), window_end=date(period[1], 12, 31),
                            content_sha256=sha256_rows([(tz, y0, y1)] + [(p.grid_bucket,) for p in group]
                                                       + [("v", NORMALS_VERSION)]))
                        await record_spend(conn, run_id, units, at=at)
                    budget.spend(units)
                    fetched = await paid_fetch(engine, client, [Location(p.grid_bucket, *bucket_center(p.grid_bucket), tz)
                                                                for p in group],
                                               start, end, run_id=run_id, units=units, budget=budget, at=at)
                    billed_total += fetched.billed
                    status = await _write_batch(engine, group, run_id=run_id, fetched=fetched, buy=(start, end),
                                                period=period, today=today, units=fetched.billed)
                    if status == "ok":
                        bought += len(group)
                    elif status == "failed":
                        failed += 1
                    else:
                        rejected += 1
                    if fetched.budget_exhausted:
                        stopped = True
                        break
                if stopped:
                    break
    finally:
        await engine.dispose()
    return {"period": list(period), "due": len(due), "batches": batches, "derived_buckets": derived,
            "bought_buckets": bought, "done": derived + bought, "failed": failed, "rejected": rejected,
            "units_billed": billed_total, "stopped_for_budget": stopped}
