"""Write <ROLE>_DATABASE_URL (and, with --generate-password, <ROLE>_PASSWORD) into a gitignored env file.

Never prints or logs a password.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

# trainer is NOLOGIN until Phase 3 (D13), which adds it here along with its grants.
ROLES = ("migrator", "app", "ingest")


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


def upsert_env_lines(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.split("=", 1)[0] not in values]
    lines.extend(f"{key}={value}" for key, value in values.items())
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


def upsert_env_line(path: Path, key: str, value: str) -> None:
    upsert_env_lines(path, {key: value})


def env_file_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return None


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
    parser.add_argument("--env-file", required=True, type=Path, help="write <ROLE>_DATABASE_URL into this file")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin instead of <ROLE>_PASSWORD"
    )
    source.add_argument(
        "--generate-password",
        action="store_true",
        help="generate a random password and write it to the env file as <ROLE>_PASSWORD",
    )
    args = parser.parse_args(argv)
    prefix = args.role.upper()
    password_key, url_key = f"{prefix}_PASSWORD", f"{prefix}_DATABASE_URL"
    owner_url = os.environ["OWNER_DATABASE_URL"]
    existing = env_file_value(args.env_file, password_key)
    if args.generate_password:
        # Refusing to overwrite keeps a rerun from silently rotating a password the
        # server may already hold; delete the file to start over.
        if existing is not None:
            raise SystemExit(f"{args.env_file} already has {password_key}; delete the file to generate a new one")
        password = secrets.token_hex(32)
        values = {password_key: password, url_key: build_role_url(owner_url, args.role, password)}
    else:
        password = _password(prefix, args.password_stdin)
        # A URL built from one password beside a stale line holding another would leave
        # create_roles.sql and the services disagreeing about the role's password.
        if existing is not None and existing != password:
            raise SystemExit(f"{args.env_file} has a different {password_key}; remove that line or the file first")
        values = {url_key: build_role_url(owner_url, args.role, password)}
    upsert_env_lines(args.env_file, values)
    print(f"wrote {' and '.join(values)} to {args.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
