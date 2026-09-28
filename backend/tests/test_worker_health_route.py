import time

import fakeredis
from fastapi.testclient import TestClient

from app.celery_signals import HEARTBEAT_KEY


def _client(monkeypatch, redis_client):
    import app.main as main

    monkeypatch.setattr(main, "get_redis_client", lambda: redis_client)
    return TestClient(main.app)


def test_worker_health_200_with_fresh_heartbeat(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    fake.set(HEARTBEAT_KEY, f"{time.time():.3f}", ex=120)
    response = _client(monkeypatch, fake).get("/health/worker")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["last_heartbeat_age_seconds"] < 5
    assert body["expired_tasks_7d"] == {}


def test_worker_health_503_without_heartbeat(monkeypatch):
    response = _client(monkeypatch, fakeredis.FakeRedis(decode_responses=True)).get("/health/worker")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


def test_worker_health_503_without_redis(monkeypatch):
    response = _client(monkeypatch, None).get("/health/worker")
    assert response.status_code == 503
