import fnmatch
import io
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import scripts.write_role_url as write_role_url
from scripts.write_role_url import build_role_url, main, upsert_env_line

OWNER = "postgresql://neondb_owner:ownerpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

def test_build_role_url_swaps_credentials_and_uses_asyncpg_ssl():
    url = build_role_url(OWNER, "app", "abc123")
    assert url == "postgresql+asyncpg://app:abc123@ep-x-123.us-east-2.aws.neon.tech/neondb?ssl=verify-full"
    assert "ownerpw" not in url


def test_build_role_url_keeps_port_and_escapes_password():
    url = build_role_url("postgresql://o:p@localhost:5433/db", "migrator", "a/b+c")
    assert url == "postgresql+asyncpg://migrator:a%2Fb%2Bc@localhost:5433/db?ssl=verify-full"


def test_build_role_url_rejects_hostless_url():
    with pytest.raises(ValueError):
        build_role_url("postgresql:///db", "app", "x")


def test_upsert_env_line_replaces_and_sets_0600(tmp_path):
    env = tmp_path / ".env.app"
    env.write_text("APP_PASSWORD=x\nAPP_DATABASE_URL=old\n")
    upsert_env_line(env, "APP_DATABASE_URL", "new")
    assert env.read_text() == "APP_PASSWORD=x\nAPP_DATABASE_URL=new\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_upsert_env_line_tightens_a_world_readable_file(tmp_path):
    env = tmp_path / ".env.app"
    env.write_text("APP_PASSWORD=x\n")
    env.chmod(0o644)
    upsert_env_line(env, "APP_DATABASE_URL", "u")
    assert env.read_text() == "APP_PASSWORD=x\nAPP_DATABASE_URL=u\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == [".env.app"]


def test_upsert_env_line_creates_missing_file_0600(tmp_path):
    env = tmp_path / ".env.app"
    upsert_env_line(env, "APP_DATABASE_URL", "u")
    assert env.read_text() == "APP_DATABASE_URL=u\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


@pytest.mark.parametrize("name", [".env.app", "role.env"])
def test_upsert_env_line_temp_file_is_gitignored_0600_and_beside_target(tmp_path, monkeypatch, name):
    # A run killed mid-write leaves the temp file (holding a DB URL with a password) behind.
    seen: list[tuple[str, int]] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((str(src), stat.S_IMODE(os.stat(src).st_mode)))
        real_replace(src, dst)

    monkeypatch.setattr(write_role_url.os, "replace", spy)
    upsert_env_line(tmp_path / name, "APP_DATABASE_URL", "u")
    [(tmp, mode)] = seen
    assert Path(tmp).parent == tmp_path
    assert mode == 0o600
    assert fnmatch.fnmatch(Path(tmp).name, ".env.*")
    backend = Path(__file__).resolve().parents[1]
    if shutil.which("git") and (backend.parent / ".git").exists():
        probe = f"backend/{Path(tmp).name}"
        result = subprocess.run(
            ["git", "check-ignore", "-q", probe], cwd=backend.parent, capture_output=True, check=False
        )
        assert result.returncode == 0, f"{probe} is not gitignored"


