import ssl

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

import app.pipelines.db as pipelines_db
from app.config import settings
from app.pipelines.db import ingest_engine, verified_connect_args

REMOTE = "postgresql+asyncpg://ingest:secret-pw@ep-fixture-123.us-east-2.aws.neon.tech/neondb"


def test_remote_url_gets_a_verifying_context():
    args = verified_connect_args(REMOTE)
    context = args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


@pytest.mark.parametrize("query", ["?sslmode=require", "?ssl=require", "?sslmode=prefer", "?ssl=disable"])
def test_ingest_engine_refuses_weaker_tls_on_a_remote_host(query):
    with pytest.raises(SystemExit) as refused:
        verified_connect_args(REMOTE + query)
    assert "secret-pw" not in str(refused.value)
    assert "verify-full" in str(refused.value)


def test_explicit_verify_full_is_accepted():
    assert isinstance(verified_connect_args(REMOTE + "?ssl=verify-full")["ssl"], ssl.SSLContext)


def test_local_urls_need_no_tls():
    assert verified_connect_args("postgresql+asyncpg://test_user:pw@localhost:5432/postgres") == {}


# --- F7 (pre-flight ruling): D14's "job CLIs refuse without INGEST_DATABASE_URL" was
# asserted but never tested; ingest_engine() itself was never exercised at all.

def test_ingest_engine_refuses_without_ingest_database_url(monkeypatch):
    monkeypatch.setattr(settings, "INGEST_DATABASE_URL", None)
    with pytest.raises(SystemExit) as refused:
        ingest_engine()
    assert "INGEST_DATABASE_URL" in str(refused.value)


def test_ingest_engine_builds_an_engine_with_verified_connect_args(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_async_engine(url: str, **kwargs: object) -> AsyncEngine:
        captured["url"] = url
        captured["connect_args"] = kwargs.get("connect_args")
        return object()  # type: ignore[return-value]

    monkeypatch.setattr(pipelines_db, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(settings, "INGEST_DATABASE_URL", "postgresql+asyncpg://test_user:pw@localhost:5432/postgres")

    ingest_engine()

    assert captured["url"] == "postgresql+asyncpg://test_user:pw@localhost:5432/postgres"
    assert captured["connect_args"] == {}


@pytest.mark.parametrize("host", ["ep-fixture-123-pooler.us-east-2.aws.neon.tech", "EP-Fixture-123-Pooler.US-EAST-2.aws.neon.tech"])
def test_ingest_engine_refuses_the_pooler_host_for_every_job(monkeypatch, host):
    """S2: the pooler check runs at engine creation, so no ingest job can skip it."""
    monkeypatch.setattr(pipelines_db, "create_async_engine", lambda *a, **k: pytest.fail("built a pooler engine"))
    monkeypatch.setattr(pipelines_db.settings, "INGEST_DATABASE_URL", f"postgresql+asyncpg://ingest:x@{host}/db")
    with pytest.raises(SystemExit, match="pooler"):
        ingest_engine()


@pytest.mark.parametrize("url", ["not a url secret-pw", "postgresql+asyncpg://ingest:secret-pw@/neondb"])
def test_ingest_engine_never_echoes_an_unusable_url(monkeypatch, url):
    """Twin of tests/ml/test_db.py: connect_args_for's ValueError quotes the (masked) URL."""
    monkeypatch.setattr(pipelines_db, "create_async_engine", lambda *a, **k: pytest.fail("built an engine"))
    monkeypatch.setattr(settings, "INGEST_DATABASE_URL", url)
    with pytest.raises(SystemExit) as refused:
        ingest_engine()
    message = str(refused.value)
    assert "INGEST_DATABASE_URL" in message
    assert "secret-pw" not in message and "ingest:" not in message and "***" not in message and "neondb" not in message
