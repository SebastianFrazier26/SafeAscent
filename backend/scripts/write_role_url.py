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


def env_file_has_key(path: Path, key: str) -> bool:
    return path.exists() and any(
        line.startswith(f"{key}=") for line in path.read_text(encoding="utf-8").splitlines()
    )


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
    owner_url = os.environ["OWNER_DATABASE_URL"]
    if args.generate_password:
        # Refusing to overwrite keeps a rerun from silently rotating a password the
        # server may already hold; delete the file to start over.
        if env_file_has_key(args.env_file, f"{prefix}_PASSWORD"):
            raise SystemExit(f"{args.env_file} already has {prefix}_PASSWORD; delete the file to generate a new one")
        password = secrets.token_hex(32)
        upsert_env_line(args.env_file, f"{prefix}_PASSWORD", password)
    else:
        password = _password(prefix, args.password_stdin)
    upsert_env_line(args.env_file, f"{prefix}_DATABASE_URL", build_role_url(owner_url, args.role, password))
    written = f"{prefix}_PASSWORD and {prefix}_DATABASE_URL" if args.generate_password else f"{prefix}_DATABASE_URL"
    print(f"wrote {written} to {args.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
