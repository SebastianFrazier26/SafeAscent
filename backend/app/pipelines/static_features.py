"""Static features per distinct point and the per-route table Phase 3 reads, on the MP
catalog. Points are the stored canonical_areas.point_key of areas that carry a non-boulder
route; a point's coordinates are parsed back from its key, so every consumer sees the same
5-decimal location. Transient DEM failures leave a point unfilled for the next run.
Missing is never 0: unknown elevation, slope and aspect stay NULL.

run_step never holds a DB transaction across network I/O (3DEP, EPQS): it reads in one short
transaction, samples with no transaction open, then writes and logs in another, so a crash
mid-sample writes nothing."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import uuid
from collections.abc import Callable, Sequence

import h3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.dem import Elevation, sample_points
from app.pipelines.epqs import (
    PER_CLASS_TARGET,
    SLOPE_CLASSES,
    EpqsClient,
    compare,
    failure_guard,
    local_slopes,
    slope_class,
)
from app.pipelines.grid import grid_bucket
from app.pipelines.localday import tz_for_point
from app.pipelines.validate import ValidationReport

FEATURE_VERSION = "f-v1"
# Longer than any single route; MP lengths past this are typos (feet entered as metres, extra digits).
MAX_LENGTH_M = 3000.0
FT_TO_M = 0.3048

Sampler = Callable[[Sequence[tuple[float, float]]], list[Elevation | None]]

POINTS_SQL = text(
    "SELECT DISTINCT a.point_key FROM canonical_areas a WHERE a.point_key IS NOT NULL "
    "AND EXISTS (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id AND NOT r.is_boulder)"
)
POINT_UPSERT = text(
    "INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version, run_id) "
    "VALUES (:k, :lat, :lon, :b, :r5, :r7, :tz, :v, :run) "
    "ON CONFLICT (point_key) DO UPDATE SET tz = COALESCE(feature_points.tz, EXCLUDED.tz)"
)
REGISTER_STATUS = text(
    "INSERT INTO cell_normals_status (grid_bucket, status) SELECT DISTINCT grid_bucket, 'pending' FROM feature_points "
    "ON CONFLICT (grid_bucket) DO NOTHING"
)
REGISTER_SERIES = text(
    "INSERT INTO grid_bucket_series (grid_bucket, tz) SELECT DISTINCT grid_bucket, tz FROM feature_points "
    "WHERE tz IS NOT NULL ON CONFLICT (grid_bucket, tz) DO NOTHING"
)
STORED_TZ_SQL = text("SELECT point_key, tz FROM feature_points WHERE tz IS NOT NULL")
# Least recently attempted first: a transient failure bumps updated_at (see RETRY_TOUCH), so points
# that keep erroring go to the back of the queue instead of filling every --limit batch.
PENDING_SQL = text(
    "SELECT point_key, lat, lon FROM feature_points WHERE dem_source IS NULL ORDER BY updated_at, point_key LIMIT :n"
)
ELEVATION_UPDATE = text(
    "UPDATE feature_points SET elevation_m = :e, dem_source = :s, dem_res_m = :r, updated_at = now(), "
    "run_id = :run WHERE point_key = :k AND dem_source IS NULL"
)
RETRY_TOUCH = text(
    "UPDATE feature_points SET updated_at = clock_timestamp() WHERE point_key = ANY(:keys) AND dem_source IS NULL"
)
MISSING_POINTS_SQL = text(
    "SELECT count(*), min(r.route_id) FROM canonical_routes r JOIN canonical_areas a ON a.area_id = r.area_id "
    "WHERE r.scorable AND (a.point_key IS NULL "
    "OR NOT EXISTS (SELECT 1 FROM feature_points p WHERE p.point_key = a.point_key))"
)
IMPLAUSIBLE_LENGTH_SQL = text(
    "SELECT r.route_id, m.length_ft FROM canonical_routes r JOIN mp_routes m ON m.mp_route_id = r.route_id "
    "WHERE NOT r.is_boulder AND m.length_ft IS NOT NULL AND m.length_ft <> 0 "
    "AND NOT (m.length_ft > 0 AND m.length_ft * :ft <= :max_m) ORDER BY r.route_id"
)
EPQS_POINTS_SQL = text(
    "SELECT lat, lon, elevation_m FROM feature_points WHERE elevation_m IS NOT NULL ORDER BY point_key"
)


def _parse_key(key: str) -> tuple[float, float]:
    lat, lon = key.split(":")
    return float(lat), float(lon)


async def upsert_points(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    report: ValidationReport,
    tz_lookup: Callable[[float, float], str | None] = tz_for_point,
) -> int:
    keys = sorted(str(k) for (k,) in (await conn.execute(POINTS_SQL)).all())
    stored = {str(k): str(z) for k, z in (await conn.execute(STORED_TZ_SQL)).all()}
    rows: list[dict[str, object]] = []
    for key in keys:
        lat, lon = _parse_key(key)
        # A stored zone is kept (the upsert never overwrites it), so only a point with none is judged.
        tz = stored.get(key) or tz_lookup(lat, lon)
        if tz is None:
            report.quarantine(key, "no_timezone")
        else:
            report.accept()
        rows.append({"k": key, "lat": lat, "lon": lon, "b": grid_bucket(lat, lon), "r5": h3.latlng_to_cell(lat, lon, 5),
                     "r7": h3.latlng_to_cell(lat, lon, 7), "tz": tz, "v": FEATURE_VERSION, "run": run_id})
    if rows:
        await conn.execute(POINT_UPSERT, rows)
    await conn.execute(REGISTER_STATUS)
    await conn.execute(REGISTER_SERIES)
    return len(rows)


Pending = list[tuple[str, float, float]]


async def pending_points(conn: AsyncConnection, limit: int) -> Pending:
    return [(str(k), float(la), float(lo)) for k, la, lo in (await conn.execute(PENDING_SQL, {"n": limit})).all()]


def sample_pending(todo: Pending, sampler: Sampler) -> list[Elevation | None]:
    values = sampler([(la, lo) for _, la, lo in todo]) if todo else []
    if len(values) != len(todo):
        raise ValueError(f"sampler returned {len(values)} values for {len(todo)} points")
    return values


async def write_elevation(
    conn: AsyncConnection, *, run_id: uuid.UUID, todo: Pending, values: Sequence[Elevation | None]
) -> dict[str, int]:
    """Store sampled values. A None is a transient DEM error: the point keeps dem_source NULL and
    is retried on a later run, after every point attempted less recently.

    'nodata' and 'no_tile' are terminal: such a point is never sampled again. To re-queue them
    (e.g. after USGS publishes a missing tile), run as ingest:
    UPDATE feature_points SET dem_source = NULL, elevation_m = NULL, dem_res_m = NULL WHERE dem_source = 'no_tile';"""
    done = [(k, v) for (k, _, _), v in zip(todo, values, strict=True) if v is not None]
    retry = [k for (k, _, _), v in zip(todo, values, strict=True) if v is None]
    if done:
        await conn.execute(ELEVATION_UPDATE, [{"e": v.elevation_m, "s": v.dem_source, "r": v.dem_res_m, "run": run_id,
                                               "k": k} for k, v in done])
    if retry:
        await conn.execute(RETRY_TOUCH, {"keys": retry})
    return {"sampled": len(done), "nodata": sum(1 for _, v in done if v.dem_source == "nodata"),
            "no_tile": sum(1 for _, v in done if v.dem_source == "no_tile"), "retry": len(retry)}


async def fill_elevation(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    sampler: Sampler = sample_points,
    limit: int,
) -> dict[str, int]:
    """Single-connection form for tests and callers that own the transaction; it samples inside
    that transaction. run_step splits the same three calls across two short transactions."""
    todo = await pending_points(conn, limit)
    return await write_elevation(conn, run_id=run_id, todo=todo, values=sample_pending(todo, sampler))


BUILD_SQL = text("""
INSERT INTO route_static_features (route_id, source, area_path, type_group, point_key, lat, aspect_deg, slope_deg,
  elevation_m, lithology, pitches, length_m, h3_r5, grid_bucket, dem_res_m, aspect_confidence, coord_precision,
  lithology_source, feature_version, updated_at)
