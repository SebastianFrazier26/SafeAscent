#!/usr/bin/env python3
"""Fail CI if a docker-compose service's command drifts from its Railway start command, or if a
Config as Code file comes back.

`.railway/railway.ts` (IaC, since 2026-11-25) is the only Railway definition. A `railway.toml` or
`railway.json` at a service root or the repo root is still auto-read by Railway's builder and
overrides IaC (how catalog-cycle built the API image), so none may exist; `railway-*.toml` files
are refused too, so a second, unread copy of the settings cannot drift.

Stdlib only, so the guard job needs no dependency install: compose is read via
`docker compose config --format json` rather than parsed as YAML, and railway.ts by a narrow
regex rather than a TypeScript toolchain.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
IAC_FILE = ".railway/railway.ts"
# compose service -> Railway service in railway.ts. backend/frontend take their Dockerfile's
# default command on both sides; catalog-cycle has no compose counterpart (a yearly cron
# against prod as ingest; run it locally with `uv run python -m app.pipelines.catalog_cycle run`).
COMPOSE_TO_IAC = {
    "worker": "celery-worker",
    "beat": "beat",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "htmlcov"}


def iac_service_blocks(source: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for block in re.split(r'\bservice\(\s*(?=")', source)[1:]:
        name = re.match(r'"([^"]+)"', block)
        if name:
            blocks[name.group(1)] = block
    return blocks


def iac_start_command(block: str) -> list[str] | None:
    start = re.search(r'\bstart:\s*("(?:[^"\\]|\\.)*")', block)
    return shlex.split(json.loads(start.group(1))) if start else None


def iac_replicas(block: str) -> int | None:
    replicas = re.search(r"\breplicas:\s*(\{[^}]*\}|\d+)", block)
    if not replicas:
        return None
    if replicas.group(1).isdigit():
        return int(replicas.group(1))
    return sum(int(n) for n in re.findall(r":\s*(\d+)", replicas.group(1)))


def iac_services(root: Path) -> dict[str, str]:
    return iac_service_blocks((root / IAC_FILE).read_text())


def start_command(root: Path, compose_service: str) -> list[str] | None:
    block = iac_services(root).get(COMPOSE_TO_IAC[compose_service])
    return iac_start_command(block) if block is not None else None


def _argv(command: Any) -> list[str] | None:
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, list):
        return [str(part) for part in command]
    return None


def mismatches(services: dict[str, Any], root: Path) -> list[str]:
    if not (root / IAC_FILE).is_file():
        return [f"{IAC_FILE} is missing"]
    problems = []
    for service, railway_service in COMPOSE_TO_IAC.items():
        expected = start_command(root, service)
        actual = _argv((services.get(service) or {}).get("command"))
        if expected is None or actual != expected:
            problems.append(
                f"compose service {service!r} command {actual} != {IAC_FILE} "
                f"service {railway_service!r} start {expected}"
            )
    return problems


def config_as_code_files(root: Path) -> list[str]:
    found = []
    for path in root.rglob("railway*"):
        if SKIP_DIRS.intersection(path.relative_to(root).parts) or not path.is_file():
            continue
        if path.name in {"railway.toml", "railway.json"} or re.fullmatch(r"railway-.+\.toml", path.name):
            found.append(str(path.relative_to(root)))
    return sorted(found)


def main() -> int:
    rendered = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    problems = mismatches(json.loads(rendered)["services"], REPO_ROOT)
    problems += [
        f"{c}: Railway settings live in {IAC_FILE}; a railway.toml/json here is auto-read and overrides it"
        for c in config_as_code_files(REPO_ROOT)
    ]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