def test_main_writes_url_without_printing_secrets(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env.app"
    env.write_text("APP_PASSWORD=s3cret\n")
    monkeypatch.setenv("OWNER_DATABASE_URL", OWNER)
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert main(["--role", "app", "--env-file", str(env)]) == 0
    assert "APP_DATABASE_URL=postgresql+asyncpg://app:s3cret@" in env.read_text()
    captured = capsys.readouterr()
    assert "s3cret" not in captured.out + captured.err
    assert "ownerpw" not in captured.out + captured.err


def test_main_rejects_unknown_role(tmp_path):
    with pytest.raises(SystemExit):
        main(["--role", "analyst", "--env-file", str(tmp_path / ".env.analyst")])


def test_main_reads_password_from_stdin(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env.migrator"
    monkeypatch.setenv("OWNER_DATABASE_URL", OWNER)
    monkeypatch.delenv("MIGRATOR_PASSWORD", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("from-stdin\n"))
    assert main(["--role", "migrator", "--env-file", str(env), "--password-stdin"]) == 0
    assert "MIGRATOR_DATABASE_URL=postgresql+asyncpg://migrator:from-stdin@" in env.read_text()
    out = capsys.readouterr()
    assert "from-stdin" not in out.out + out.err


def test_main_generates_password_into_a_0600_file_without_printing_it(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env.app"
    monkeypatch.setenv("OWNER_DATABASE_URL", OWNER)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert main(["--role", "app", "--env-file", str(env), "--generate-password"]) == 0
    lines = dict(line.split("=", 1) for line in env.read_text().splitlines())
    password = lines["APP_PASSWORD"]
    assert re.fullmatch(r"[0-9a-f]{64}", password)
    assert lines["APP_DATABASE_URL"] == build_role_url(OWNER, "app", password)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    out = capsys.readouterr()
    assert out.out == f"wrote APP_PASSWORD and APP_DATABASE_URL to {env}\n"
    assert password not in out.out + out.err
    assert "ownerpw" not in out.out + out.err


def test_main_generates_a_fresh_password_per_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_DATABASE_URL", OWNER)
    a, b = tmp_path / ".env.a", tmp_path / ".env.b"
    main(["--role", "app", "--env-file", str(a), "--generate-password"])
    main(["--role", "app", "--env-file", str(b), "--generate-password"])
    assert a.read_text().splitlines()[0] != b.read_text().splitlines()[0]


def test_main_generate_refuses_to_replace_an_existing_password(tmp_path, monkeypatch):
    env = tmp_path / ".env.migrator"
    env.write_text("MIGRATOR_PASSWORD=already-live\n")
    monkeypatch.setenv("OWNER_DATABASE_URL", OWNER)
    with pytest.raises(SystemExit, match="already has MIGRATOR_PASSWORD"):
        main(["--role", "migrator", "--env-file", str(env), "--generate-password"])
    assert env.read_text() == "MIGRATOR_PASSWORD=already-live\n"


def test_main_argument_shape(tmp_path):
    with pytest.raises(SystemExit):
        main(["--role", "app"])
    with pytest.raises(SystemExit):
        main(["--role", "app", "--scram"])
    with pytest.raises(SystemExit):
        main(["--role", "app", "--env-file", str(tmp_path / "x"), "--generate-password", "--password-stdin"])


def test_main_generate_writes_nothing_when_the_url_cannot_be_built(tmp_path, monkeypatch):
    env = tmp_path / ".env.app"
    monkeypatch.setenv("OWNER_DATABASE_URL", "postgresql:///neondb")
    with pytest.raises(ValueError):
        main(["--role", "app", "--env-file", str(env), "--generate-password"])
    assert not env.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("from_stdin", [False, True])
def test_main_refuses_a_stale_password_line_that_differs(tmp_path, monkeypatch, capsys, from_stdin):
    env = tmp_path / ".env.app"
    env.write_text("APP_PASSWORD=stale-old-password\n")
    monkeypatch.setenv("OWNER_DATABASE_URL", OWNER)
    args = ["--role", "app", "--env-file", str(env)]
    if from_stdin:
        monkeypatch.setattr("sys.stdin", io.StringIO("new-password\n"))
        args.append("--password-stdin")
    else:
        monkeypatch.setenv("APP_PASSWORD", "new-password")
    with pytest.raises(SystemExit, match="has a different APP_PASSWORD") as exc:
        main(args)
    assert "stale-old-password" not in str(exc.value) and "new-password" not in str(exc.value)
    assert env.read_text() == "APP_PASSWORD=stale-old-password\n"
    out = capsys.readouterr()
    assert "new-password" not in out.out + out.err
