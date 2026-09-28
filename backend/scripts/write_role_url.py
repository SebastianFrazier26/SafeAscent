"""Write <ROLE>_DATABASE_URL into a gitignored env file without echoing any secret."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote, urlsplit


def build_role_url(owner_url: str, role: str, password: str) -> str:
    parts = urlsplit(owner_url)
    if not parts.hostname:
        raise ValueError("owner URL has no host")
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    # asyncpg spells libpq's sslmode as ssl and rejects channel_binding.
    return f"postgresql+asyncpg://{quote(role, safe='')}:{quote(password, safe='')}@{host}{parts.path}?ssl=require"


def upsert_env_line(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    prefix = args.role.upper()
    owner_url = os.environ["OWNER_DATABASE_URL"]
    password = os.environ[f"{prefix}_PASSWORD"]
    upsert_env_line(args.env_file, f"{prefix}_DATABASE_URL", build_role_url(owner_url, args.role, password))
    print(f"wrote {prefix}_DATABASE_URL to {args.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
