"""Every dataset reader runs as trainer with only grants_phase3.sql's allowlist (DP17)."""

import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import create_async_engine

from app.ml.dataset import (
    pick_snapshot,
    read_area_paths,
    read_areas,
    read_exposure,
    read_incidents,
    read_static,
)
from tests.ml.test_dataset_sql import SEED
from tests.pgtest import requires_pg, run_sql
from tests.test_migrations import _require_psql, fresh_db, role_cleanup  # noqa: F401  (fixtures)
from tests.test_roles_phase3 import _phase3

pytestmark = requires_pg


async def _read_all(url: str) -> dict[str, int]:
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.connect() as conn:
            assert (await conn.exec_driver_sql("SELECT current_user")).scalar_one() == "trainer"
            as_of = await pick_snapshot(conn, data_cutoff=date(2026, 10, 1), production=True,
                                        today=date(2026, 10, 20))
            return {
                "incidents": len(await read_incidents(conn)),
                "areas": len(await read_areas(conn)),
                "paths": len(await read_area_paths(conn)),
                "exposure": len(await read_exposure(conn, as_of=as_of, data_cutoff=as_of)),
                "static": len(await read_static(conn)),
            }
    finally:
        await engine.dispose()


def test_every_reader_runs_as_trainer(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    _, trainer = _phase3(fresh_db)
    run_sql(fresh_db, SEED)
    assert asyncio.run(_read_all(trainer)) == {"incidents": 3, "areas": 2, "paths": 2, "exposure": 2, "static": 24}
