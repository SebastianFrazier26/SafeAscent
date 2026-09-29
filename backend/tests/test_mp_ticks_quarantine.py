import asyncio
from collections import Counter
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.mp_ticks_quarantine import classify_tick, run
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)


def test_tick_after_capture_day_is_future_even_when_before_today():
    assert classify_tick(date(2026, 3, 1), date(2026, 2, 8), True, TODAY) == "future"
    assert classify_tick(date(2026, 2, 10), date(2026, 2, 8), True, TODAY) == "future"


def test_tick_one_day_after_capture_is_allowed_for_writer_zone_slack():
    # created_at holds the writer session's local time, which may be up to 10 h behind UTC.
    assert classify_tick(date(2026, 2, 9), date(2026, 2, 8), True, TODAY) is None


def test_tick_after_today_is_future_when_capture_day_unknown():
    assert classify_tick(date(2026, 9, 29), None, True, TODAY) == "future"
    assert classify_tick(date(2026, 9, 28), None, True, TODAY) is None


def test_precedence_future_then_orphan_then_pre_1970():
    assert classify_tick(date(3901, 1, 1), date(2026, 2, 8), False, TODAY) == "future"
    assert classify_tick(date(1965, 1, 1), date(2026, 2, 8), False, TODAY) == "orphan_route"
    assert classify_tick(date(1965, 1, 1), date(2026, 2, 8), True, TODAY) == "pre_1970"
    assert classify_tick(None, date(2026, 2, 8), True, TODAY) is None


SEED = """
INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');
INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES (900000001, 'Fixture Route', 900000100);
INSERT INTO mp_ticks (tick_id, route_id, climber_name, tick_date, created_at) VALUES
  (1, '900000001', 'c', '2025-01-04', '2026-02-08 10:00'),
  (2, '900000001', 'c', '2026-03-01', '2026-02-08 10:00'),
  (3, '900000001', 'c', '3901-01-15', '2026-02-08 10:00'),
  (4, '900000999', 'c', '2025-01-04', '2026-02-08 10:00'),
  (5, 'abc',       'c', '2025-01-04', '2026-02-08 10:00'),
  (6, '900000001', 'c', '1965-06-01', '2026-02-08 10:00'),
  (7, '900000001', 'c', NULL,         '2026-02-08 10:00'),
  (8, '900000001', 'c', '2026-02-09', '2026-02-08 23:00');
"""

# F5 (progress.md pre-flight ruling): expected reasons come from classify_tick itself, one
# call per seed row, rather than a hand-written literal — otherwise a change to classify_tick
# that QUARANTINE_SQL forgets to follow would still pass this test.
# (tick_date, captured_on == created_at's date, route_known)
SEED_ROW_INPUTS: dict[int, tuple[date | None, date, bool]] = {
    1: (date(2025, 1, 4), date(2026, 2, 8), True),
    2: (date(2026, 3, 1), date(2026, 2, 8), True),
    3: (date(3901, 1, 15), date(2026, 2, 8), True),
    4: (date(2025, 1, 4), date(2026, 2, 8), False),  # route_id 900000999 does not exist
    5: (date(2025, 1, 4), date(2026, 2, 8), False),  # route_id 'abc' is not a valid mp_route_id
    6: (date(1965, 6, 1), date(2026, 2, 8), True),
    7: (None, date(2026, 2, 8), True),
    8: (date(2026, 2, 9), date(2026, 2, 8), True),
}
EXPECTED_REASONS = {
    tick_id: classify_tick(tick_date, captured_on, route_known, TODAY)
    for tick_id, (tick_date, captured_on, route_known) in SEED_ROW_INPUTS.items()
}
EXPECTED_COUNTS = dict(Counter(reason or "clean" for reason in EXPECTED_REASONS.values()))


@requires_pg
def test_sql_matches_the_python_rule_and_is_idempotent():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                counts = await run(conn, today=TODAY)
            assert counts == EXPECTED_COUNTS
            async with engine.begin() as conn:
                again = await run(conn, today=TODAY)
                changed = (await conn.execute(text(
                    "SELECT rows_upserted FROM source_ingest_log WHERE source = 'mp_ticks_quarantine' "
                    "ORDER BY finished_at DESC LIMIT 1"))).scalar_one()
            assert again == counts
            assert changed == 0
            async with engine.connect() as conn:
                rows = dict((await conn.execute(text("SELECT tick_id, quarantine_reason FROM mp_ticks"))).all())
            assert rows == EXPECTED_REASONS
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))
