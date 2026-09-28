"""Migration tests against a throwaway database on a real PostGIS server.

Set MIGRATIONS_TEST_ADMIN_URL to a libpq URL for a superuser connection to a
maintenance database, e.g. postgresql://test_user:test_password@localhost:5432/postgres.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

ADMIN_URL = os.environ.get("MIGRATIONS_TEST_ADMIN_URL")
BACKEND = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="MIGRATIONS_TEST_ADMIN_URL not set")


def _db_url(dbname: str) -> str:
    assert ADMIN_URL is not None
    return ADMIN_URL.rsplit("/", 1)[0] + f"/{dbname}"


async def _execute(url: str, sql: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _fetchrow(url: str, sql: str) -> list[object]:
    conn = await asyncpg.connect(url)
    try:
        record = await conn.fetchrow(sql)
        return list(record) if record is not None else []
    finally:
        await conn.close()


def _run(dbname: str, sql: str) -> None:
    asyncio.run(_execute(_db_url(dbname), sql))


def _fetch_row(dbname: str, sql: str) -> list[object]:
    return asyncio.run(_fetchrow(_db_url(dbname), sql))


def _alembic_cfg(dbname: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _db_url(dbname).replace("postgresql://", "postgresql+asyncpg://", 1))
    return cfg


@pytest.fixture
def fresh_db() -> Iterator[str]:
    assert ADMIN_URL is not None
    name = f"mig_{uuid.uuid4().hex[:12]}"
    asyncio.run(_execute(ADMIN_URL, f'CREATE DATABASE "{name}"'))
    try:
        yield name
    finally:
        asyncio.run(_execute(ADMIN_URL, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_baseline_builds_live_schema_and_models_match(fresh_db):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "0001_baseline")
    row = _fetch_row(
        fresh_db,
        "SELECT to_regclass('public.accidents')::text, to_regclass('public.weather')::text, "
        "to_regclass('public.routes')::text, to_regclass('public.mountains')::text",
    )
    assert row == ["accidents", "weather", "routes", "mountains"]
    command.upgrade(cfg, "head")
    command.check(cfg)
