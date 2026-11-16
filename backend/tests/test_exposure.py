import argparse
import asyncio
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines import db as db_module
from app.pipelines.exposure import _run, build, drop_snapshot, validate
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, sa_url
from tests.test_exposure_components import CRAG, ICE_KEY, SEED, TICKS, TODAY, _climate

CLIMATE = _climate()[0]
# A second ice area on the ice area's own point: same season shares, so exposure points tie across
# the two areas, while its own tick counts keep the tick column from collapsing into the first's.
TWIN_ICE = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000140, 'Fixture Twin Ice', 900000100, 40.3, -105.4);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000008, 'Fixture Twin', 900000140, 'Ice');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000140, 900000100, '900000100.900000140', 2, 'us', 'CO', 40.3, -105.4, 'own', 900000140, '{ICE_KEY}', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000008, 900000140, 'ice', false, '{{ice}}', 'rt-mp-v3', true, NULL, 'h', 'v');
INSERT INTO internal.route_tick_scrapes (mp_route_id, source, scraped_at, complete, complete_through, total_ticks)
  VALUES (900000008, 'mp_scrape', '2026-02-10T00:00:00+00', true, DATE '2026-01-31', 10);
INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES
  (900000008, '2025-01', 'lead', 7), (900000008, '2024-12', 'lead', 3);
