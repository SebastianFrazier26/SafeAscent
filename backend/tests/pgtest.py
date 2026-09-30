"""Throwaway migrated databases for pipeline tests. Needs MIGRATIONS_TEST_ADMIN_URL
(see test_migrations.py); tests using it skip without one."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command

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