SELECT r.route_id, 'mp', a.path, r.type_group, p.point_key, p.lat, p.aspect_deg, p.slope_deg, p.elevation_m,
       p.lithology,
       CASE WHEN m.pitches >= 1 AND m.pitches < 1000 AND m.pitches = trunc(m.pitches) THEN m.pitches::smallint END,
       CASE WHEN m.length_ft > 0 AND m.length_ft * :ft <= :max_m THEN (m.length_ft * :ft)::real END,
       p.h3_r5, p.grid_bucket, p.dem_res_m, p.aspect_confidence,
       CASE WHEN p.point_key IS NULL THEN 'none' ELSE a.coord_precision END, p.lithology_source, :v, now()
FROM canonical_routes r
JOIN mp_routes m ON m.mp_route_id = r.route_id
LEFT JOIN canonical_areas a ON a.area_id = r.area_id
LEFT JOIN feature_points p ON p.point_key = a.point_key
WHERE NOT r.is_boulder
ON CONFLICT (route_id) DO UPDATE SET source = EXCLUDED.source, area_path = EXCLUDED.area_path,
  type_group = EXCLUDED.type_group, point_key = EXCLUDED.point_key, lat = EXCLUDED.lat, aspect_deg = EXCLUDED.aspect_deg,
  slope_deg = EXCLUDED.slope_deg, elevation_m = EXCLUDED.elevation_m, lithology = EXCLUDED.lithology,
  pitches = EXCLUDED.pitches, length_m = EXCLUDED.length_m, h3_r5 = EXCLUDED.h3_r5, grid_bucket = EXCLUDED.grid_bucket,
  dem_res_m = EXCLUDED.dem_res_m, aspect_confidence = EXCLUDED.aspect_confidence,
  coord_precision = EXCLUDED.coord_precision, lithology_source = EXCLUDED.lithology_source,
  feature_version = EXCLUDED.feature_version, updated_at = now()
