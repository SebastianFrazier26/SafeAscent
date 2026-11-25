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


def _iac_root(tmp_path, railway_ts: str):
    (tmp_path / ".railway").mkdir()
    (tmp_path / ".railway" / "railway.ts").write_text(railway_ts)
    return tmp_path


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


def test_drifted_iac_start_is_reported(tmp_path):
    source = (REPO_ROOT / guard.IAC_FILE).read_text().replace(
        "worker --loglevel=info --concurrency=2 -E", "worker --beat --loglevel=info"
    )
    problems = guard.mismatches(_compose_services(), _iac_root(tmp_path, source))
    assert len(problems) == 1 and "celery-worker" in problems[0]


def test_service_missing_from_iac_is_reported(tmp_path):
    source = (REPO_ROOT / guard.IAC_FILE).read_text().replace('service("beat"', 'service("beat-renamed"')
    problems = guard.mismatches(_compose_services(), _iac_root(tmp_path, source))
    assert len(problems) == 1 and "'beat'" in problems[0]


def test_missing_iac_file_is_reported(tmp_path):
    assert guard.mismatches(_compose_services(), tmp_path) == [f"{guard.IAC_FILE} is missing"]


def test_repo_has_no_config_as_code_files():
    assert guard.config_as_code_files(REPO_ROOT) == []


def test_config_as_code_files_are_reported(tmp_path):
    for rel in ("railway.toml", "backend/railway.json", "frontend/railway.toml", "backend/railway-x.toml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("[deploy]\n")
    (tmp_path / "node_modules" / "railway").mkdir(parents=True)
    (tmp_path / "node_modules" / "railway" / "railway.json").write_text("{}")
    (tmp_path / ".railway").mkdir()
    (tmp_path / ".railway" / "railway.ts").write_text("")
    assert guard.config_as_code_files(tmp_path) == [
        "backend/railway-x.toml", "backend/railway.json", "frontend/railway.toml", "railway.toml",
    ]


def test_iac_replicas_reads_region_map_and_scalar():
    assert guard.iac_replicas('replicas: { "us-east4-eqdc4a": 1 },') == 1
    assert guard.iac_replicas('replicas: { "a": 2, "b": 1 },') == 3
    assert guard.iac_replicas("replicas: 2,") == 2
    assert guard.iac_replicas("start: \"x\",") is None
