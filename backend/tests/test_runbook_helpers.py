"""scripts/runbook_helpers.sh, sourced in both shells the runbooks are run from, with fake
URLs only. No helper here reaches a database: every case refuses or stops before psql/uv."""

import shutil
import subprocess
from pathlib import Path

import pytest

HELPERS = Path(__file__).resolve().parents[1] / "scripts" / "runbook_helpers.sh"
FAKE = "postgresql+asyncpg://u:p%40ss@h.example/db?ssl=verify-full"
SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]


def _run(shell: str, script: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(cwd)}
    return subprocess.run(
        [shell, "-c", f". {HELPERS}\n{script}"], cwd=cwd, env=base | (env or {}), capture_output=True, text=True
    )


@pytest.fixture(params=SHELLS)
def shell(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def test_retarget_points_the_url_at_the_branch_host(shell, tmp_path):
    out = _run(shell, f"_retarget '{FAKE}'", tmp_path, {"TARGET_HOST": "ep-branch.example"})
    assert out.returncode == 0, out.stderr
    assert out.stdout == "postgresql+asyncpg://u:p%40ss@ep-branch.example/db?ssl=verify-full"


def test_production_keeps_the_env_file_host(shell, tmp_path):
    out = _run(shell, f"_retarget '{FAKE}'", tmp_path, {"TARGET_HOST": "production"})
    assert (out.returncode, out.stdout) == (0, FAKE)


@pytest.mark.parametrize(
    "env, message",
    [
        ({}, "TARGET_HOST is unset"),
        ({"TARGET_HOST": ""}, "TARGET_HOST is unset"),
        ({"TARGET_HOST": "evil#host"}, "bare host name"),
        ({"TARGET_HOST": "a/b"}, "bare host name"),
    ],
)
def test_retarget_refuses_without_an_explicit_target(shell, tmp_path, env, message):
    out = _run(shell, f"_retarget '{FAKE}'", tmp_path, env)
    assert out.returncode != 0 and out.stdout == ""
    assert message in out.stderr


def test_retarget_refuses_a_url_it_cannot_rewrite(shell, tmp_path):
    out = _run(shell, "_retarget 'postgresql://u:p@h.example'", tmp_path, {"TARGET_HOST": "ep-branch.example"})
    assert out.returncode != 0 and out.stdout == ""


@pytest.mark.parametrize("helper", ["ING r9", "INGMOD app.pipelines.gnis", "MIG current", "OWNER_PSQL -c 'SELECT 1'",
                                    "ANALYST_PSQL -c 'SELECT 1'", "VERIFY tests/verify"])
def test_every_helper_refuses_before_reading_env_files_when_target_is_unset(shell, tmp_path, helper):
    out = _run(shell, f"{helper}; echo rc=$?", tmp_path)
    assert "TARGET_HOST is unset" in out.stderr
    assert out.stdout.strip() == "rc=1"


def test_a_helper_refuses_a_missing_env_file(shell, tmp_path):
    out = _run(shell, "OWNER_PSQL -c 'SELECT 1'; echo rc=$?", tmp_path, {"TARGET_HOST": "production"})
    assert "missing ./.env.owner" in out.stderr
    assert out.stdout.strip() == "rc=1"


def test_split_and_verify_full_keep_the_password_out_of_the_url(shell, tmp_path):
    script = (
        "split_pg_url 'postgresql://u:p%40ss@h.example/db?sslmode=require&application_name=a b'; verify_full_url; "
        'printf "%s\\n%s\\n" "$PG_URL_NOPASS" "$PGPASSWORD"'
    )
    out = _run(shell, script, tmp_path)
    assert out.returncode == 0, out.stderr
    url, password = out.stdout.splitlines()
    assert url == "postgresql://u@h.example/db?application_name=a%20b&sslmode=verify-full&sslrootcert=system"
    assert password == "p@ss"
