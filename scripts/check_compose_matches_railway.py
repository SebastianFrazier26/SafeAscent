#!/usr/bin/env python3
"""Fail CI if a docker-compose service's command drifts from its Railway startCommand.

Stdlib only, so the guard job needs no dependency install: compose is read via
`docker compose config --format json` rather than parsed as YAML.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RAILWAY_CONFIGS = {
    "worker": "backend/railway-worker.toml",
    "beat": "backend/railway-beat.toml",
}
# Railway services with no docker-compose counterpart, and why. Every backend/railway-*.toml must be
# listed here or in RAILWAY_CONFIGS, so a new service cannot skip the parity check unnoticed.
NO_COMPOSE_COUNTERPART = {
    # A yearly cron against prod as ingest; compose has no scheduler. Run it locally with
    # `uv run python -m app.pipelines.catalog_cycle run`.
    "catalog-cycle": "backend/railway-catalog-cycle.toml",
}


def start_command(root: Path, service: str) -> list[str]:
    deploy = tomllib.loads((root / RAILWAY_CONFIGS[service]).read_text())["deploy"]
    return shlex.split(deploy["startCommand"])


def _argv(command: Any) -> list[str] | None:
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, list):
        return [str(part) for part in command]
    return None


def mismatches(services: dict[str, Any], root: Path) -> list[str]:
    problems = []
    for service, config in RAILWAY_CONFIGS.items():
        expected = start_command(root, service)
        actual = _argv((services.get(service) or {}).get("command"))
        if actual != expected:
            problems.append(
                f"compose service {service!r} command {actual} != {config} startCommand {expected}"
            )
    return problems


def unlisted_configs(root: Path) -> list[str]:
    known = set(RAILWAY_CONFIGS.values()) | set(NO_COMPOSE_COUNTERPART.values())
    return sorted(
        str(p.relative_to(root)) for p in (root / "backend").glob("railway-*.toml")
        if str(p.relative_to(root)) not in known
    )


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
        f"{c} is neither compared with compose nor listed in NO_COMPOSE_COUNTERPART"
        for c in unlisted_configs(REPO_ROOT)
    ]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
