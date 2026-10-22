import asyncio
import re
from datetime import date
from pathlib import Path

from alembic import command
from sqlalchemy.ext.asyncio import AsyncSession

from tests.pgtest import requires_pg
from tests.test_migrations import _alembic_cfg, _db_url, fresh_db  # noqa: F401  (fixture)

SRC = Path(__file__).parents[1] / "app" / "tasks" / "safety_computation_optimized.py"


def test_purge_keeps_the_phase3_backtest_window():
    match = re.search(r"DELETE FROM historical_predictions(.*?)\"\"\"", SRC.read_text(), re.S)
    assert match is not None
    assert "NOT (prediction_date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02')" in match.group(1)


@requires_pg
def test_purge_deletes_only_stale_rows_outside_the_window(fresh_db):  # noqa: F811
    command.upgrade(_alembic_cfg(fresh_db), "0003_hist_insufficient_data")
    asyncio.run(_run_purge(fresh_db))


async def _run_purge(dbname: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.tasks.safety_computation_optimized import purge_old_history

    # Dates are fixed and `today` is pinned, so the outcome never depends on the wall clock.
    kept = [date(2026, 2, 5), date(2026, 7, 2), date(2027, 6, 1)]
    purged = [date(2026, 1, 1), date(2026, 2, 4), date(2026, 7, 3)]
    engine = create_async_engine(_db_url(dbname).replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as conn:
            for i, d in enumerate(kept + purged):
                await conn.execute(
                    text("INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) "
                         "VALUES (:r, :d, 10, 'green')"),
                    {"r": i, "d": d},
                )
        async with AsyncSession(engine) as db:
            await purge_old_history(db, date(2028, 1, 1))
        async with engine.connect() as conn:
            left = (await conn.execute(text("SELECT prediction_date FROM historical_predictions"))).scalars().all()
    finally:
        await engine.dispose()
    assert sorted(left) == sorted(kept)
