"""Acceptance checks for plan 1, run by the owner/agent against a Neon branch or prod as
the read-only analyst role: VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_accidents_raw_matches_live_row_count():
    [row] = fetch("SELECT (SELECT count(*) FROM internal.accidents_raw) = (SELECT count(*) FROM accidents) AS same")
    assert row["same"]


def test_no_future_tick_is_unflagged():
    [row] = fetch(
        "SELECT count(*) AS n FROM mp_ticks WHERE quarantine_reason IS NULL "
        "AND tick_date > LEAST((now() AT TIME ZONE 'UTC')::date, created_at::date + 1)"
    )
    assert row["n"] == 0


def test_r8_flags_are_known_reasons_under_the_current_rule_version():
    rows = fetch(
        "SELECT coalesce(quarantine_reason, 'clean') AS r, count(*) AS n, "
        "bool_and(quarantine_rule_version = 'r8-v1') AS versioned FROM mp_ticks GROUP BY 1"
    )
    assert {r["r"] for r in rows} <= {"clean", "future", "orphan_route", "pre_1970"}
    assert all(r["versioned"] for r in rows)
    assert sum(r["n"] for r in rows if r["r"] != "clean") > 0
