from __future__ import annotations

import pytest

from app.tasks import scores_heartbeat as hb

SCORES_URL = "https://hc-ping.example/scores"
NIGHTLY_URL = "https://hc-ping.example/nightly"
GOOD = hb.ActiveRow(version="eb-1", bundle_present=True, sha_ok=True)


@pytest.fixture
def pings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str | None, str]]:
    calls: list[tuple[str | None, str]] = []
    monkeypatch.setattr(hb, "ping", lambda url, suffix="": calls.append((url, suffix)) or True)
    monkeypatch.setattr(hb.settings, "HEALTHCHECKS_SCORES_URL", SCORES_URL)
    monkeypatch.setattr(hb.settings, "HEALTHCHECKS_NIGHTLY_URL", NIGHTLY_URL)
    return calls


def _facts(monkeypatch: pytest.MonkeyPatch, active: list[hb.ActiveRow], missing: int = 0) -> None:
    async def load(month: int) -> hb.Facts:
        return hb.Facts(active=active, missing_routes=missing)

    monkeypatch.setattr(hb, "_load_facts", load)


def test_no_active_version_fails_with_reason(pings, monkeypatch):
    _facts(monkeypatch, [])
    result = hb.run()
    assert result == {"ok": False, "reason": "no_active_version"}
    assert (SCORES_URL, "/fail") in pings and (NIGHTLY_URL, "/fail") in pings
    assert (SCORES_URL, "") not in pings


def test_two_active_versions_fail(pings, monkeypatch):
    _facts(monkeypatch, [GOOD, hb.ActiveRow("eb-2", True, True)])
    assert hb.run() == {"ok": False, "reason": "multiple_active_versions"}
    assert (SCORES_URL, "/fail") in pings


def test_missing_current_month_rows_fail_with_the_count(pings, monkeypatch):
    _facts(monkeypatch, [GOOD], missing=17)
    result = hb.run()
    assert result == {"ok": False, "reason": "missing_scores", "version": "eb-1", "missing": 17}
    assert (SCORES_URL, "/fail") in pings


@pytest.mark.parametrize("row,reason", [(hb.ActiveRow("eb-1", False, False), "bundle_missing"),
                                        (hb.ActiveRow("eb-1", True, False), "bundle_sha_mismatch")])
def test_bundle_problems_fail(pings, monkeypatch, row, reason):
    _facts(monkeypatch, [row])
    assert hb.run() == {"ok": False, "reason": reason, "version": "eb-1"}
    assert (SCORES_URL, "/fail") in pings


def test_healthy_pings_success_on_both_checks(pings, monkeypatch):
    _facts(monkeypatch, [GOOD])
    assert hb.run() == {"ok": True, "version": "eb-1", "month": hb.utc_today().month}
    assert pings == [(SCORES_URL, ""), (NIGHTLY_URL, "")]


def test_a_database_error_fails_the_check_and_is_reraised(pings, monkeypatch):
    async def boom(month: int) -> hb.Facts:
        raise RuntimeError("db down")

    monkeypatch.setattr(hb, "_load_facts", boom)
    with pytest.raises(RuntimeError):
        hb.run()
    assert (SCORES_URL, "/fail") in pings and (NIGHTLY_URL, "/fail") in pings


def test_beat_runs_it_at_two_utc_in_place_of_the_nightly():
    from celery.schedules import crontab

    from app.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "compute-daily-safety-scores" not in schedule
    entry = schedule["scores-heartbeat"]
    assert entry["task"] == "app.tasks.scores_heartbeat.scores_heartbeat"
    assert entry["schedule"] == crontab(minute=0, hour=2)
    assert "app.tasks.scores_heartbeat" in celery_app.conf.include
