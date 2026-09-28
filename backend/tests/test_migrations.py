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
from urllib.parse import urlsplit

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from scripts.write_role_url import scram_verifier

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


async def _fetchall(url: str, sql: str) -> list[str]:
    conn = await asyncpg.connect(url)
    try:
        return [str(record[0]) for record in await conn.fetch(sql)]
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


async def _insert_history(url: str, rows: list[tuple[int, object, str]]) -> None:
    # Bound parameters, like the nightly job's text() insert: None must reach the column as NULL.
    conn = await asyncpg.connect(url)
    try:
        await conn.executemany(
            "INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) "
            "VALUES ($1, '2026-09-28', $2, $3)",
            rows,
        )
    finally:
        await conn.close()


def test_0003_stores_insufficient_days_as_null_gray_and_rejects_mixed_pairs(fresh_db):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")
    url = _db_url(fresh_db)
    asyncio.run(_insert_history(url, [(1, None, "gray"), (2, 30.0, "yellow")]))
    assert _fetch_row(fresh_db, "SELECT risk_score FROM historical_predictions WHERE route_id = 1") == [None]
    for row in [(3, None, "yellow"), (4, 12.0, "gray")]:
        with pytest.raises(asyncpg.CheckViolationError):
            asyncio.run(_insert_history(url, [row]))


def test_0003_downgrade_refuses_while_null_scores_exist(fresh_db):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")
    asyncio.run(_insert_history(_db_url(fresh_db), [(1, None, "gray"), (2, 30.0, "yellow")]))
    with pytest.raises(RuntimeError, match="refusing to downgrade 0003"):
        command.downgrade(cfg, "0002_drop_ascents_climbers")
    # Nothing was deleted and the column is still nullable.
    assert _fetch_row(fresh_db, "SELECT count(*) FROM historical_predictions") == [2]

    _run(fresh_db, "DELETE FROM historical_predictions WHERE risk_score IS NULL")
    command.downgrade(cfg, "0002_drop_ascents_climbers")
    assert _fetch_row(
        fresh_db,
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'historical_predictions' AND column_name = 'risk_score'",
    ) == ["NO"]
    assert _fetch_row(
        fresh_db,
        "SELECT count(*) FROM pg_constraint WHERE conname = 'historical_predictions_score_status_check'",
    ) == [0]


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
# sa_test_owner stands in for Neon's neondb_owner: CREATEROLE but not superuser, so the
# test sees PG16's automatic ADMIN grant to a role's creator, as prod will.
OWNER_ROLE = "sa_test_owner"
TEST_ROLES = ("migrator", "app", "analyst", OWNER_ROLE)
PASSWORDS = {"owner": "test-owner-pw", "migrator": "test-migrator-pw", "app": "test-app-pw"}

ANALYST_FIXTURE_SQL = """
CREATE ROLE analyst LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD 'test-analyst-pw';
ALTER ROLE analyst SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;
"""


def _drop_test_roles() -> None:
    assert ADMIN_URL is not None
    host = urlsplit(ADMIN_URL).hostname
    if host not in ("localhost", "127.0.0.1"):
        raise RuntimeError(f"refusing to DROP ROLE on non-local host {host!r}")
    asyncio.run(_execute(ADMIN_URL, "DROP ROLE IF EXISTS " + ", ".join(TEST_ROLES)))


@pytest.fixture
def role_cleanup() -> Iterator[None]:
    # Requested before fresh_db, so it tears down after the database (and every object
    # these roles own in it) is gone; roles are cluster-wide.
    _drop_test_roles()
    yield
    _drop_test_roles()


def _require_psql() -> str:
    if PSQL is None:
        if os.environ.get("CI"):
            pytest.fail("psql (15+) is required in CI for the role-script test")
        pytest.skip("psql (15+) not installed")
    return PSQL


def _role_url(dbname: str, role: str, password: str) -> str:
    base = _db_url(dbname).split("://", 1)[1].split("@", 1)[1]
    return f"postgresql://{role}:{password}@{base}"


def _psql(url: str, script: Path, env_extra: dict[str, str], *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_require_psql(), url, "-X", "-q", *flags, "-f", str(script)],
        env={**os.environ, **env_extra},
        capture_output=True,
        text=True,
        check=False,
    )


def _as(url: str, sql: str) -> None:
    asyncio.run(_execute(url, sql))


def _denied(url: str, sql: str) -> None:
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        _as(url, sql)


def _stat_statements_enabled(dbname: str) -> bool:
    row = _fetch_row(dbname, "SELECT current_setting('shared_preload_libraries')")
    return "pg_stat_statements" in str(row[0])


