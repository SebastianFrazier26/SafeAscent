"""trainer_engine (pre-flight F48): same refusals as ingest_engine, and no message names the URL."""

import ssl

import pytest

import app.ml.db as ml_db
from app.config import settings
from app.ml.db import trainer_engine

REMOTE = "postgresql+asyncpg://trainer:secret-pw@ep-fixture-123.us-east-2.aws.neon.tech/neondb"


@pytest.fixture
def captured(monkeypatch):
    seen: dict[str, object] = {}

    def fake(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(ml_db, "create_async_engine", fake)
    return seen


def _refused(monkeypatch, url):
    monkeypatch.setattr(settings, "TRAINER_DATABASE_URL", url)
    with pytest.raises(SystemExit) as refused:
        trainer_engine()
    message = str(refused.value)
    assert "TRAINER_DATABASE_URL" in message
    assert "secret-pw" not in message and "neon.tech" not in message and "trainer:" not in message
    return message


@pytest.mark.parametrize("url", [None, ""])
def test_refuses_without_trainer_url(monkeypatch, captured, url):
    assert "not set" in _refused(monkeypatch, url)
    assert captured == {}


@pytest.mark.parametrize("host", ["ep-fixture-123-pooler.us-east-2.aws.neon.tech",
                                  "EP-Fixture-123-Pooler.US-EAST-2.aws.neon.tech"])
def test_refuses_the_pooler_host(monkeypatch, captured, host):
    assert "pooler" in _refused(monkeypatch, f"postgresql+asyncpg://trainer:secret-pw@{host}/db")
    assert captured == {}


@pytest.mark.parametrize("query", ["?sslmode=require", "?ssl=require", "?sslmode=prefer", "?ssl=disable"])
def test_refuses_weaker_tls_on_a_remote_host(monkeypatch, captured, query):
    assert "verify-full" in _refused(monkeypatch, REMOTE + query)
    assert captured == {}


def test_refuses_a_remote_host_without_a_verifying_context(monkeypatch, captured):
    monkeypatch.setattr("app.pipelines.db.connect_args_for", lambda url: {"ssl": _unverified()})
    assert "verify-full" in _refused(monkeypatch, REMOTE)
    assert captured == {}


def _unverified() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def test_refuses_an_unparseable_or_hostless_url(monkeypatch, captured):
    _refused(monkeypatch, "not a url secret-pw")
    _refused(monkeypatch, "postgresql+asyncpg://trainer:secret-pw@/neondb")
    assert captured == {}


def test_remote_url_gets_verify_full(monkeypatch, captured):
    monkeypatch.setattr(settings, "TRAINER_DATABASE_URL", REMOTE + "?ssl=verify-full")
    trainer_engine()
    context = captured["connect_args"]["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert captured["poolclass"].__name__ == "NullPool"


def test_local_url_builds_without_tls(monkeypatch, captured):
    monkeypatch.setattr(settings, "TRAINER_DATABASE_URL", "postgresql+asyncpg://t:pw@localhost:5432/postgres")
    trainer_engine()
    assert captured["connect_args"] == {}
