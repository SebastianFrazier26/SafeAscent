"""`.env.example` must list exactly the Settings fields plus the frontend VITE_* build args."""

import re
from pathlib import Path

from app.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
FRONTEND_BUILD_ARGS = {"VITE_API_BASE_URL", "VITE_MAPBOX_TOKEN"}
KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def _documented_keys() -> list[str]:
    return [
        match.group(1)
        for line in ENV_EXAMPLE.read_text().splitlines()
        if (match := KEY_LINE.match(line))
    ]


def test_env_example_has_no_duplicate_keys():
    keys = _documented_keys()
    assert len(keys) == len(set(keys))


def test_env_example_matches_settings():
    documented = set(_documented_keys())
    expected = set(Settings.model_fields) | FRONTEND_BUILD_ARGS
    assert sorted(documented - expected) == [], "in .env.example but not in Settings"
    assert (
        sorted(expected - documented) == []
    ), "in Settings but missing from .env.example"
