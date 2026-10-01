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


@pytest.mark.parametrize("url", ["", "not a url", "postgresql:///db"])
def test_split_verified_refuses_a_url_without_user_and_host(shell, tmp_path, url):
    out = _run(shell, f"_split_verified '{url}'; echo rc=$?", tmp_path)
    assert "no user@host" in out.stderr
    assert out.stdout.strip() == "rc=1"


BARE_PATH = "/usr/bin:/bin"
needs_no_system_psql = pytest.mark.skipif(
    shutil.which("psql", path=BARE_PATH) is not None, reason="psql is on the bare system PATH"
)


def _fake_bin(directory: Path, name: str, body: str = "exit 0") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    exe.chmod(0o755)
    return directory


@needs_no_system_psql
def test_sourcing_puts_the_keg_only_libpq_on_path(shell, tmp_path):
    keg = _fake_bin(tmp_path / "libpq" / "bin", "psql")
    out = _run(shell, "command -v psql", tmp_path, {"PATH": BARE_PATH, "LIBPQ_BIN": str(keg)})
    assert (out.returncode, out.stdout.strip()) == (0, str(keg / "psql"))


def test_sourcing_keeps_a_psql_already_on_path(shell, tmp_path):
    first = _fake_bin(tmp_path / "first", "psql")
    keg = _fake_bin(tmp_path / "keg", "psql")
    out = _run(shell, "command -v psql", tmp_path, {"PATH": f"{first}:{BARE_PATH}", "LIBPQ_BIN": str(keg)})
    assert out.stdout.strip() == str(first / "psql")


@needs_no_system_psql
def test_a_psql_helper_refuses_clearly_when_psql_is_missing(shell, tmp_path):
    (tmp_path / ".env.owner").write_text("OWNER_DATABASE_URL=postgresql://u:p@h.example/db\n", encoding="utf-8")
    env = {"PATH": BARE_PATH, "LIBPQ_BIN": str(tmp_path / "absent"), "TARGET_HOST": "production"}
    out = _run(shell, "OWNER_PSQL -c 'SELECT 1'; echo rc=$?", tmp_path, env)
    assert "psql not found" in out.stderr
    assert out.stdout.strip() == "rc=1"


def test_branch_host_refuses_an_empty_name_before_calling_neonctl(shell, tmp_path):
    marker = tmp_path / "neonctl-called"
    fake = _fake_bin(tmp_path / "bin", "neonctl", f"touch {marker}")
    out = _run(shell, "branch_host ''", tmp_path, {"PATH": f"{fake}:{BARE_PATH}"})
    assert out.returncode != 0 and out.stdout == ""
    assert "branch name" in out.stderr
    assert not marker.exists()


def test_save_once_writes_a_new_file_and_echoes_it(shell, tmp_path):
    out = _run(shell, "save_once out.txt echo hello; echo rc=$?", tmp_path)
    assert out.stdout.splitlines() == ["hello", "rc=0"]
    assert (tmp_path / "out.txt").read_text() == "hello\n"


def test_save_once_never_replaces_existing_evidence(shell, tmp_path):
    (tmp_path / "out.txt").write_text("before apply\n")
    out = _run(shell, "save_once out.txt echo after; echo rc=$?", tmp_path)
    assert "exists: not overwriting" in out.stderr and out.stdout.strip() == "rc=1"
    assert (tmp_path / "out.txt").read_text() == "before apply\n"


@pytest.mark.parametrize("command", ["false", "true"])
def test_save_once_leaves_nothing_after_a_failed_or_empty_run(shell, tmp_path, command):
    out = _run(shell, f"save_once out.txt {command}; echo rc=$?", tmp_path)
    assert out.stdout.strip() == "rc=1" and "not written" in out.stderr
    assert list(tmp_path.iterdir()) == []


def test_save_once_runs_a_helper_function(shell, tmp_path):
    out = _run(shell, "greet() { echo \"hi $1\"; }; save_once g.txt greet there; echo rc=$?", tmp_path)
    assert out.stdout.splitlines() == ["hi there", "rc=0"]
