"""scripts/runbook_helpers.sh, sourced in both shells the runbooks are run from, with fake
URLs only. No helper here reaches a database: every case refuses or stops before psql/uv."""

import os
import pty
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


@pytest.mark.parametrize("helper", ["ING r9", "INGMOD app.pipelines.gnis", "INGLLM app.data.repair.r10_llm plan", "MIG current", "TRAIN run", "OWNER_PSQL -c 'SELECT 1'",
                                    "ANALYST_PSQL -c 'SELECT 1'", "VERIFY tests/verify"])
def test_every_helper_refuses_before_reading_env_files_when_target_is_unset(shell, tmp_path, helper):
    out = _run(shell, f"{helper}; echo rc=$?", tmp_path)
    assert "TARGET_HOST is unset" in out.stderr
    assert out.stdout.strip() == "rc=1"


def test_a_helper_refuses_a_missing_env_file(shell, tmp_path):
    out = _run(shell, "OWNER_PSQL -c 'SELECT 1'; echo rc=$?", tmp_path, {"TARGET_HOST": "production"})
    assert "missing ./.env.owner" in out.stderr
    assert out.stdout.strip() == "rc=1"


def test_train_refuses_a_missing_trainer_env_file(shell, tmp_path):
    out = _run(shell, "TRAIN rollback; echo rc=$?", tmp_path, {"TARGET_HOST": "ep-branch.example"})
    assert "missing ./.env.trainer" in out.stderr
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


def _csv(path: Path, header: str, *rows: str) -> None:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def test_same_ids_passes_on_the_same_keys_in_any_order(shell, tmp_path):
    _csv(tmp_path / "a.csv", "accident_id,correct", "2,yes", "1,no")
    _csv(tmp_path / "b.csv", "﻿accident_id,correct", "1,", "2,")
    out = _run(shell, "same_ids a.csv b.csv accident_id && echo PASS", tmp_path)
    assert out.stdout.splitlines() == ["2 ids match", "PASS"], out.stderr


def test_same_ids_compares_pairs_on_several_columns(shell, tmp_path):
    _csv(tmp_path / "a.csv", "low_id,high_id,decision", "1,2,merge")
    _csv(tmp_path / "b.csv", "low_id,high_id,decision", "1,3,")
    out = _run(shell, "same_ids a.csv b.csv low_id high_id && echo PASS", tmp_path)
    assert "MISMATCH" in out.stderr and "PASS" not in out.stdout


@pytest.mark.parametrize(
    "a_rows, b_rows, message",
    [
        ((), (), "has no rows"),
        (("1",), (), "has no rows"),
        (("1", "2"), ("1",), "MISMATCH 2 vs 1"),
        (("1", "1"), ("1", "2"), "MISMATCH"),
    ],
)
def test_same_ids_fails_closed(shell, tmp_path, a_rows, b_rows, message):
    _csv(tmp_path / "a.csv", "accident_id", *a_rows)
    _csv(tmp_path / "b.csv", "accident_id", *b_rows)
    out = _run(shell, "same_ids a.csv b.csv accident_id && echo PASS", tmp_path)
    assert message in out.stderr and "PASS" not in out.stdout


@pytest.mark.parametrize("rows", [("1", '""'), ("1", "   "), ("1", "\t")])
def test_same_ids_refuses_a_blank_key(shell, tmp_path, rows):
    _csv(tmp_path / "a.csv", "accident_id", *rows)
    out = _run(shell, "same_ids a.csv a.csv accident_id && echo PASS; echo rc=$?", tmp_path)
    assert "blank accident_id" in out.stderr and "PASS" not in out.stdout and out.stdout.strip() == "rc=1"


def test_same_ids_refuses_a_blank_column_of_a_pair(shell, tmp_path):
    _csv(tmp_path / "a.csv", "low_id,high_id", "1,2", "3, ")
    out = _run(shell, "same_ids a.csv a.csv low_id high_id && echo PASS", tmp_path)
    assert "line 3 has a blank high_id" in out.stderr and "PASS" not in out.stdout


@pytest.mark.parametrize("script", ["same_ids a.csv missing.csv accident_id", "same_ids a.csv a.csv nope", "same_ids a.csv a.csv"])
def test_same_ids_refuses_a_missing_file_column_or_argument(shell, tmp_path, script):
    _csv(tmp_path / "a.csv", "accident_id", "1")
    out = _run(shell, f"{script} && echo PASS", tmp_path)
    assert out.returncode != 0 and "PASS" not in out.stdout and out.stderr


