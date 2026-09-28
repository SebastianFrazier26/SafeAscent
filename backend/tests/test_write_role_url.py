import stat

import pytest

from scripts.write_role_url import build_role_url, main, upsert_env_line

OWNER = "postgresql://neondb_owner:ownerpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


def test_build_role_url_swaps_credentials_and_uses_asyncpg_ssl():
    url = build_role_url(OWNER, "app", "abc123")
    assert url == "postgresql+asyncpg://app:abc123@ep-x-123.us-east-2.aws.neon.tech/neondb?ssl=require"
    assert "ownerpw" not in url


def test_build_role_url_keeps_port_and_escapes_password():
    url = build_role_url("postgresql://o:p@localhost:5433/db", "migrator", "a/b+c")
    assert url == "postgresql+asyncpg://migrator:a%2Fb%2Bc@localhost:5433/db?ssl=require"


def test_build_role_url_rejects_hostless_url():
    with pytest.raises(ValueError):
        build_role_url("postgresql:///db", "app", "x")


def test_upsert_env_line_replaces_and_sets_0600(tmp_path):
    env = tmp_path / ".env.app"
    env.write_text("APP_PASSWORD=x\nAPP_DATABASE_URL=old\n")
    upsert_env_line(env, "APP_DATABASE_URL", "new")
    assert env.read_text() == "APP_PASSWORD=x\nAPP_DATABASE_URL=new\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


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
