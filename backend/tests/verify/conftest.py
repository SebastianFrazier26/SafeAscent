"""A skipped acceptance cell must never read as a pass: VERIFY always lists every skipped cell
with its reason, whatever -r flags the caller passed (owner ruling 2026-11-23)."""

from __future__ import annotations

import pytest


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    skipped = [r for r in terminalreporter.stats.get("skipped", []) if "tests/verify/" in r.nodeid]
    if not skipped:
        return
    terminalreporter.section("SKIPPED acceptance cells", sep="!", red=True, bold=True)
    for report in skipped:
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        terminalreporter.write_line(f"SKIPPED {report.nodeid}: {reason}", red=True, bold=True)
