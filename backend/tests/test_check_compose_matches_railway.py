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


def test_every_railway_config_is_compared_or_exempt():
    assert guard.unlisted_configs(REPO_ROOT) == []


def test_an_unlisted_railway_config_is_reported(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "railway-surprise.toml").write_text("[deploy]\n")
    assert guard.unlisted_configs(tmp_path) == ["backend/railway-surprise.toml"]


def test_every_exempt_config_exists():
    for path in guard.NO_COMPOSE_COUNTERPART.values():
        assert (REPO_ROOT / path).is_file(), path


def test_repo_railway_ts_matches_toml_start_commands():
    assert guard.iac_mismatches(REPO_ROOT) == []


def _iac_root(tmp_path, railway_ts: str):
    for config in guard.IAC_SERVICES:
        target = tmp_path / config
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO_ROOT / config).read_text())
    (tmp_path / ".railway").mkdir()
    (tmp_path / ".railway" / "railway.ts").write_text(railway_ts)
    return tmp_path


def test_drifted_iac_start_is_reported(tmp_path):
    source = (REPO_ROOT / guard.IAC_FILE).read_text().replace(
        "worker --loglevel=info --concurrency=2 -E", "worker --beat --loglevel=info"
    )
    problems = guard.iac_mismatches(_iac_root(tmp_path, source))
    assert len(problems) == 1 and "celery-worker" in problems[0]


def test_service_missing_from_iac_is_reported(tmp_path):
    source = 'const x = service("celery-worker", { start: "celery -A app.celery_app worker --loglevel=info --concurrency=2 -E" });'
    problems = guard.iac_mismatches(_iac_root(tmp_path, source))
    assert {p.split("'")[1] for p in problems} == {"beat", "catalog-cycle"}


def test_missing_iac_file_is_reported(tmp_path):
    assert guard.iac_mismatches(tmp_path) == [f"{guard.IAC_FILE} is missing"]