"""


@requires_pg
def test_snapshot_rows_components_coverage_and_bounds():
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=True))
        [(proxy, cov, point, lo, hi, cutoff, version)] = fetch(
            name, "SELECT proxy, tick_coverage, exposure_point, exposure_lo, exposure_hi, data_cutoff, proxy_version "
                  f"FROM internal.exposure_index WHERE area_id = {CRAG} AND type_group = 'trad' AND month = 7")
    import json

    proxy = json.loads(proxy) if isinstance(proxy, str) else proxy
    assert set(proxy) == {"route_density", "season_share", "mp_ticks"} and version == "x-mp-v1"
    assert cutoff == TODAY and cov == pytest.approx(1 / 3)
    assert point == pytest.approx(3 * proxy["season_share"]["value"], rel=1e-6)
    assert (lo, hi) == pytest.approx((point / 3, point * 3), rel=1e-6)


@requires_pg
def test_missing_season_gives_null_point_and_bounds():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=True))
        rows = fetch(name, "SELECT count(*) FILTER (WHERE exposure_point IS NOT NULL), count(*) "
                           "FROM internal.exposure_index")
    assert rows == [(0, 36)]


@requires_pg
def test_removal_drill_changes_only_mp_ticks():
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        on = run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=True, write=False))
        off = run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=False, write=False))
        assert fetch(name, "SELECT count(*) FROM internal.exposure_index") == [(0,)]
        run_on_conn(name, lambda conn: build(conn, today=TODAY, cutoff=date(2026, 9, 1), mp_enabled=False))
        assert fetch(name, "SELECT count(*) FROM internal.exposure_index WHERE tick_coverage IS NOT NULL "
                           "OR proxy->'mp_ticks'->>'reason' <> 'disabled'") == [(0,)]
    assert on["digests"]["route_density"] == off["digests"]["route_density"]
    assert on["digests"]["season_share"] == off["digests"]["season_share"]
    assert on["digests"]["mp_ticks"] != off["digests"]["mp_ticks"]


@requires_pg
def test_backtest_snapshots_survive_production_pruning_and_can_be_dropped():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        async def go(conn):
            await build(conn, today=date(2026, 5, 1), cutoff=date(2025, 1, 1), mp_enabled=True)
            for day in (date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)):
                await build(conn, today=day, mp_enabled=True)
            before = (await conn.execute(text(
                "SELECT DISTINCT as_of, data_cutoff FROM internal.exposure_index ORDER BY 1"))).all()
            dropped = await drop_snapshot(conn, as_of=date(2026, 5, 1), data_cutoff=date(2025, 1, 1))
            return [tuple(r) for r in before], dropped

        before, dropped = run_on_conn(name, go)
    assert before == [(date(2026, 5, 1), date(2025, 1, 1)), (date(2026, 7, 1), date(2026, 7, 1)),
                      (date(2026, 8, 1), date(2026, 8, 1)), (date(2026, 9, 1), date(2026, 9, 1))]
    assert dropped == 36


@requires_pg
def test_validate_reports_counts_and_omits_undefined_spearman():
    # F10: the old assertion's "or" reduced to its first disjunct (full_coverage_cells == 12 was
    # already asserted), so the "omits undefined" case the test's name promises was never
    # exercised, and tick_coverage_mean was never checked. CRAG has 3 routes (1 complete, 1
    # scraped, 1 never scraped) across 12 months = 1/3 coverage; the ice area's 1 route is
    # complete = 1.0; the bare area's 1 route is never scraped = 0.0.
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        async def go(conn):
            await build(conn, today=TODAY, mp_enabled=True)
            return await validate(conn, as_of=TODAY, data_cutoff=TODAY)

        got = run_on_conn(name, go)
    assert got["cells"] == 36 and got["full_coverage_cells"] == 12
    assert got["tick_coverage_mean"] == pytest.approx((12 * 1 / 3 + 12 * 1 + 12 * 0) / 36)
    assert "spearman_point_ticks" in got


@requires_pg
def test_validate_omits_tick_metrics_when_mp_ticks_is_disabled():
    # F10: the undefined case — with mp_enabled=False every cell's mp_ticks is "disabled", so
    # there is no coverage anywhere and both tick-derived keys are absent.
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        async def go(conn):
            await build(conn, today=TODAY, mp_enabled=False)
            return await validate(conn, as_of=TODAY, data_cutoff=TODAY)

        got = run_on_conn(name, go)
    assert got["full_coverage_cells"] == 0
    assert "tick_coverage_mean" not in got and "spearman_point_ticks" not in got


def test_cutoff_after_today_is_refused():
    with pytest.raises(ValueError, match="after today"):
        asyncio.run(build(None, today=TODAY, cutoff=date(2026, 10, 16), mp_enabled=True))  # type: ignore[arg-type]


@requires_pg
def test_cli_build_logs_metrics(monkeypatch):
    # F11: the logged CLI path (start_run -> build -> validate -> finish_run(metrics=checks)), where
    # the _ALLOWED_METRICS change and report.rows_in = report.accepted = int(checks["cells"]) live.
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        monkeypatch.setattr(db_module, "ingest_engine", lambda: create_async_engine(sa_url(name)))
        args = argparse.Namespace(cmd="build", cutoff=None, dry_run=False)
        asyncio.run(_run(args, today=TODAY, mp_enabled=True))
        [(status, rows_upserted, report)] = fetch(
            name, "SELECT status, rows_upserted, validation_report FROM source_ingest_log WHERE source = 'exposure'")
    import json

    report = json.loads(report) if isinstance(report, str) else report
    assert status == "ok" and rows_upserted == 36
    assert {"cells", "full_coverage_cells"} <= set(report)


@requires_pg
def test_validate_spearman_matches_exposure_math(monkeypatch):
    # validate ranks in SQL; exposure_math.spearman is the reference (ties averaged, 0-based).
    from app.pipelines.exposure_math import spearman

    with migrated_db(seed_sql=SEED + TICKS + CLIMATE + TWIN_ICE) as name:
        async def go(conn):
            await build(conn, today=TODAY, mp_enabled=True)
            return await validate(conn, as_of=TODAY, data_cutoff=TODAY)

        got = run_on_conn(name, go)
        pairs = fetch(name, "SELECT exposure_point, (proxy->'mp_ticks'->>'value')::float8 FROM internal.exposure_index "
                            "WHERE tick_coverage = 1 AND exposure_point IS NOT NULL")
    ref = spearman([float(p) for p, _ in pairs], [float(t) for _, t in pairs])
    assert ref is not None
    assert len({p for p, _ in pairs}) < len(pairs) and len({t for _, t in pairs}) < len(pairs)
    assert got["spearman_point_ticks"] == pytest.approx(ref, abs=1e-9)


@requires_pg
def test_scope_and_components_read_one_snapshot(monkeypatch):
    # A catalog write committed between the scope read and the tick read: under per-statement
    # snapshots the tick statuses would count a 4th crag route against scope's 3 and fail.
    import asyncpg

    import app.pipelines.exposure as exposure_mod
    from tests.pgtest import pg_url

    real = exposure_mod.mp_ticks
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        async def concurrent_then_ticks(conn, scope_, **kw):
            other = await asyncpg.connect(pg_url(name))
            try:
                await other.execute(
                    "INSERT INTO mp_routes (mp_route_id, name, location_id, type) "
                    f"VALUES (900000007, 'Fixture Late', {CRAG}, 'Trad');"
                    "INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, "
                    "type_rule_version, scorable, unscorable_reason, facts_sha256, catalog_version) "
                    f"VALUES (900000007, {CRAG}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'h', 'v')")
            finally:
                await other.close()
            return await real(conn, scope_, **kw)

        monkeypatch.setattr(exposure_mod, "mp_ticks", concurrent_then_ticks)
        run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=True))
        rows = fetch(name, f"SELECT DISTINCT n_routes, tick_coverage FROM internal.exposure_index "
                           f"WHERE area_id = {CRAG} AND type_group = 'trad'")
        late = fetch(name, "SELECT count(*) FROM canonical_routes WHERE route_id = 900000007")
    assert late == [(1,)]
    assert len(rows) == 1 and rows[0][0] == 3 and rows[0][1] == pytest.approx(1 / 3)


@requires_pg
def test_build_refuses_to_run_after_a_query_in_a_read_committed_transaction():
    with migrated_db(seed_sql=SEED) as name:
        async def go(conn):
            await conn.execute(text("SELECT 1"))
            await build(conn, today=TODAY, mp_enabled=True)

        with pytest.raises(Exception, match="SET TRANSACTION ISOLATION LEVEL"):
            run_on_conn(name, go)


@requires_pg
def test_cli_warns_loudly_in_the_january_blackout_and_still_builds(monkeypatch, capsys):
    january = date(2027, 1, 10)
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        # Pruned on 2027-01-01 to Jan 1 of 2024; this January's ERA5 has not landed (last 2026-01-05).
        from tests.pgtest import run_sql

        run_sql(name, "UPDATE grid_bucket_series SET first_era5_date = DATE '2024-01-01'")
        monkeypatch.setattr(db_module, "ingest_engine", lambda: create_async_engine(sa_url(name)))
        out = asyncio.run(_run(argparse.Namespace(cmd="build", cutoff=None, dry_run=False), today=january,
                               mp_enabled=True))
        [(status, report)] = fetch(name, "SELECT status, validation_report FROM source_ingest_log "
                                         "WHERE source = 'exposure'")
        points = fetch(name, "SELECT count(*), count(exposure_point) FROM internal.exposure_index")
    import json

    err = capsys.readouterr().err
    assert "WARNING (January blackout)" in err and "2 ERA5 series" in err
    assert out["status"] == "ok" and out["season_blackout_series"] == 2
    assert points == [(36, 0)]
    report = json.loads(report) if isinstance(report, str) else report
    assert status == "ok" and any(p.startswith("season_blackout: 2") for p in report["problems"])


@requires_pg
def test_cli_is_quiet_outside_the_blackout_and_dry_run_writes_nothing(monkeypatch, capsys):
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        monkeypatch.setattr(db_module, "ingest_engine", lambda: create_async_engine(sa_url(name)))
        out = asyncio.run(_run(argparse.Namespace(cmd="build", cutoff=None, dry_run=True), today=TODAY,
                               mp_enabled=True))
        written = fetch(name, "SELECT (SELECT count(*) FROM internal.exposure_index), "
                              "(SELECT count(*) FROM source_ingest_log WHERE source = 'exposure')")
    assert out["status"] == "dry_run" and out["cells"] == 36 and out["season_blackout_series"] == 0
    assert written == [(0, 0)]
    assert "WARNING" not in capsys.readouterr().err


@requires_pg
def test_build_refuses_an_autocommit_connection():
    # SET TRANSACTION on AUTOCOMMIT only warns; every statement would read its own snapshot.
    with migrated_db(seed_sql=SEED) as name:
        async def go():
            engine = create_async_engine(sa_url(name))
            try:
                async with engine.connect() as conn:
                    auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
                    await build(auto, today=TODAY, mp_enabled=True, write=False)
            finally:
                await engine.dispose()

        with pytest.raises(RuntimeError, match="REPEATABLE READ"):
            asyncio.run(go())


def _drop_cli(monkeypatch, name, as_of, cutoff):
    from app.pipelines import exposure as exposure_mod

    monkeypatch.setattr(db_module, "ingest_engine", lambda: create_async_engine(sa_url(name)))
    monkeypatch.setattr("sys.argv", ["exposure", "drop", "--as-of", as_of, "--cutoff", cutoff])
    exposure_mod.cli()


@requires_pg
def test_cli_drop_logs_dropped_rows(monkeypatch, capsys):
    import json

    with migrated_db(seed_sql=SEED + TICKS) as name:
        run_on_conn(name, lambda conn: build(conn, today=TODAY, cutoff=date(2025, 1, 1), mp_enabled=True))
        _drop_cli(monkeypatch, name, str(TODAY), "2025-01-01")
        left = fetch(name, "SELECT count(*) FROM internal.exposure_index")
        [(status, upserted, report)] = fetch(
            name, "SELECT status, rows_upserted, validation_report FROM source_ingest_log WHERE source = 'exposure_index'")
    report = json.loads(report) if isinstance(report, str) else report
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "dropped_rows": 36}
    assert left == [(0,)] and status == "ok" and upserted == 0
    assert report["dropped_rows"] == 36 and report["source"] == "exposure_index:drop"


@requires_pg
def test_cli_drop_of_no_such_snapshot_exits_non_zero_and_logs_it(monkeypatch, capsys):
    import json

    with migrated_db(seed_sql=SEED) as name:
        with pytest.raises(SystemExit) as exit_:
            _drop_cli(monkeypatch, name, str(TODAY), "2025-01-01")
        [(status, report)] = fetch(
            name, "SELECT status, validation_report FROM source_ingest_log WHERE source = 'exposure_index'")
    report = json.loads(report) if isinstance(report, str) else report
    assert exit_.value.code == 1 and "no such snapshot" in capsys.readouterr().err
    assert status == "rejected" and report["dropped_rows"] == 0
    assert any(p.startswith("no such snapshot") for p in report["problems"])
