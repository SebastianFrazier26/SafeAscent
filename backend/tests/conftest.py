"""
Pytest configuration and shared fixtures for SafeAscent backend tests.

Provides:
- async_client: HTTP client for API integration tests
- test_db: Test database session
- sample_data: Reusable test data fixtures
"""
# ruff: noqa: E402
import os

# Settings() is built at import time and DATABASE_URL is required, so this must
# run before any `app` import. setdefault keeps CI's service URL when set.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test_user:test_password@localhost:5432/safeascent_test"
)
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from typing import AsyncGenerator

from app.main import app


# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)


@pytest_asyncio.fixture(scope="function", autouse=True)
async def reset_db_engine():
    """
    Reset database engine for each test to avoid event loop conflicts.

    This fixture runs automatically before each test and ensures that
    database connections don't get reused across different event loops.

    The issue: SQLAlchemy's connection pool is bound to the event loop
    where the engine was first created. When pytest-asyncio creates a new
    event loop per test, old connections fail with "different loop" error.

    The solution: Dispose the engine before each test to force fresh connections.
    """
    # Import the module-level engine
    import app.db.session as db_session

    # Dispose existing engine to clear connection pool
    await db_session.engine.dispose()

    yield

    # Cleanup after test (optional, but good practice)
    await db_session.engine.dispose()


@pytest.fixture(autouse=True)
def _app_main_is_not_replaced():
    """Every fixture that sets app.dependency_overrides must reach the app test_client serves. A
    test that leaves a different app in app.main (an unrestored reload) fails here, at its own
    teardown, instead of a later DB test failing on the default DATABASE_URL."""
    yield
    import app.main as main
    assert main.app is app, "a test replaced app.main.app without restoring it"


@pytest_asyncio.fixture(scope="function")
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """
    Async HTTP client for testing FastAPI endpoints.

    Scope: function - Each test gets a fresh client with isolated event loop.
    This prevents "Task got Future attached to a different loop" errors.

    Usage:
        async def test_example(async_client):
            response = await async_client.get("/api/v1/health")
            assert response.status_code == 200
    """
    # Use ASGITransport to properly mount the ASGI app
    # Each invocation creates a new transport to avoid connection pool reuse
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(scope="function")
def test_client():
    """
    Synchronous HTTP client for testing FastAPI endpoints.

    This fixture creates a fresh TestClient for each test function,
    ensuring clean event loop state between tests. This prevents
    "Task got Future attached to a different loop" errors when
    tests make multiple sequential API calls.

    Scope: function - Each test gets a fresh client.

    Usage:
        def test_example(test_client):
            response = test_client.post("/api/v1/predict", json={...})
            assert response.status_code == 200
    """
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def anyio_backend():
    """Configure anyio backend for pytest-asyncio."""
    return "asyncio"


# --- stored scores (P3 Task 22) --------------------------------------------------------

SCORES_VERSION = "eb_v1-t22-fixture"
SCORE_ROUTES = """
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (900000002, 'Fixture Route Two', 900000110, 'Trad'), (900000003, 'Fixture Route Three', 900000110, 'Trad');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000002, 900000110, 'trad', false, '{trad}', 'rt-mp-v3', true, NULL, 'x', 'v'),
  (900000003, 900000110, 'trad', false, '{trad}', 'rt-mp-v3', true, NULL, 'x', 'v');
"""
_SCORE_COLS = ("model_version, route_id, month, type_group, area_id, insufficient, insufficient_reason, rr_mean, rr_q05, "
               "rr_q95, percentile, pct_q05, pct_q95, conf_level, n_eff_geo, n_eff_feature, per10k_mean, per10k_q05, "
               "per10k_q95, per10k_reason")
# (route, month): ok/high, ok/low, insufficient in July; route 1 also has January for the profile.
SCORE_ROWS = [
    (900000001, 7, "false, NULL, 1.4, 0.9, 2.2, 40.0, 20.0, 60.0, 'high', 3.5, 6.0, 2.5, 1.0, 4.0, NULL"),
    (900000001, 1, "false, NULL, 0.6, 0.3, 1.1, 12.04, 3.0, 30.0, 'medium', 1.0, 2.0, NULL, NULL, NULL, 'ticks_incomplete'"),
    (900000002, 7, "false, NULL, 2.0, 0.5, 6.0, 62.34, 10.0, 90.0, 'low', 0.2, 0.5, NULL, NULL, NULL, 'ticks_missing'"),
    (900000003, 7, "true, 'no_evidence', NULL, NULL, NULL, NULL, NULL, NULL, 'insufficient', 0, 0, NULL, NULL, NULL, NULL"),
]


def scores_bundle(version: str) -> bytes:
    """A hand-built bundle for trad in July and January (the shape test_explain uses)."""
    import math

    import numpy as np

    from app.ml.bundle import ModelBundle

    names = ["intercept", "fam_mountain", "a_trad", "m_7", "d_trad_7", "z_elevation_m", "ind_elevation_missing"]
    coef = np.array([math.log(0.05), 0.0, 0.2, 0.1, 0.0, 0.3, 0.4])
    const = {"trad": {"7": -(coef[0] + coef[2] + coef[3]), "1": -(coef[0] + coef[2])}}
    return ModelBundle(version, "rock_family", {"coef": coef, "center": np.zeros(7), "scale": np.ones(7)},
                       {"names": names, "const": {g: {m: float(v) for m, v in c.items()} for g, c in const.items()}}
                       ).to_bytes()


def scores_seed_sql(status: str) -> str:
    from app.ml.bundle import sha256

    raw = scores_bundle(SCORES_VERSION)
    promoted = "now()" if status in ("active", "previous", "retired") else "NULL"
    rows = ",\n  ".join(f"('{SCORES_VERSION}', {r}, {m}, 'trad', 900000110, {rest})" for r, m, rest in SCORE_ROWS)
    return (
        SCORE_ROUTES
        + "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, promoted_at, "
          "bundle, bundle_sha256, bundle_bytes, scores_rows) VALUES "
          f"('{SCORES_VERSION}', 'eb_v1', 'h', '1990-01-01', '2026-10-01', '{status}', {promoted}, "
          f"'\\x{raw.hex()}'::bytea, '{sha256(raw)}', {len(raw)}, {len(SCORE_ROWS)});\n"
        + f"INSERT INTO model.route_scores ({_SCORE_COLS}) VALUES\n  {rows};\n"
    )


@pytest.fixture
def scores_redis(monkeypatch):
    """A fake Redis behind app.utils.cache, so tests never touch a local server."""
    import fakeredis

    from app.utils import cache as cache_module

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: fake)
    return fake


def _seeded(status: str):
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db.session import get_db
    from tests.pgtest import migrated_db, sa_url
    from tests.test_migration_0012 import SEED
    from tests.test_migrations import ADMIN_URL

    if not ADMIN_URL:
        pytest.skip("MIGRATIONS_TEST_ADMIN_URL not set")
    with migrated_db(seed_sql=SEED + scores_seed_sql(status)) as name:
        url = sa_url(name)

        async def override():
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with AsyncSession(engine) as session:
                    yield session
            finally:
                await engine.dispose()

        app.dependency_overrides[get_db] = override
        try:
            yield SimpleNamespace(version=SCORES_VERSION, db=name)
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def seeded_scores(scores_redis):
    yield from _seeded("active")


@pytest.fixture
def seeded_scores_no_active(scores_redis):
    yield from _seeded("candidate")
