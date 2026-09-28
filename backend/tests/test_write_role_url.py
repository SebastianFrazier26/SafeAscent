import base64
import hashlib
import hmac
import io
import stat

import pytest

from scripts.write_role_url import build_role_url, main, scram_verifier, upsert_env_line

OWNER = "postgresql://neondb_owner:ownerpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# RFC 7677 section 3 example exchange (user "user", password "pencil").
RFC_SALT = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
RFC_AUTH_MESSAGE = (
    b"n=user,r=rOprNGfwEbeRWgbNEkqO,"
    b"r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
    b"c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
)
RFC_CLIENT_PROOF = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
RFC_SERVER_SIGNATURE = base64.b64decode("6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=")


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


def _verifier_parts(verifier: str) -> tuple[int, bytes, bytes, bytes]:
    mechanism, rest = verifier.split("$", 1)
    assert mechanism == "SCRAM-SHA-256"
    iter_salt, keys = rest.split("$")
    iterations, salt = iter_salt.split(":")
    stored_key, server_key = keys.split(":")
    return int(iterations), base64.b64decode(salt), base64.b64decode(stored_key), base64.b64decode(server_key)


def test_scram_verifier_matches_rfc7677_exchange():
    verifier = scram_verifier("pencil", salt=RFC_SALT, iterations=4096)
    iterations, salt, stored_key, server_key = _verifier_parts(verifier)
    assert (iterations, salt) == (4096, RFC_SALT)
    assert hmac.new(server_key, RFC_AUTH_MESSAGE, "sha256").digest() == RFC_SERVER_SIGNATURE
    client_signature = hmac.new(stored_key, RFC_AUTH_MESSAGE, "sha256").digest()
    client_key = bytes(a ^ b for a, b in zip(RFC_CLIENT_PROOF, client_signature))
    assert hashlib.sha256(client_key).digest() == stored_key


def test_scram_verifier_uses_a_fresh_16_byte_salt():
    a, b = scram_verifier("pw"), scram_verifier("pw")
    assert a != b
    assert len(_verifier_parts(a)[1]) == 16


def test_scram_verifier_rejects_non_ascii():
    with pytest.raises(ValueError):
        scram_verifier("pässword")


def test_main_scram_reads_env_and_prints_only_the_verifier(monkeypatch, capsys):
    monkeypatch.setenv("APP_PASSWORD", "s3cret-plain")
    assert main(["--role", "app", "--scram"]) == 0
    out = capsys.readouterr()
    assert "s3cret-plain" not in out.out + out.err
    verifier = out.out.strip()
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert len(out.out.splitlines()) == 1


def test_main_scram_reads_stdin(monkeypatch, capsys):
    monkeypatch.delenv("MIGRATOR_PASSWORD", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("from-stdin\n"))
    assert main(["--role", "migrator", "--scram", "--password-stdin"]) == 0
    out = capsys.readouterr()
    assert "from-stdin" not in out.out + out.err
    assert out.out.startswith("SCRAM-SHA-256$")


def test_main_requires_exactly_one_action(tmp_path):
    with pytest.raises(SystemExit):
        main(["--role", "app"])
    with pytest.raises(SystemExit):
        main(["--role", "app", "--scram", "--env-file", str(tmp_path / "x")])