def test_a_gate_block_without_the_helpers_fails_instead_of_matching(shell, tmp_path):
    # The C2 failure: a fresh shell where the comparison helper was never defined.
    _csv(tmp_path / "a.csv", "accident_id", "1")
    script = "( same_ids a.csv a.csv accident_id || exit 1; echo 'GATE OK' ); echo rc=$?"
    out = subprocess.run([shell, "-c", script], cwd=tmp_path, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(tmp_path)})
    assert out.stdout.strip() == "rc=1"


def test_gate_require_refuses_until_gate_pass_ran(shell, tmp_path):
    env = {"GATE_DIR": str(tmp_path / "gates")}
    before = _run(shell, "gate_require r4_dry; echo rc=$?", tmp_path, env)
    assert "has not passed: STOP" in before.stderr and before.stdout.strip() == "rc=1"
    passed = _run(shell, "gate_pass r4_dry && gate_require r4_dry", tmp_path, env)
    assert passed.stdout.splitlines() == ["GATE OK: r4_dry", "gate r4_dry passed"]


@pytest.mark.parametrize("name", ["", "../x", "a b"])
def test_gate_names_are_plain(shell, tmp_path, name):
    out = _run(shell, f"gate_pass '{name}'; echo rc=$?", tmp_path, {"GATE_DIR": str(tmp_path / "g")})
    assert out.stdout.strip() == "rc=1" and not (tmp_path / "g").exists()


