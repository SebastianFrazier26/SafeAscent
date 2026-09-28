"""Write <ROLE>_DATABASE_URL into a gitignored env file, or print a role's SCRAM verifier.

Neither mode prints or logs a password.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

ROLES = ("migrator", "app")


def build_role_url(owner_url: str, role: str, password: str) -> str:
    parts = urlsplit(owner_url)
    if not parts.hostname:
        raise ValueError("owner URL has no host")
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    # asyncpg spells libpq's sslmode as ssl and rejects channel_binding. verify-full is a
    # marker here, not the whole story: asyncpg's own verify-full needs a root cert file
    # (no sslrootcert=system fallback), so app.db.ssl.connect_args_for builds an actual
    # SSLContext (certifi's bundle) and passes it as connect_args, which SQLAlchemy merges
    # in ahead of this query param.
    return f"postgresql+asyncpg://{quote(role, safe='')}:{quote(password, safe='')}@{host}{parts.path}?ssl=verify-full"


def upsert_env_line(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    # mkstemp creates the file 0600, so the secret is never readable at a looser mode,
    # even if the existing file was 0644; os.replace swaps it in atomically. The fixed
    # ".env.tmp." prefix keeps a leftover from a killed run under the `.env.*` ignore rule.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def scram_verifier(password: str, *, salt: bytes | None = None, iterations: int = 4096) -> str:
    """Return the SCRAM-SHA-256 secret Postgres stores in pg_authid.rolpassword.

    Sending this instead of the plaintext keeps the password out of server logs and
    pg_stat_statements.
    """
    # SASLprep is skipped; it is the identity only for ASCII, so anything else would
    # produce a verifier the server never matches.
    if not password.isascii():
        raise ValueError("password must be ASCII")
    salt = secrets.token_bytes(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def _password(prefix: str, from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            raise SystemExit("no password on stdin")
        return password
    return os.environ[f"{prefix}_PASSWORD"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=ROLES)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--env-file", type=Path, help="write <ROLE>_DATABASE_URL into this file")
    action.add_argument("--scram", action="store_true", help="print the role's SCRAM-SHA-256 verifier")
    parser.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin instead of <ROLE>_PASSWORD"
    )
    args = parser.parse_args(argv)
    prefix = args.role.upper()
    password = _password(prefix, args.password_stdin)
    if args.scram:
        print(scram_verifier(password))
        return 0
    owner_url = os.environ["OWNER_DATABASE_URL"]
    upsert_env_line(args.env_file, f"{prefix}_DATABASE_URL", build_role_url(owner_url, args.role, password))
    print(f"wrote {prefix}_DATABASE_URL to {args.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
