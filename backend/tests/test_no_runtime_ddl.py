"""Schema changes belong in Alembic. The app role has no CREATE on public, and even
CREATE TABLE IF NOT EXISTS checks that privilege before it checks existence."""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
DDL = re.compile(
    r"\b(CREATE\s+(TABLE|INDEX|EXTENSION|SCHEMA|VIEW)|ALTER\s+TABLE|DROP\s+(TABLE|INDEX|VIEW))\b",
    re.IGNORECASE,
)


def test_app_code_issues_no_ddl():
    offenders = [
        f"{path.relative_to(APP_DIR.parent)}:{number}"
        for path in sorted(APP_DIR.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if DDL.search(line)
    ]
    assert offenders == []
