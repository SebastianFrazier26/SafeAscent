import ast
import asyncio
import os
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.pipelines.mp_tick_aggregates as loader
from app.pipelines.mp_tick_aggregates import AggregateRow, is_ice_mixed, load, main, read_export, validate
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
R1, R2, R3, R4 = 900000001, 900000002, 900000003, 900000004
SCRAPED = "2026-09-20T00:00:00+00:00"
ICE = {R1: "Ice", R2: "Ice, Mixed", R4: "Trad"}


def _export(
    tmp_path: Path,
    monthly: list[tuple[int, str, str, int]],
    totals: list[tuple[int, int, int]],
    scraped_at: str = SCRAPED,
    name: str = "ticks.sqlite",
) -> Path:
    path = tmp_path / name
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE route_tick_totals (mp_route_id INTEGER PRIMARY KEY, total_ticks INTEGER NOT NULL, "
        "last_page INTEGER NOT NULL, pages_fetched INTEGER NOT NULL, complete INTEGER NOT NULL, scraped_at TEXT NOT NULL);"
        "CREATE TABLE route_tick_monthly (mp_route_id INTEGER NOT NULL, year_month TEXT NOT NULL, style TEXT NOT NULL, "
        "n INTEGER NOT NULL, PRIMARY KEY (mp_route_id, year_month, style));"
    )
    db.executemany("INSERT INTO route_tick_monthly VALUES (?, ?, ?, ?)", monthly)
    db.executemany(
        "INSERT INTO route_tick_totals VALUES (?, ?, 1, 1, ?, ?)", [(r, t, c, scraped_at) for r, t, c in totals]
    )
    db.commit()
    db.close()
    return path


def _keys(rows):
    return {(r.mp_route_id, r.period, r.style, r.tick_count) for r in rows}


def test_loader_imports_no_network_client():
    tree = ast.parse(Path(loader.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"httpx", "requests", "urllib", "aiohttp", "http", "socket"}


def test_ice_mixed_detection():
    assert is_ice_mixed("Ice") and is_ice_mixed("Trad, Mixed, Alpine") and is_ice_mixed("ice")
    assert not is_ice_mixed("Trad") and not is_ice_mixed(None) and not is_ice_mixed("Sport, Alpine")


def test_scrape_month_and_later_are_partial_not_dropped(tmp_path):
    path = _export(tmp_path, [(R1, "2026-09", "lead", 2), (R1, "2026-01", "lead", 3)], [(R1, 5, 1)])
    totals, monthly = read_export(path)
    for today in (TODAY, date(2026, 10, 5), date(2027, 3, 1)):
        rows, report, _ = validate(totals, monthly, route_types=ICE, existing={}, today=today)
        assert report.quarantined == {"partial_month": 1}
        assert _keys(rows) == {(R1, "2026-01", "lead", 3), (R1, "total", "all", 5)}


def test_future_months_bad_values_scope_and_incomplete_routes_are_quarantined(tmp_path):
    path = _export(
        tmp_path,
        [
            (R1, "2026-10", "lead", 1),
            (R1, "3901-01", "tr", 1),
            (R1, "1965-02", "lead", 1),
            (R1, "2025-13", "lead", 1),
            (R1, "2025-02", "lead", 0),
            (R1, "2025-02", "bogus", 1),
            (R2, "2025-02", "lead", 4),
            (R3, "2025-02", "lead", 4),
            (R4, "2025-02", "lead", 1),
        ],
        [(R1, 9, 1), (R2, 4, 0), (R3, 4, 1), (R4, 1, 1)],
    )
    totals, monthly = read_export(path)
    rows, report, stats = validate(totals, monthly, route_types=ICE, existing={}, today=TODAY)
    assert report.quarantined == {
        "future_month": 2,
        "pre_1970": 1,
        "bad_year_month": 1,
        "nonpositive_count": 1,
        "bad_style": 1,
        "route_incomplete": 2,
        "unknown_route": 2,
        "not_ice_mixed": 2,
    }
    assert _keys(rows) == {(R1, "total", "all", 9)}
    assert stats == {
        "routes": 4,
        "mp_reported_total": 18,
        "accepted_month_ticks": 0,
        "already_loaded": 0,
        "total_mismatch_routes": 1,
    }


def test_a_scrape_stamped_after_today_is_quarantined(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3)], [(R1, 3, 1)], scraped_at="2026-10-02T00:00:00+00:00")
    totals, monthly = read_export(path)
    rows, report, _ = validate(totals, monthly, route_types=ICE, existing={}, today=TODAY)
    assert rows == []
    assert report.quarantined == {"scrape_after_today": 2}


def test_stored_rows_are_never_overwritten(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 4), (R1, "2025-03", "lead", 1)], [(R1, 7, 1)])
    totals, monthly = read_export(path)
    existing = {(R1, "2025-01", "lead"): 3, (R1, "total", "all"): 7}
    rows, report, stats = validate(totals, monthly, route_types=ICE, existing=existing, today=TODAY)
    assert _keys(rows) == {(R1, "2025-03", "lead", 1)}
    assert report.quarantined == {"count_changed": 1}
    assert stats["already_loaded"] == 1


def test_read_export_works_against_a_wal_mode_export_with_a_live_writer(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3)], [(R1, 3, 1)])
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO route_tick_totals VALUES (?, ?, 1, 1, 1, ?)", (R2, 9, SCRAPED))
    try:
        totals, monthly = read_export(path)  # must not raise, and must not see the uncommitted row
        assert {t.mp_route_id for t in totals} == {R1}
        assert {m.mp_route_id for m in monthly} == {R1}
    finally:
        writer.rollback()
        writer.close()


