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


def main() -> int:
    rendered = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    problems = mismatches(json.loads(rendered)["services"], REPO_ROOT)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
