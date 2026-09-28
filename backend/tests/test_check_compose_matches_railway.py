"""Tests for scripts/check_compose_matches_railway.py, the compose/Railway parity CI guard."""
import importlib.util
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_compose_matches_railway", REPO_ROOT / "scripts" / "check_compose_matches_railway.py"
)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_compose_matches_railway"] = guard
_spec.loader.exec_module(guard)


def _compose_services() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]


def test_repo_compose_matches_railway_start_commands():
    assert guard.mismatches(_compose_services(), REPO_ROOT) == []


def test_mismatched_command_is_reported():
    services = _compose_services()
    services["worker"]["command"] = "celery -A app.celery_app worker --beat --loglevel=info"
    problems = guard.mismatches(services, REPO_ROOT)
    assert len(problems) == 1 and "worker" in problems[0]


def test_list_form_command_is_compared_by_argv():
    # `docker compose config --format json` normalizes command to a list.
    services = _compose_services()
    services["beat"]["command"] = guard.start_command(REPO_ROOT, "beat")
    assert guard.mismatches(services, REPO_ROOT) == []


def test_missing_service_is_reported():
    services = _compose_services()
    del services["beat"]
    assert any("beat" in p for p in guard.mismatches(services, REPO_ROOT))
