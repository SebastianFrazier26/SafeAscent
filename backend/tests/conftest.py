"""
Pytest configuration and shared fixtures for SafeAscent backend tests.

Provides:
- async_client: HTTP client for API integration tests
- test_db: Test database session
- sample_data: Reusable test data fixtures
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from typing import AsyncGenerator

from app.main import app
from app.db.session import get_db


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
