"""db/checks/p2a1_precheck.sql, the Task 12 Step 2 read, must count AAC rows, id schemes and
severities exactly as the Python rules do, whitespace included (final re-review 2a-1 m3)."""

import asyncio
from collections import Counter
from pathlib import Path

import asyncpg

from app.data.repair.dates import source_scheme
from app.data.repair.severity import normalized_severity
from app.data.repair.sources import source_family
from tests.pgtest import migrated_db, pg_url, requires_pg

SQL = Path(__file__).resolve().parents[1] / "db" / "checks" / "p2a1_precheck.sql"
ROWS = [
    ("AAC", "12", "Serious"),
    ("\taac", "88a", "\tfatal\n"),
    (" AAC", "88T ", "minor"),
    ("aac\n", "20190001", ""),
    ("aac", " 20190002 ", None),
    ("aac", None, "fatal"),
    ("aac", "x", "serious"),
    ("aac", "2700", "minor"),
    ("aac", "\t55\n", "near_miss"),
    ("aac", "2019-001", "fatal"),
    ("aac", "120190001", "fatal"),
    ("nps", "9", "fatal"),
    (" avalanche", "a-1", "near_miss"),
]


def _literal(value: str | None) -> str:
    return "NULL" if value is None else "E'" + value.replace("\\", "\\\\").replace("'", "''").replace("\t", "\\t").replace("\n", "\\n") + "'"


SEED = "\n".join(
    [
        "INSERT INTO accidents (accident_id, source, source_id, injury_severity) VALUES "
        + ", ".join(f"({i}, {_literal(s)}, {_literal(sid)}, {_literal(sev)})" for i, (s, sid, sev) in enumerate(ROWS, 1))
        + ";",
        "INSERT INTO internal.accidents_raw (accident_id, source, source_id) SELECT accident_id, source, source_id FROM accidents;",
    ]
)


def _statements() -> list[str]:
    body = "\n".join(line for line in SQL.read_text(encoding="utf-8").splitlines() if not line.startswith("--"))
    return [s.strip() for s in body.split(";\n") if s.strip()]


@requires_pg
def test_precheck_sql_matches_the_python_rules():
    async def run(url: str) -> list[asyncpg.Record]:
        conn = await asyncpg.connect(url)
        try:
            return [row for sql in _statements() for row in await conn.fetch(sql)]
        finally:
            await conn.close()

    with migrated_db(seed_sql=SEED) as name:
        rows = asyncio.run(run(pg_url(name)))

    aac = [r for r in ROWS if source_family(r[0]) == "aac"]
    [total] = [tuple(r)[1:] for r in rows if r[0] == "aac_total"]
    assert total == (len(aac), sum(1 for r in aac if r[1] is not None))
    schemes = {r[1]: r[2] for r in rows if r[0] == "scheme"}
    assert schemes == dict(Counter(source_scheme(r[1]) for r in aac))
    [over] = [r[3] for r in rows if r[0] == "scheme" and r[1] == "sequence"]
    assert over == 2  # "2700" and the 9-digit "120190001"
    severities = Counter()
    for r in rows:
        if r[0] == "severity":
            severities[r[2]] += r[3]
    assert severities == Counter(normalized_severity(r[2]) for r in ROWS)
    [live_raw] = [tuple(r)[1:] for r in rows if r[0] == "live_raw"]
    assert live_raw == (len(ROWS), len(ROWS))
