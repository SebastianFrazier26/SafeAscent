"""Throwaway migrated databases for pipeline tests. Needs MIGRATIONS_TEST_ADMIN_URL
(see test_migrations.py); tests using it skip without one."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from tests.test_migrations import ADMIN_URL, _alembic_cfg, _db_url, _execute

requires_pg = pytest.mark.skipif(not ADMIN_URL, reason="MIGRATIONS_TEST_ADMIN_URL not set")


def pg_url(name: str) -> str:
    return _db_url(name)


def sa_url(name: str) -> str:
    return _db_url(name).replace("postgresql://", "postgresql+asyncpg://", 1)


def run_sql(name: str, sql: str) -> None:
    asyncio.run(_execute(_db_url(name), sql))


@contextmanager
def migrated_db(revision: str = "head", seed_sql: str | None = None) -> Iterator[str]:
    assert ADMIN_URL is not None
    name = f"p2_{uuid.uuid4().hex[:12]}"
    asyncio.run(_execute(ADMIN_URL, f'CREATE DATABASE "{name}"'))
    try:
        command.upgrade(_alembic_cfg(name), revision)
        if seed_sql:
            run_sql(name, seed_sql)
        yield name
    finally:
        asyncio.run(_execute(ADMIN_URL, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def fetch(name: str, sql: str) -> list[tuple[Any, ...]]:
    async def go() -> list[tuple[Any, ...]]:
        conn = await asyncpg.connect(_db_url(name))
        try:
            return [tuple(r) for r in await conn.fetch(sql)]
        finally:
            await conn.close()

    return asyncio.run(go())


def run_on_engine(name: str, fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        engine = create_async_engine(sa_url(name))
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def run_on_conn(name: str, fn: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    """Runs fn in one committed transaction; a SQLAlchemy result comes back as a list of tuples."""

    async def go(engine: AsyncEngine) -> Any:
        async with engine.begin() as conn:
            result = await fn(conn)
            return [tuple(r) for r in result.all()] if hasattr(result, "all") else result

    return run_on_engine(name, go)