def _backend(tmp_path: Path) -> Path:
    """A backend/-shaped cwd: bg_start re-sources the helpers from scripts/ in its child."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "runbook_helpers.sh").write_text(HELPERS.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


# A pattern no real process carries, so a repair running on this machine cannot change a result.
BG_ENV = {"TARGET_HOST": "ep-branch.example", "BG_POLL": "1", "BG_GUARD_PATTERN": "no-such-runbook-process-[x]"}


def test_bg_start_runs_in_the_background_and_bg_wait_reports_success(shell, tmp_path):
    cwd = _backend(tmp_path)
    script = "bg_start logs/a.log echo hi && bg_wait logs/a.log 30; echo rc=$?"
    out = _run(shell, script, cwd, BG_ENV)
    log = (cwd / "logs" / "a.log").read_text()
    assert "hi\n" in log and "RUNBOOK_EXIT=0" in log and "RUNBOOK_ELAPSED=" in log
    assert out.stdout.strip().endswith("rc=0") and "finished ok" in out.stdout


def test_bg_wait_reports_a_failed_command(shell, tmp_path):
    cwd = _backend(tmp_path)
    out = _run(shell, "bg_start f.log false && bg_wait f.log 30; echo rc=$?", cwd, BG_ENV)
    assert "FAILED" in out.stderr and out.stdout.strip().endswith("rc=1")


def test_bg_child_inherits_the_target_and_runs_helpers(shell, tmp_path):
    cwd = _backend(tmp_path)
    out = _run(shell, "bg_start t.log _retarget postgresql://u:p@h.example/db && bg_wait t.log 30; echo rc=$?", cwd, BG_ENV)
    assert "postgresql://u:p@ep-branch.example/db" in (cwd / "t.log").read_text()
    assert out.stdout.strip().endswith("rc=0")


def _run_under_job_control(shell: str, script: str, cwd: Path, env: dict[str, str]) -> str:
    """Runs SCRIPT with job control forced on (`set -m`) and a controlling terminal (a pty;
    zsh refuses to enable monitor mode without one). Under job control, bash/zsh assign a
    background job's leading process its own new pgid (pgid == its own pid) before it runs
    at all — the same state a real interactive top-level shell leaves bg_start's nohup
    wrapper in, and the condition os.setsid() must tolerate (EPERM otherwise, Task 2)."""
    out_path = cwd / "_job_control_out.txt"
    wrapped = f"set -m\n. {HELPERS}\n{{ {script}\n}} > {out_path} 2>&1\n"
    base = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(cwd)}
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen([shell, "-c", wrapped], stdin=slave, stdout=slave, stderr=slave,
                                 cwd=cwd, env=base | env)
        os.close(slave)
        proc.wait(timeout=30)
    finally:
        os.close(master)
    return out_path.read_text() if out_path.exists() else ""


def test_bg_start_works_when_the_caller_is_a_process_group_leader(shell, tmp_path):
    cwd = _backend(tmp_path)
    out = _run_under_job_control(shell, "bg_start logs/a.log echo hi && bg_wait logs/a.log 30; echo rc=$?", cwd, BG_ENV)
    assert "EPERM" not in out and "Operation not permitted" not in out, out
    log = (cwd / "logs" / "a.log").read_text()
    assert "hi\n" in log and "RUNBOOK_EXIT=0" in log and "RUNBOOK_ELAPSED=" in log
    assert "rc=0" in out and "finished ok" in out


def test_bg_wait_returns_2_while_running_then_the_run_can_be_awaited_again(shell, tmp_path):
    cwd = _backend(tmp_path)
    out = _run(shell, "bg_start s.log sleep 3 && { bg_wait s.log 0; echo first=$?; bg_wait s.log 30; echo second=$?; }", cwd, BG_ENV)
    assert "first=2" in out.stdout and "second=0" in out.stdout
    assert "still running" in out.stderr


def test_bg_wait_reports_a_killed_run(shell, tmp_path):
    (tmp_path / "k.log").write_text("partial output\n")
    (tmp_path / "k.log.pid").write_text("999999\n")
    out = _run(shell, "bg_wait k.log 5; echo rc=$?", tmp_path, BG_ENV)
    assert "killed" in out.stderr and out.stdout.strip().endswith("rc=1")


@pytest.mark.parametrize(
    "script, env, message",
    [
        ("bg_start x.log echo hi", {}, "TARGET_HOST is unset"),
        ("bg_start x.log", BG_ENV, "usage"),
        ("bg_wait nothing.log", BG_ENV, "bg_start never ran"),
    ],
)
def test_bg_helpers_refuse(shell, tmp_path, script, env, message):
    out = _run(shell, f"{script}; echo rc=$?", _backend(tmp_path), env)
    assert message in out.stderr and out.stdout.strip().endswith("rc=1")
    assert not (tmp_path / "x.log").exists()


def test_bg_start_never_starts_a_second_run_on_the_same_log(shell, tmp_path):
    cwd = _backend(tmp_path)
    (cwd / "r.log").write_text("RUNBOOK_EXIT=0\n")
    out = _run(shell, "bg_start r.log echo again; echo rc=$?", cwd, BG_ENV)
    assert "already started" in out.stderr and out.stdout.strip() == "rc=1"
    assert (cwd / "r.log").read_text() == "RUNBOOK_EXIT=0\n"


def test_bg_wait_does_not_call_a_killed_wrapper_rolled_back_while_its_job_runs(shell, tmp_path):
    # Only the nohup wrapper dies; the job it started (the apply, here `sleep`) lives on in the
    # wrapper's process group, so bg_wait must say "still running", not "rolled back".
    cwd = _backend(tmp_path)
    script = (
        "bg_start w.log sh -c 'sleep 3; echo job-done' && sleep 1 && kill -9 \"$(cat w.log.pid)\" && sleep 0.3"
        " && { bg_wait w.log 0; echo first=$?; sleep 3; bg_wait w.log 0; echo second=$?; }"
    )
    out = _run(shell, script, cwd, BG_ENV)
    assert "first=2" in out.stdout and "second=1" in out.stdout, (out.stdout, out.stderr)
    first_err = out.stderr.split("still running", 1)[0]
    assert "rolled back" not in first_err
    assert "job-done" in (cwd / "w.log").read_text()
    assert "confirm no repair is running" in out.stderr


def test_bg_wait_refuses_rolled_back_while_a_repair_process_lives(shell, tmp_path):
    (tmp_path / "k.log").write_text("partial output\n")
    (tmp_path / "k.log.pid").write_text("999999\n")
    marker = f"runbook-guard-{tmp_path.name}"
    script = f"sh -c 'sleep 5; : {marker}' & sleep 0.3; bg_wait k.log 0; echo rc=$?; kill $! 2>/dev/null"
    out = _run(shell, script, tmp_path, BG_ENV | {"BG_GUARD_PATTERN": marker})
    assert out.stdout.strip().endswith("rc=2") and "do not rerun" in out.stderr and "rolled back" not in out.stderr