def test_read_export_gives_a_clear_error_not_a_traceback_when_the_directory_is_read_only(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3)], [(R1, 3, 1)])
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()  # last connection closing checkpoints and removes the -wal/-shm files
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]

    os.chmod(tmp_path, 0o500)
    try:
        with pytest.raises(SystemExit) as excinfo:
            read_export(path)
    finally:
        os.chmod(tmp_path, 0o700)
    message = str(excinfo.value)
    assert ".backup" in message and "snapshot" in message
    assert "Traceback" not in message


def _seed_ice_route() -> str:
    return (
        "INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');"
        f"INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES ({R1}, 'Fixture Ice', 900000100, 'Ice');"
    )


async def _stored(url: str) -> list[tuple[str, str, int]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT period, style, tick_count FROM internal.mp_tick_aggregates ORDER BY period, style"))
            return [tuple(r) for r in result.all()]
    finally:
        await engine.dispose()


@requires_pg
def test_second_identical_load_is_a_noop(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R1, "2025-02", "follow", 2)], [(R1, 5, 1)])
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        first = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        second = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        assert first["status"] == "ok" and first["rows_upserted"] == 3
        assert second["status"] == "noop"
        assert asyncio.run(_stored(url)) == [("2025-01", "lead", 3), ("2025-02", "follow", 2), ("total", "all", 5)]


@requires_pg
def test_reload_in_a_later_month_is_still_a_noop(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R1, "2026-09", "lead", 1)], [(R1, 4, 1)])
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        first = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.5, dry_run=False))
        later = asyncio.run(main(path, today=date(2026, 11, 2), max_quarantine_share=0.5, dry_run=False))
        assert first["status"] == "ok"
        assert later["status"] == "noop"
        assert ("2026-09", "lead", 1) not in asyncio.run(_stored(url))


@requires_pg
def test_changed_counts_are_quarantined_never_overwritten(tmp_path, monkeypatch):
    first_path = _export(tmp_path, [(R1, "2025-01", "lead", 3)], [(R1, 3, 1)], name="a.sqlite")
    second_path = _export(
        tmp_path, [(R1, "2025-01", "lead", 4), (R1, "2025-03", "lead", 1)], [(R1, 5, 1)],
        scraped_at="2026-09-25T00:00:00+00:00", name="b.sqlite",
    )
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        asyncio.run(main(first_path, today=TODAY, max_quarantine_share=0.9, dry_run=False))
        second = asyncio.run(main(second_path, today=TODAY, max_quarantine_share=0.9, dry_run=False))
        assert second["status"] == "ok" and second["rows_upserted"] == 1
        assert second["report"]["quarantined"] == {"count_changed": 2}
        assert asyncio.run(_stored(url)) == [("2025-01", "lead", 3), ("2025-03", "lead", 1), ("total", "all", 3)]


@requires_pg
def test_load_does_not_count_a_row_already_present(tmp_path, monkeypatch):
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        engine = create_async_engine(url)
        scraped_at = datetime.fromisoformat(SCRAPED)

        async def _run() -> tuple[int, int]:
            async with engine.begin() as conn:
                first = await load(
                    conn,
                    [AggregateRow(R1, "2025-01", "lead", 3, scraped_at)],
                    run_id=uuid.uuid4(),
                    scrape_run_id="run-1",
                )
            async with engine.begin() as conn:
                # Same key as above (a repeat load) plus one genuinely new key.
                second = await load(
                    conn,
                    [
                        AggregateRow(R1, "2025-01", "lead", 3, scraped_at),
                        AggregateRow(R1, "2025-02", "lead", 1, scraped_at),
                    ],
                    run_id=uuid.uuid4(),
                    scrape_run_id="run-2",
                )
            return first, second

        try:
            first, second = asyncio.run(_run())
        finally:
            asyncio.run(engine.dispose())
        assert first == 1
        assert second == 1  # the repeated key isn't counted, only the new one


@requires_pg
def test_load_chunks_batches_past_the_asyncpg_param_limit(tmp_path, monkeypatch):
    # 6,554+ rows blew the old unchunked statement's param count past asyncpg's 32767 limit
    # (5 params/row + 2 shared); this count also leaves an uneven last chunk under
    # loader.LOAD_CHUNK_ROWS to exercise the remainder.
    n = 7001
    scraped_at = datetime.fromisoformat(SCRAPED)
    rows = [AggregateRow(900100000 + i, "2025-01", "lead", 1, scraped_at) for i in range(n)]
    with migrated_db() as name:
        url = sa_url(name)
        engine = create_async_engine(url)

        async def _run() -> tuple[int, int]:
            async with engine.begin() as conn:
                first = await load(conn, rows, run_id=uuid.uuid4(), scrape_run_id="run-1")
            async with engine.begin() as conn:
                second = await load(conn, rows, run_id=uuid.uuid4(), scrape_run_id="run-2")
            return first, second

        try:
            first, second = asyncio.run(_run())
        finally:
            asyncio.run(engine.dispose())
        assert first == n
        assert second == 0  # a rerun of the same rows inserts nothing new


@requires_pg
def test_heavy_quarantine_rejects_the_batch_and_writes_nothing(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2026-10", "lead", 1), (R1, "2025-01", "lead", 1)], [(R1, 2, 1)])
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        result = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        assert result["status"] == "rejected"
        assert result["rows_upserted"] == 0
        assert asyncio.run(_stored(url)) == []