def test_role_scripts_create_least_privilege_roles(role_cleanup, fresh_db):
    _require_psql()
    _run(
        fresh_db,
        f"CREATE ROLE {OWNER_ROLE} LOGIN NOSUPERUSER CREATEROLE PASSWORD '{PASSWORDS['owner']}';"
        f'ALTER DATABASE "{fresh_db}" OWNER TO {OWNER_ROLE};'
        # PostGIS is not a trusted extension; on Neon it predates the owner's objects too.
        "CREATE EXTENSION postgis;",
    )
    owner_url = _role_url(fresh_db, OWNER_ROLE, PASSWORDS["owner"])
    cfg = _alembic_cfg(fresh_db)
    cfg.set_main_option("sqlalchemy.url", owner_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    command.upgrade(cfg, "head")
    _as(owner_url, ANALYST_FIXTURE_SQL)

    stats = _stat_statements_enabled(fresh_db)
    if stats:
        _run(fresh_db, "CREATE EXTENSION pg_stat_statements; SELECT pg_stat_statements_reset();")

    verifiers = {role: scram_verifier(PASSWORDS[role]) for role in ("migrator", "app")}
    created = _psql(
        owner_url,
        ROLES_DIR / "create_roles.sql",
        {"MIGRATOR_PASSWORD_SCRAM": verifiers["migrator"], "APP_PASSWORD_SCRAM": verifiers["app"]},
        "--echo-queries",
    )
    assert created.returncode == 0, created.stderr
    sent = created.stdout + created.stderr
    assert "CREATE ROLE migrator" in sent
    assert verifiers["migrator"] in sent and verifiers["app"] in sent
    for plaintext in (PASSWORDS["migrator"], PASSWORDS["app"]):
        assert plaintext not in sent

    stored = _fetch_row(
        fresh_db,
        "SELECT (SELECT rolpassword FROM pg_authid WHERE rolname = 'migrator'),"
        " (SELECT rolpassword FROM pg_authid WHERE rolname = 'app')",
    )
    assert stored == [verifiers["migrator"], verifiers["app"]]
    if stats:
        texts = asyncio.run(_fetchall(_db_url(fresh_db), "SELECT query FROM pg_stat_statements"))
        assert any("CREATE ROLE" in t for t in texts)
        for plaintext in (PASSWORDS["migrator"], PASSWORDS["app"]):
            assert not any(plaintext in t for t in texts)

    verified = _psql(owner_url, ROLES_DIR / "verify_roles.sql", {})
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "ALL ROLE CHECKS PASSED" in verified.stdout

    app_url = _role_url(fresh_db, "app", PASSWORDS["app"])
    migrator_url = _role_url(fresh_db, "migrator", PASSWORDS["migrator"])
    analyst_url = _role_url(fresh_db, "analyst", "test-analyst-pw")
    with pytest.raises(asyncpg.exceptions.InvalidPasswordError):
        _as(_role_url(fresh_db, "app", "wrong-pw"), "SELECT 1")
    with pytest.raises(asyncpg.exceptions.InvalidPasswordError):
        _as(_role_url(fresh_db, "migrator", "wrong-pw"), "SELECT 1")

    _as(app_url, "SELECT count(*) FROM accidents; SELECT count(*) FROM weather")
    upsert = (
        "INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code)"
        " VALUES (1, DATE '2026-01-01', {score}, 'green')"
        " ON CONFLICT (route_id, prediction_date) DO UPDATE SET risk_score = EXCLUDED.risk_score"
    )
    _as(app_url, upsert.format(score=1.0))
    _as(app_url, upsert.format(score=2.0))
    _as(app_url, "DELETE FROM historical_predictions WHERE prediction_date < DATE '2026-06-01'")
    _denied(app_url, "UPDATE accidents SET accident_id = accident_id")
    _denied(app_url, "DELETE FROM weather")
    _denied(app_url, "INSERT INTO accidents DEFAULT VALUES")
    _denied(app_url, "CREATE TABLE app_should_not_create (x int)")
    _denied(app_url, "TRUNCATE historical_predictions")
    _denied(app_url, "SELECT setval('historical_predictions_id_seq', 1)")

    _as(analyst_url, "SELECT count(*) FROM historical_predictions")
    for sql in ("UPDATE accidents SET accident_id = accident_id", "DELETE FROM weather", "TRUNCATE accidents"):
        # READ WRITE overrides the analyst's read-only default, so only the grants stop it.
        _denied(analyst_url, f"BEGIN READ WRITE; {sql}; COMMIT")

    _as(migrator_url, "CREATE TABLE new_after_roles (id serial PRIMARY KEY)")
    _as(app_url, "SELECT count(*) FROM new_after_roles")
    _as(analyst_url, "SELECT count(*) FROM new_after_roles")
    _denied(app_url, "INSERT INTO new_after_roles DEFAULT VALUES")

    _as(migrator_url, "GRANT UPDATE ON accidents TO app")
    _as(owner_url, "GRANT app TO analyst")
    stray = _psql(owner_url, ROLES_DIR / "verify_roles.sql", {})
    assert stray.returncode != 0
    assert "app has no INSERT/UPDATE/DELETE on accidents" in stray.stderr
    assert "no role memberships (pg_auth_members empty): analyst" in stray.stderr
    assert "nobody can SET or INHERIT app" in stray.stderr


def test_create_roles_refuses_plaintext_in_the_scram_variables(role_cleanup, fresh_db):
    _require_psql()
    _run(fresh_db, ANALYST_FIXTURE_SQL)
    result = _psql(
        _db_url(fresh_db),
        ROLES_DIR / "create_roles.sql",
        {"MIGRATOR_PASSWORD_SCRAM": "plain-migrator-pw", "APP_PASSWORD_SCRAM": "plain-app-pw"},
    )
    assert result.returncode != 0
    assert "SCRAM-SHA-256 verifiers" in result.stderr
    assert "plain-migrator-pw" not in result.stdout + result.stderr
    assert _fetch_row(fresh_db, "SELECT count(*) FROM pg_roles WHERE rolname IN ('migrator', 'app')") == [0]


def test_role_cleanup_refuses_a_remote_admin_url(monkeypatch):
    monkeypatch.setitem(globals(), "ADMIN_URL", "postgresql://u:p@ep-x.neon.tech/postgres")
    with pytest.raises(RuntimeError, match="non-local"):
        _drop_test_roles()
