"""Migration tests against a throwaway database on a real PostGIS server.

Set MIGRATIONS_TEST_ADMIN_URL to a libpq URL for a superuser connection to a
maintenance database, e.g. postgresql://test_user:test_password@localhost:5432/postgres.
"""

import asyncio
import logging
import os
import shutil
import subprocess
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
    cfg.attributes["configure_logger"] = False
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


def test_head_drops_ascents_and_climbers_but_keeps_legacy_tables(fresh_db):
    command.upgrade(_alembic_cfg(fresh_db), "head")
    row = _fetch_row(
        fresh_db,
        "SELECT to_regclass('public.ascents')::text, to_regclass('public.climbers')::text, "
        "to_regclass('public.routes')::text, to_regclass('public.mountains')::text, "
        "(SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.accidents'::regclass "
        " AND contype = 'f' AND confrelid = 'public.routes'::regclass)",
    )
    assert row == [None, None, "routes", "mountains", 1]


def test_0002_refuses_to_drop_non_empty_tables(fresh_db):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "0001_baseline")
    _run(fresh_db, "INSERT INTO climbers (username) VALUES ('fixture-user')")
    with pytest.raises(RuntimeError, match="refusing to drop climbers"):
        command.upgrade(cfg, "head")
    assert _fetch_row(fresh_db, "SELECT to_regclass('public.ascents')::text") == ["ascents"]


def test_models_no_longer_define_dropped_tables():
    from app.db.session import Base

    assert "ascents" not in Base.metadata.tables
    assert "climbers" not in Base.metadata.tables


def test_running_alembic_leaves_app_loggers_enabled(fresh_db, caplog):
    probe = logging.getLogger("app.migration_probe")
    root_level = logging.getLogger().level
    command.upgrade(_alembic_cfg(fresh_db), "0001_baseline")
    assert not probe.disabled
    assert probe.propagate
    assert logging.getLogger().level == root_level
    with caplog.at_level(logging.WARNING):
        probe.warning("still captured")
    assert "still captured" in caplog.text


def test_baseline_refuses_a_database_that_already_has_the_schema(fresh_db):
    _run(fresh_db, "CREATE TABLE accidents (accident_id integer)")
    with pytest.raises(RuntimeError, match="alembic stamp 0001_baseline"):
        command.upgrade(_alembic_cfg(fresh_db), "0001_baseline")
    row = _fetch_row(
        fresh_db,
        "SELECT to_regclass('public.weather')::text, to_regclass('public.routes')::text, "
        "to_regclass('public.alembic_version')::text",
    )
    assert row == [None, None, None]


PSQL = shutil.which("psql")
ROLES_DIR = BACKEND / "db" / "roles"
TEST_ROLES = ("migrator", "app", "analyst")

ANALYST_FIXTURE_SQL = """
CREATE ROLE analyst LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD 'test-analyst-pw';
ALTER ROLE analyst SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;
"""


def _drop_test_roles() -> None:
    assert ADMIN_URL is not None
    asyncio.run(_execute(ADMIN_URL, "DROP ROLE IF EXISTS " + ", ".join(TEST_ROLES)))


@pytest.fixture
def role_cleanup() -> Iterator[None]:
    # Requested before fresh_db, so it tears down after the database (and every object
    # these roles own in it) is gone; roles are cluster-wide.
    _drop_test_roles()
    yield
    _drop_test_roles()


def _psql(dbname: str, script: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert PSQL is not None
    return subprocess.run(
        [PSQL, _db_url(dbname), "-X", "-q", "-f", str(script)],
        env={**os.environ, **env_extra},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(PSQL is None, reason="psql (15+) not installed")
def test_role_scripts_create_least_privilege_roles(role_cleanup, fresh_db):
    command.upgrade(_alembic_cfg(fresh_db), "head")
    _run(fresh_db, ANALYST_FIXTURE_SQL)

    created = _psql(
        fresh_db,
        ROLES_DIR / "create_roles.sql",
        {"MIGRATOR_PASSWORD": "test-migrator-pw", "APP_PASSWORD": "test-app-pw"},
    )
    assert created.returncode == 0, created.stderr
    assert "test-migrator-pw" not in created.stdout + created.stderr
    assert "test-app-pw" not in created.stdout + created.stderr

    verified = _psql(fresh_db, ROLES_DIR / "verify_roles.sql", {})
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "ALL ROLE CHECKS PASSED" in verified.stdout

    base = _db_url(fresh_db).split("://", 1)[1].split("@", 1)[1]
    app_url = f"postgresql://app:test-app-pw@{base}"
    migrator_url = f"postgresql://migrator:test-migrator-pw@{base}"

    asyncio.run(_execute(app_url, "SELECT count(*) FROM accidents"))
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        asyncio.run(_execute(app_url, "CREATE TABLE app_should_not_create (x int)"))
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        asyncio.run(_execute(app_url, "TRUNCATE accidents"))

    asyncio.run(_execute(migrator_url, "CREATE TABLE new_after_roles (id serial PRIMARY KEY)"))
    asyncio.run(_execute(app_url, "INSERT INTO new_after_roles DEFAULT VALUES"))
