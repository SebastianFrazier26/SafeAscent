"""db/checks/p2a2_precheck.sql, the Task 13 Step 3 read that 12e diffs prod against: it must
run on the migrated schema and list raw state counts and the Denali/McKinley GNIS rows
(final review 2a-2 minor: 2a-1's precheck had a test, this one had none)."""

import asyncio
from pathlib import Path

import asyncpg

from tests.pgtest import migrated_db, pg_url, requires_pg

SQL = Path(__file__).resolve().parents[1] / "db" / "checks" / "p2a2_precheck.sql"
SEED = """
INSERT INTO accidents (accident_id, source, state) VALUES
  (1, 'aac', 'Alaska'), (2, 'aac', 'Alaska'), (3, 'nps', 'Wash.'), (4, 'aac', NULL);
INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) VALUES
  (1414314, 'Mount McKinley', 'mckinley', 'AK', 63.07, -151.0),
  (2, 'Denali', 'denali', 'AK', 63.0, -151.0),
  (3, 'Longs Peak', 'longs', 'CO', 40.25, -105.6);
"""


def _statements() -> list[str]:
    body = "\n".join(line for line in SQL.read_text(encoding="utf-8").splitlines() if not line.startswith("--"))
    return [s.strip() for s in body.split(";\n") if s.strip()]


@requires_pg
def test_precheck_sql_lists_states_and_the_denali_rows():
    async def run(url: str) -> list[tuple[object, ...]]:
        conn = await asyncpg.connect(url)
        try:
            return [tuple(row) for sql in _statements() for row in await conn.fetch(sql)]
        finally:
            await conn.close()

    with migrated_db(seed_sql=SEED) as name:
        rows = asyncio.run(run(pg_url(name)))

    assert [r for r in rows if r[0] == "state"] == [("state", "Alaska", 2), ("state", "Wash.", 1), ("state", None, 1)]
    assert [r for r in rows if r[0] == "gnis_denali"] == [
        ("gnis_denali", 2, "Denali", "AK"),
        ("gnis_denali", 1414314, "Mount McKinley", "AK"),
    ]
