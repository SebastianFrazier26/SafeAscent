"""Static features per distinct point and the per-route table Phase 3 reads, on the MP
catalog. Points are the stored canonical_areas.point_key of areas that carry a non-boulder
route; a point's coordinates are parsed back from its key, so every consumer sees the same
5-decimal location. Transient DEM failures leave a point unfilled for the next run.
Missing is never 0: unknown elevation, slope and aspect stay NULL."""

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
PENDING_SQL = text(
    "SELECT point_key, lat, lon FROM feature_points WHERE dem_source IS NULL ORDER BY point_key LIMIT :n"
)
ELEVATION_UPDATE = text(
    "UPDATE feature_points SET elevation_m = :e, dem_source = :s, dem_res_m = :r, updated_at = now(), "
    "run_id = :run WHERE point_key = :k"
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
    rows: list[dict[str, object]] = []
    for key in keys:
        lat, lon = _parse_key(key)
        tz = tz_lookup(lat, lon)
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


async def fill_elevation(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    sampler: Sampler = sample_points,
    limit: int,
) -> dict[str, int]:
    todo = [(str(k), float(la), float(lo)) for k, la, lo in (await conn.execute(PENDING_SQL, {"n": limit})).all()]
    if not todo:
        return {"sampled": 0, "null": 0, "retry": 0}
    values = sampler([(la, lo) for _, la, lo in todo])
    if len(values) != len(todo):
        raise ValueError(f"sampler returned {len(values)} values for {len(todo)} points")
    # A None is a transient DEM error: the point keeps dem_source NULL and is retried next run.
    done = [(k, v) for (k, _, _), v in zip(todo, values) if v is not None]
    if done:
        await conn.execute(ELEVATION_UPDATE, [{"e": v.elevation_m, "s": v.dem_source, "r": v.dem_res_m, "run": run_id,
                                               "k": k} for k, v in done])
    return {"sampled": len(done), "null": sum(1 for _, v in done if v.elevation_m is None),
            "retry": len(todo) - len(done)}


BUILD_SQL = text("""
INSERT INTO route_static_features (route_id, source, area_path, type_group, point_key, lat, aspect_deg, slope_deg,
  elevation_m, lithology, pitches, length_m, h3_r5, grid_bucket, dem_res_m, aspect_confidence, coord_precision,
  lithology_source, feature_version, updated_at)
SELECT r.route_id, 'mp', a.path, r.type_group, p.point_key, p.lat, p.aspect_deg, p.slope_deg, p.elevation_m,
       p.lithology,
       CASE WHEN m.pitches >= 1 AND m.pitches < 1000 AND m.pitches = trunc(m.pitches) THEN m.pitches::smallint END,
       CASE WHEN m.length_ft > 0 THEN (m.length_ft * 0.3048)::real END,
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


async def build_route_features(conn: AsyncConnection) -> int:
    await conn.execute(text(
        "DELETE FROM route_static_features f USING canonical_routes r WHERE r.route_id = f.route_id AND r.is_boulder"))
    return int((await conn.execute(BUILD_SQL, {"v": FEATURE_VERSION})).rowcount)


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
    """Stratified cross-check. The sample is deterministic: sampled points are read in
    point_key order and shuffled with random.Random(seed), then probed in that order until
    each slope class holds per_class points, so the same table and seed pick the same points.
    passed requires compare()'s gate and, per class, an EPQS failure share of at most
    epqs.MAX_FAILURE_SHARE (failure_guard)."""
    points = [(float(la), float(lo), float(e)) for la, lo, e in (await conn.execute(EPQS_POINTS_SQL)).all()]
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
    """One logged step in its own transaction; the CLI and Task 12's catalog_cycle both call it."""
    from app.pipelines.ingest_log import RunStatus, finish_run, start_run, write_quarantine

    if step not in ("points", "elevation", "routes", "epqs-check"):
        raise ValueError(f"unknown step {step!r}")
    report = ValidationReport(f"static_features:{step}")
    async with engine.begin() as conn:
        run_id = await start_run(conn, source=f"static_features:{step}", window_start=None, window_end=None,
                                 content_sha256=None)
        result: dict[str, object]
        upserted = 0
        if step == "points":
            upserted = await upsert_points(conn, run_id=run_id, report=report)
            result = {"points": upserted}
        elif step == "elevation":
            stats = await fill_elevation(conn, run_id=run_id, sampler=sampler, limit=limit)
            upserted = stats["sampled"]
            result = dict(stats)
        elif step == "routes":
            upserted = await build_route_features(conn)
            result = {"routes": upserted}
        else:
            result = await epqs_check(conn, epqs_client or EpqsClient(), sampler=sampler)
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