""")


async def build_route_features(conn: AsyncConnection, report: ValidationReport | None = None) -> int:
    """Refuses while any scorable route's point is missing from feature_points: building then
    would mark located routes 'none'/no_location. Run the points step first. A length outside
    (0, MAX_LENGTH_M] is stored NULL and quarantined as implausible_length (0 is "not given")."""
    missing, example = (await conn.execute(MISSING_POINTS_SQL)).one()
    if missing:
        raise RuntimeError(f"{missing} scorable route(s) have no feature point (e.g. route {example}); "
                           "run the points step before routes")
    params = {"ft": FT_TO_M, "max_m": MAX_LENGTH_M}
    bad = (await conn.execute(IMPLAUSIBLE_LENGTH_SQL, params)).all()
    await conn.execute(text(
        "DELETE FROM route_static_features f USING canonical_routes r WHERE r.route_id = f.route_id AND r.is_boulder"))
    built = int((await conn.execute(BUILD_SQL, params | {"v": FEATURE_VERSION})).rowcount)
    if report is not None:
        for route_id, length_ft in bad:
            report.quarantine(str(route_id), "implausible_length", length_m=f"{float(length_ft) * FT_TO_M:.0f}")
        for _ in range(built - len(bad)):
            report.accept()
    return built


async def epqs_check(
    conn: AsyncConnection,
    client: EpqsClient,
    *,
    sampler: Sampler = sample_points,
    seed: int = 42,
    per_class: int = PER_CLASS_TARGET,
    probe_batch: int = 500,
    max_probe: int = 6000,
) -> dict[str, object]:
    """Single-connection form of load_epqs_points + epqs_sample (for tests); run_step does not
    hold a transaction while probing."""
    return epqs_sample(await load_epqs_points(conn), client, sampler=sampler, seed=seed, per_class=per_class,
                       probe_batch=probe_batch, max_probe=max_probe)


EpqsPoints = list[tuple[float, float, float]]


async def load_epqs_points(conn: AsyncConnection) -> EpqsPoints:
    return [(float(la), float(lo), float(e)) for la, lo, e in (await conn.execute(EPQS_POINTS_SQL)).all()]


def epqs_sample(
    points: EpqsPoints,
    client: EpqsClient,
    *,
    sampler: Sampler = sample_points,
    seed: int = 42,
    per_class: int = PER_CLASS_TARGET,
    probe_batch: int = 500,
    max_probe: int = 6000,
) -> dict[str, object]:
    """Stratified cross-check. The sample is deterministic: sampled points are read in
    point_key order and shuffled with random.Random(seed), then probed in that order until
    each slope class holds per_class points, so the same table and seed pick the same points.
    passed requires compare()'s gate and, per class, an EPQS failure share of at most
    epqs.MAX_FAILURE_SHARE (failure_guard)."""
    points = list(points)
    random.Random(seed).shuffle(points)
    chosen: dict[str, list[tuple[float, float, float]]] = {name: [] for name, _, _ in SLOPE_CLASSES}
    probed = 0
    for i in range(0, min(len(points), max_probe), probe_batch):
        batch = points[i : min(i + probe_batch, max_probe)]
        probed += len(batch)
        for (la, lo, e), slope in zip(batch, local_slopes([(la, lo) for la, lo, _ in batch], sampler)):
            if slope is None:
                continue
            cls = slope_class(slope)
            if len(chosen[cls]) < per_class:
                chosen[cls].append((la, lo, e))
        if all(len(v) >= per_class for v in chosen.values()):
            break
    rows = [(cls, e, client.elevation(la, lo)) for cls, pts in chosen.items() for la, lo, e in pts]
    result = compare(rows)
    counts, bias_problems = failure_guard(rows)
    passed = int(result["passed"] == 1 and not bias_problems)
    return result | {"passed": passed, "epqs_counts": counts, "bias_problems": bias_problems, "probed": probed,
                     "failed_calls": client.failed_calls}


def _log_problems(step: str, result: dict[str, object]) -> list[str]:
    problems = [json.dumps(result, sort_keys=True, default=str)]
    if step == "epqs-check":
        counts = result.get("epqs_counts")
        if isinstance(counts, dict):
            problems += [f"epqs class {cls}: attempted {c['attempted']}, failed {c['failed']}, compared {c['compared']}"
                         for cls, c in counts.items()]
        bias = result.get("bias_problems")
        if isinstance(bias, list):
            problems += [str(p) for p in bias]
    return problems


async def run_step(
    engine: AsyncEngine, step: str, *, limit: int = 5000, epqs_client: EpqsClient | None = None,
    sampler: Sampler = sample_points,
) -> dict[str, object]:
    """One logged step; the CLI and Task 12's catalog_cycle both call it. Network steps read in
    one short transaction, do their I/O with none open, then write and log in a second."""
    from app.pipelines.ingest_log import RunStatus, finish_run, start_run, write_quarantine

    if step not in ("points", "elevation", "routes", "epqs-check"):
        raise ValueError(f"unknown step {step!r}")
    report = ValidationReport(f"static_features:{step}")
    todo: Pending = []
    values: list[Elevation | None] = []
    epqs_result: dict[str, object] = {}
    if step == "elevation":
        async with engine.connect() as conn:
            todo = await pending_points(conn, limit)
        values = sample_pending(todo, sampler)
    elif step == "epqs-check":
        async with engine.connect() as conn:
            points = await load_epqs_points(conn)
        epqs_result = epqs_sample(points, epqs_client or EpqsClient(), sampler=sampler)
    async with engine.begin() as conn:
        run_id = await start_run(conn, source=f"static_features:{step}", window_start=None, window_end=None,
                                 content_sha256=None)
        result: dict[str, object]
        upserted = 0
        if step == "points":
            upserted = await upsert_points(conn, run_id=run_id, report=report)
            result = {"points": upserted}
        elif step == "elevation":
            stats = await write_elevation(conn, run_id=run_id, todo=todo, values=values)
            upserted = stats["sampled"]
            result = dict(stats)
        elif step == "routes":
            upserted = await build_route_features(conn, report)
            result = {"routes": upserted}
        else:
            result = epqs_result
        if report.quarantined_total():
            await write_quarantine(conn, run_id, report)
        # A failed EPQS gate is logged 'rejected' so nothing downstream reads the run as a pass.
        status: RunStatus = "rejected" if step == "epqs-check" and result.get("passed") != 1 else "ok"
        await finish_run(conn, run_id, status=status, report=report, rows_upserted=upserted,
                         problems=_log_problems(step, result))
    return result


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        return await run_step(engine, args.step, limit=args.limit)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.pipelines.static_features")
    parser.add_argument("step", choices=["points", "elevation", "routes", "epqs-check"])
    parser.add_argument("--limit", type=int, default=5000)
    parsed = parser.parse_args()
    out = asyncio.run(_main(parsed))
    print(json.dumps(out, sort_keys=True, default=str))
    if parsed.step == "epqs-check" and out.get("passed") != 1:
        sys.exit(1)
