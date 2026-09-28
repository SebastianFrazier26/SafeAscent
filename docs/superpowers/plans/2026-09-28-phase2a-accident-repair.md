# Phase 2a Accident Repair (PRs 2a-1, 2a-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every accident row traceable and trustworthy for Phase 3: repaired AAC years and date precision (R1, R2), severity scale (R9), misgeocode fixes against USGS GNIS (R3a, R3), versioned activity classes (R4), deduplication with owner review only for uncertain pairs (R5), structured experience facts (R12), and the `accidents_clean` view Phase 3 trains on.

**Architecture:** Pure, typed rule modules under `backend/app/data/repair/` compute proposed changes from rows; one audited writer (`framework.apply_changes`) applies them idempotently, writing an `internal.accident_revisions` row per changed field keyed `(accident_id, field, rule_version)`. A CLI (`python -m app.data.repair <step>`) dry-runs by default and applies with `--apply`, connecting as `ingest`. Owner review is by CSV export/import in gitignored `data/review/`. Acceptance checks are `-m db` tests run as `analyst` against a Neon branch.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, numpy (already a dependency), stdlib `csv`/`re`/`statistics`; Alembic; pytest.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Phase 2a (R1–R5, R9, R12, `accidents_clean`), with owner decisions in `2026-09-28-phase2a-foundations.md` "Decisions needed" (D10 applies here). Phase 3 contract: P3 Definitions (`date_precision`, `geocode_precision`, `excluded_reason`).

**Prerequisite:** `2026-09-28-phase2a-foundations.md` merged and its runbook Tasks 8–9 done (roles, `0004`, backups).

**Two PRs:** PR 2a-1 = Tasks 1–4 + runbook Task 12 (`feat/p2a-repair-dates`); PR 2a-2 = Tasks 5–11 + runbook Task 13 (`feat/p2a-repair-geo-dedupe`), branched from `main` after 2a-1 merges.

## Global Constraints

Everything in the foundations plan's Global Constraints applies. In particular:
- No DDL under `backend/app/`; schema changes only in revisions `0005`, `0006`; forward-only with refusing downgrades.
- Every repair is idempotent per `(accident_id, field, rule_version)`; a rerun with the same rule version changes nothing. Rows are never deleted; excluded rows stay with `excluded_reason`.
- Accident narratives (`description`) are read internally by R4/R12 only; they never go into logs, run reports, fixtures, docs, commits, or review CSVs. Golden-set files hold `accident_id` + labels only.
- Nulling coordinates must also null `coordinates` (the `accidents_coords_trigger` only fills it when both are non-null).
- `accidents.date` changes only where a rule dates a row confidently (D10); unverified years keep their date and get `excluded_reason = 'year_unverified'`.
- No LLM anywhere in the pipeline. Owner review is only for the bands the spec names.
- Review CSVs go to `data/review/` (gitignored by Task 1); nothing under it is committed.

## Decisions this plan makes where the spec is silent (owner may overrule at review)

1. **R1 edge rows.** A suspect row needs ≥5 trusted neighbours on *each* side by `source_id`; otherwise it is `year_unverified`. Without this, a genuine 2019+ record at the top of the id range would be pulled back to the last trusted year.
2. **R1 median** is `statistics.median_low` (an actual neighbour year, never a .5).
3. **R3a ambiguous names** null coordinates only when the row's current point is >50 km from *every* candidate summit; a row already near one of them is left alone.
4. **R3 default precision** for rows no rule touches: avalanche-source rows `exact` (reported incident coordinates), AAC rows with US coordinates `area` (geocoded from place text), NPS `park_centroid`, route-linked `crag`.
5. **R3 `region_fallback` and `unknown` rows get their coordinates nulled** (the point is a known-bad fallback); the verified state stays in `state`. Spec leaves coordinates for `region_fallback` unstated.
6. **R4 unmapped activity values stop the run.** The classifier returns `None` for any activity string outside its vocabularies, and `--apply` refuses while any row is unmapped; the fix is a reviewed code change to the vocabulary, not a default.
7. **R5 score weights:** date 0.30, distance 0.25, accident type 0.15, severity 0.15, place-name Jaro-Winkler 0.15; an unknown component scores 0.5. R5's rule version embeds a hash of the decision set, so new owner decisions produce new revision rows instead of being skipped as "already recorded".
8. **R12 conflicting mentions** (two stated levels, two different year counts) resolve to `unknown`/NULL: precision over recall.
9. **`accidents_clean` omits narrative columns** (`description`, `tags`, `age_range`) and adds a `accidents_clean_daily` view (`date_precision = 'day'`) as the CM training input the spec's R2 CI check targets.

## Review Focus

1. **A suspect AAC row at the very end of the `source_id` range** (no trusted rows after it) — expect `year_unverified`, date untouched (Task 3 `test_row_past_last_trusted_id_is_unverified`).
2. **An R3 run that nulls a location** — expect `coordinates` NULL too, not a stale geography point (Task 1 `test_nulling_location_also_nulls_coordinates`).
3. **Re-importing the duplicate review CSV with one decision flipped** — expect new revision rows and a changed group, not a silent skip (Task 9 `test_new_owner_decision_produces_a_new_rule_version`).
4. **"inexperienced" and "unguided" in a narrative** — expect `novice` and `unguided`, never `experienced`/`guided` (Task 10 `test_negated_forms_do_not_match_the_positive_class`).
5. **An activity value no rule knows** (e.g. `paragliding`) — expect the apply to refuse and name the value (Task 8 `test_unmapped_activity_blocks_apply`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/app/data/__init__.py`, `backend/app/data/repair/__init__.py` | Create | Packages. |
| `backend/app/data/repair/framework.py` | Create | `Change`, `apply_changes`, `fetch_accidents`, `run_step`. |
| `backend/app/data/repair/sources.py` | Create | `source_family()`; refuses unknown sources. |
| `backend/app/data/repair/review.py` | Create | Seeded sample export, decision import (CSV). |
| `backend/app/data/repair/dates.py` | Create | R1 year repair, R2 date precision. |
| `backend/app/data/repair/severity.py` | Create | R9 severity scale + hand-check agreement. |
| `backend/app/pipelines/textsim.py` | Create | In-house Jaro-Winkler, `place_key` (reused by plan 4's matcher). |
| `backend/app/pipelines/geo.py` | Create | `haversine_km`, `haversine_km_many`. |
| `backend/app/pipelines/gnis.py` | Create | GNIS DomesticNames summit parser + loader. |
| `backend/app/data/repair/geocode.py` | Create | R3a, R3. |
| `backend/app/data/repair/activity.py` | Create | R4. |
| `backend/app/data/repair/dedupe.py` | Create | R5 scoring, bands, groups, canonical choice. |
| `backend/app/data/repair/experience.py` | Create | R12. |
| `backend/app/data/repair/__main__.py` | Create | CLI orchestrator. |
| `backend/alembic/versions/0005_gnis_and_duplicate_decisions.py` | Create | `gnis_summits`, `internal.duplicate_decisions`. |
| `backend/alembic/versions/0006_accidents_clean.py` | Create | `accidents_clean`, `accidents_clean_daily` views. |
| `backend/app/models/reference.py` | Create | `GnisSummit`, `DuplicateDecision` models. |
| `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql` | Modify | Repair grants and expected writes. |
| `data/golden/README.md`, `data/golden/activity_v1.csv`, `data/golden/experience_v1.csv` | Create (runbook fills rows) | Golden labels, ids only. |
| `.gitignore` | Modify | `data/review/`. |
| `backend/tests/test_repair_*.py`, `test_textsim.py`, `test_geo.py`, `test_gnis.py`, `test_migration_0005_0006.py`, `test_migration_0006.py` | Create | Tests. |
| `backend/tests/verify/test_phase2a_repair.py` | Create | `-m db` acceptance cells. |
| `backend/pyproject.toml`, `CHANGELOG.md`, `data/DATABASE_STRUCTURE.md` | Modify | mypy allowlist, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–10 | `framework.Change`, `apply_changes`, `fetch_accidents`, `run_step` | Task 1 first; frozen signatures. |
| 1 | 3, 7, 8, 9 | `sources.source_family` | Created in Task 1. |
| 1 | 4, 8, 10 | `review.export_sample`, `read_decisions` | Created in Task 1. |
| 2 | 11 | `app/data/repair/__main__.py` | Task 2 creates the CLI with `r2r1`; Tasks 4, 7–10 each add their subcommand in their own commit. |
| 5 | 6, 7, 9 and plan 4 | `textsim.jaro_winkler`, `place_key`; `geo.haversine_km` | Frozen in Task 5; plan 4 imports them. |
| 6 | 7, 13 | `gnis_summits` table (0005) | Task 6 owns 0005; Task 7 reads it. |
| 6, 9 | each other | revision `0005` also holds `internal.duplicate_decisions` | Task 6 writes the whole revision including `duplicate_decisions`; Task 9 only uses it. |
| 4, 6, 9, 11 | each other and plan 1 | `grants_phase2.sql`, `verify_roles_phase2.sql` | Append-only; serial commits. |
| 7 | 9 | R5 must run after R3 | `dedupe` CLI refuses unless `r3-v1` revisions exist. |
| 1–11 | each other | `backend/pyproject.toml` mypy list | Serial appends. |
| 11 | plan 3 (R11) | `accidents_clean` definition | Plan 3's loaded rows set the same columns so they flow into the view. |

---

# PR 2a-1 — `feat/p2a-repair-dates`

### Task 1: Repair framework, source families, review CSV helpers

**Files:**
- Create: `backend/app/data/__init__.py`, `backend/app/data/repair/__init__.py`, `backend/app/data/repair/framework.py`, `backend/app/data/repair/sources.py`, `backend/app/data/repair/review.py`, `backend/tests/test_repair_framework.py`, `backend/tests/test_repair_review.py`
- Modify: `.gitignore`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Consumes: `ingest_log.start_run/finish_run`, `ValidationReport` (plan 1).
- Produces:
  - `Value = str | int | float | bool | date | tuple[float, float] | None`
  - `@dataclass(frozen=True) Change(accident_id: int, field: str, new_value: Value, method: str)`; `LOCATION = "latlon"` (new_value `tuple[lat, lon] | None`)
  - `@dataclass ApplyResult(applied: int = 0, unchanged: int = 0, already_recorded: int = 0)`
  - `async apply_changes(conn, changes: Sequence[Change], *, rule_version: str, run_id: uuid.UUID) -> ApplyResult`
  - `async fetch_accidents(conn, columns: Sequence[str]) -> list[dict[str, object]]` (always includes `accident_id`; columns from `READABLE_COLUMNS`)
  - `async run_step(conn, *, step: str, rule_version: str, changes: Sequence[Change], report: ValidationReport, apply: bool) -> dict[str, object]`
  - `sources.SourceFamily = Literal["aac", "avalanche", "nps"]`; `source_family(source: str | None) -> SourceFamily` (raises `ValueError` on anything else)
  - `review.export_sample(rows, *, n: int, seed: int, path: Path, columns: Sequence[str], decision_column: str) -> int`; `review.read_decisions(path: Path, *, key: str, decision_column: str, allowed: frozenset[str]) -> dict[int, str]`

- [ ] **Step 1: Failing tests**

`backend/tests/test_repair_framework.py`:

```python
import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.framework import LOCATION, Change, apply_changes, fetch_accidents
from app.data.repair.sources import source_family
from tests.pgtest import migrated_db, requires_pg, sa_url

SEED = """
INSERT INTO accidents (accident_id, source, date, latitude, longitude) VALUES
  (1, 'AAC', '2023-05-02', 40.0, -105.0),
  (2, 'NPS', '2010-07-01', 36.5, -118.3);
"""


@pytest.mark.parametrize(
    "raw,family",
    [("AAC", "aac"), ("aac_anac", "aac"), ("CAIC", "avalanche"), ("Avalanche.org", "avalanche"), ("NPS", "nps")],
)
def test_source_families(raw, family):
    assert source_family(raw) == family


def test_unknown_source_is_refused():
    with pytest.raises(ValueError, match="unmapped source"):
        source_family("Somewhere Else")


def test_unrepairable_field_is_refused():
    async def go() -> None:
        await apply_changes(None, [Change(1, "description", "x", "t")], rule_version="v", run_id=uuid.uuid4())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="not repairable"):
        asyncio.run(go())


@requires_pg
def test_apply_is_audited_and_idempotent_per_rule_version():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            changes = [
                Change(1, "date", date(2003, 5, 2), "r1"),
                Change(1, "date_precision", "day", "r1"),
                Change(2, "date_precision", "day", "r2"),
            ]
            async with engine.begin() as conn:
                first = await apply_changes(conn, changes, rule_version="r1-v1", run_id=uuid.uuid4())
            async with engine.begin() as conn:
                second = await apply_changes(conn, changes, rule_version="r1-v1", run_id=uuid.uuid4())
            assert (first.applied, first.unchanged, first.already_recorded) == (3, 0, 0)
            assert (second.applied, second.already_recorded) == (0, 3)
            async with engine.connect() as conn:
                rows = await fetch_accidents(conn, ["date", "date_precision"])
                assert rows[0] == {"accident_id": 1, "date": date(2003, 5, 2), "date_precision": "day"}
                rev = (await conn.execute(text(
                    "SELECT old_value, new_value FROM internal.accident_revisions WHERE accident_id = 1 AND field = 'date'"
                ))).one()
                assert tuple(rev) == ("2023-05-02", "2003-05-02")
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_nulling_location_also_nulls_coordinates():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await apply_changes(conn, [Change(1, LOCATION, None, "r3")], rule_version="r3-v1", run_id=uuid.uuid4())
                await apply_changes(conn, [Change(2, LOCATION, (36.6, -118.29), "r3")], rule_version="r3-v1", run_id=uuid.uuid4())
            async with engine.connect() as conn:
                row1 = (await conn.execute(text(
                    "SELECT latitude, longitude, coordinates IS NULL FROM accidents WHERE accident_id = 1"))).one()
                row2 = (await conn.execute(text(
                    "SELECT round(ST_Y(coordinates::geometry)::numeric, 2) FROM accidents WHERE accident_id = 2"))).scalar_one()
            assert tuple(row1) == (None, None, True)
            assert float(row2) == 36.6
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))
```

`backend/tests/test_repair_review.py`:

```python
import pytest

from app.data.repair.review import export_sample, read_decisions


def test_export_is_seeded_and_adds_a_blank_decision_column(tmp_path):
    rows = [{"accident_id": i, "year": 2000 + i} for i in range(100)]
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    assert export_sample(rows, n=10, seed=7, path=a, columns=["accident_id", "year"], decision_column="agree") == 10
    export_sample(rows, n=10, seed=7, path=b, columns=["accident_id", "year"], decision_column="agree")
    assert a.read_text() == b.read_text()
    assert a.read_text().splitlines()[0] == "accident_id,year,agree"


def test_read_decisions_rejects_blank_and_unknown_values(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("accident_id,agree\n1,yes\n2,\n")
    with pytest.raises(ValueError, match="row 2: blank"):
        read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"}))
    path.write_text("accident_id,agree\n1,yes\n2,maybe\n")
    with pytest.raises(ValueError, match="'maybe'"):
        read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"}))
    path.write_text("accident_id,agree\n1,yes\n2,NO\n")
    assert read_decisions(path, key="accident_id", decision_column="agree", allowed=frozenset({"yes", "no"})) == {
        1: "yes",
        2: "no",
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_repair_framework.py tests/test_repair_review.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.data'`).

- [ ] **Step 3: Implement**

`backend/app/data/__init__.py`: `"""Offline data repair (Phase 2a)."""`
`backend/app/data/repair/__init__.py`: `"""Versioned, audited accident repairs R1–R12."""`

`backend/app/data/repair/sources.py`:

```python
from __future__ import annotations

from typing import Literal

SourceFamily = Literal["aac", "avalanche", "nps"]


def source_family(source: str | None) -> SourceFamily:
    key = (source or "").strip().lower()
    if key.startswith("aac"):
        return "aac"
    if key in {"caic", "avalanche", "avalanche.org"} or "avalanche" in key:
        return "avalanche"
    if key.startswith("nps"):
        return "nps"
    raise ValueError(f"unmapped source {source!r}: extend sources.source_family in a reviewed change")
```

`backend/app/data/repair/framework.py`:

```python
"""The one writer for accident repairs. Every changed field gets an
internal.accident_revisions row keyed (accident_id, field, rule_version); a rerun with the
same rule version finds the key and changes nothing."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

# Identifiers interpolated into SQL come only from these sets, never from data.
REPAIRABLE_FIELDS = frozenset(
    {
        "date", "year", "country", "date_precision", "year_source", "year_lo", "year_hi",
        "geocode_precision", "geocode_method", "activity_class", "activity_rule_version",
        "inclusion_flag", "incident_group_id", "is_canonical", "severity_scale", "excluded_reason",
        "source_url", "exp_years_climbing", "exp_stated_level", "exp_first_season", "guided",
        "exp_rule_version",
    }
)
READABLE_COLUMNS = REPAIRABLE_FIELDS | {
    "source", "source_id", "state", "location", "mountain", "route", "latitude", "longitude",
    "elevation_meters", "accident_type", "activity", "injury_severity", "description", "tags", "mp_route_id",
}
LOCATION = "latlon"

Value = str | int | float | bool | date | tuple[float, float] | None


@dataclass(frozen=True)
class Change:
    accident_id: int
    field: str
    new_value: Value
    method: str


@dataclass
class ApplyResult:
    applied: int = 0
    unchanged: int = 0
    already_recorded: int = 0


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return f"{float(value[0]):.6f},{float(value[1]):.6f}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


async def fetch_accidents(conn: AsyncConnection, columns: Sequence[str]) -> list[dict[str, object]]:
    unknown = set(columns) - READABLE_COLUMNS
    if unknown:
        raise ValueError(f"not readable: {sorted(unknown)}")
    cols = ", ".join(["accident_id", *columns])
    result = await conn.execute(text(f"SELECT {cols} FROM accidents ORDER BY accident_id"))
    return [dict(row._mapping) for row in result]


async def apply_changes(
    conn: AsyncConnection, changes: Sequence[Change], *, rule_version: str, run_id: uuid.UUID
) -> ApplyResult:
    for change in changes:
        if change.field != LOCATION and change.field not in REPAIRABLE_FIELDS:
            raise ValueError(f"field {change.field!r} is not repairable")
    result = ApplyResult()
    for change in changes:
        key = {"a": change.accident_id, "f": change.field, "v": rule_version}
        recorded = await conn.execute(
            text("SELECT 1 FROM internal.accident_revisions WHERE accident_id = :a AND field = :f AND rule_version = :v"),
            key,
        )
        if recorded.first() is not None:
            result.already_recorded += 1
            continue
        if change.field == LOCATION:
            row = (
                await conn.execute(
                    text("SELECT latitude, longitude FROM accidents WHERE accident_id = :a FOR UPDATE"),
                    {"a": change.accident_id},
                )
            ).one()
            old: object = None if row.latitude is None or row.longitude is None else (row.latitude, row.longitude)
            if old == change.new_value:
                result.unchanged += 1
                continue
            lat, lon = change.new_value if isinstance(change.new_value, tuple) else (None, None)
            # The coords trigger only fills `coordinates` when both are non-null, so a
            # nulled location must clear it here or the old point would survive.
            await conn.execute(
                text(
                    "UPDATE accidents SET latitude = :lat, longitude = :lon, updated_at = now(), "
                    "coordinates = CASE WHEN CAST(:lat AS double precision) IS NULL THEN NULL ELSE coordinates END "
                    "WHERE accident_id = :a"
                ),
                {"lat": lat, "lon": lon, "a": change.accident_id},
            )
        else:
            old = (
                await conn.execute(
                    text(f"SELECT {change.field} FROM accidents WHERE accident_id = :a FOR UPDATE"),
                    {"a": change.accident_id},
                )
            ).scalar_one()
            if old == change.new_value:
                result.unchanged += 1
                continue
            await conn.execute(
                text(f"UPDATE accidents SET {change.field} = :v, updated_at = now() WHERE accident_id = :a"),
                {"v": change.new_value, "a": change.accident_id},
            )
        await conn.execute(
            text(
                "INSERT INTO internal.accident_revisions "
                "(accident_id, field, old_value, new_value, method, rule_version, run_id) "
                "VALUES (:a, :f, :old, :new, :m, :v, :run)"
            ),
            key | {"old": _as_text(old), "new": _as_text(change.new_value), "m": change.method, "run": run_id},
        )
        result.applied += 1
    return result


async def run_step(
    conn: AsyncConnection,
    *,
    step: str,
    rule_version: str,
    changes: Sequence[Change],
    report: ValidationReport,
    apply: bool,
) -> dict[str, object]:
    proposed = Counter(f"{c.field}={_as_text(c.new_value) if c.field != LOCATION else ('null' if c.new_value is None else 'moved')}" for c in changes)
    summary: dict[str, object] = {
        "step": step,
        "rule_version": rule_version,
        "proposed": dict(proposed.most_common(40)),
        "report": report.summary(),
    }
    if not apply:
        return summary | {"mode": "dry_run"}
    run_id = await start_run(conn, source=f"repair:{step}", window_start=None, window_end=None, content_sha256=rule_version)
    applied = await apply_changes(conn, changes, rule_version=rule_version, run_id=run_id)
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=applied.applied)
    return summary | {
        "mode": "apply",
        "applied": applied.applied,
        "unchanged": applied.unchanged,
        "already_recorded": applied.already_recorded,
    }
```

`backend/app/data/repair/review.py`:

```python
"""Owner review round-trips: a seeded sample goes out as CSV to gitignored data/review/,
decisions come back through the same file. Blank or unknown decisions fail loudly."""

from __future__ import annotations

import csv
import random
from collections.abc import Mapping, Sequence
from pathlib import Path


def export_sample(
    rows: Sequence[Mapping[str, object]],
    *,
    n: int,
    seed: int,
    path: Path,
    columns: Sequence[str],
    decision_column: str,
) -> int:
    chosen = random.Random(seed).sample(list(rows), min(n, len(rows)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([*columns, decision_column])
        for row in chosen:
            writer.writerow([row.get(c) for c in columns] + [""])
    return len(chosen)


def read_decisions(path: Path, *, key: str, decision_column: str, allowed: frozenset[str]) -> dict[int, str]:
    decisions: dict[int, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for number, row in enumerate(csv.DictReader(fh), start=1):
            value = (row.get(decision_column) or "").strip().lower()
            if not value:
                raise ValueError(f"{path.name} row {number}: blank {decision_column}")
            if value not in allowed:
                raise ValueError(f"{path.name} row {number}: {value!r} not in {sorted(allowed)}")
            decisions[int(row[key])] = value
    return decisions
```

`.gitignore`, in the "Data files" block: add `data/review/`.

`backend/pyproject.toml`: append `"app.data", "app.data.repair", "app.data.repair.framework", "app.data.repair.sources", "app.data.repair.review"` to the first strict block.

`backend/db/roles/grants_phase2.sql`, before `RESET ROLE;` add:

```sql
-- Plan 2 (accident repair, PR 2a-1)
GRANT UPDATE (date, year, latitude, longitude, coordinates, country, date_precision, year_source, year_lo, year_hi,
              geocode_precision, geocode_method, activity_class, activity_rule_version, inclusion_flag,
              incident_group_id, is_canonical, severity_scale, excluded_reason, source_url, updated_at,
              exp_years_climbing, exp_stated_level, exp_first_season, guided, exp_rule_version)
  ON public.accidents TO ingest;
GRANT SELECT, INSERT ON internal.accident_revisions TO ingest;
```

`backend/db/roles/verify_roles_phase2.sql`: add `('internal.accident_revisions', 'INSERT')` to `ingest_writes`, and after the `mp_ticks` column check add:

```sql
INSERT INTO role_checks VALUES
  ('ingest may not rewrite accident narratives or identity',
     NOT has_column_privilege('ingest', 'public.accidents', 'description', 'UPDATE')
     AND NOT has_column_privilege('ingest', 'public.accidents', 'source', 'UPDATE')
     AND NOT has_column_privilege('ingest', 'public.accidents', 'source_id', 'UPDATE')
     AND has_column_privilege('ingest', 'public.accidents', 'date_precision', 'UPDATE'));
```

In `backend/tests/test_roles_phase2.py` add after the existing ingest assertions:

```python
    _as(ingest, "UPDATE accidents SET date_precision = 'day' WHERE false")
    _denied(ingest, "UPDATE accidents SET description = 'x' WHERE false")
```

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest tests/test_repair_framework.py tests/test_repair_review.py tests/test_roles_phase2.py -q && uv run mypy && uv run ruff check . ../scripts/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/data .gitignore backend/pyproject.toml backend/db/roles/ backend/tests/test_repair_framework.py \
  backend/tests/test_repair_review.py backend/tests/test_roles_phase2.py
git commit -m "feat(repair): audited idempotent accident repair writer, source families, review CSVs"
```

---

### Task 2: R2 date precision (and the CLI skeleton)

**Files:**
- Create: `backend/app/data/repair/dates.py`, `backend/app/data/repair/__main__.py`, `backend/tests/test_repair_dates.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: Task 1.
- Produces: `R2_VERSION = "r2-v1"`, `r2_precision(family: SourceFamily, d: date | None, year: float | None) -> str`; CLI `python -m app.data.repair <step> [--apply]` with `build_parser()` and a `STEPS: dict[str, Callable[[AsyncConnection, argparse.Namespace], Awaitable[dict[str, object]]]]` registry that later tasks extend.

- [ ] **Step 1: Failing tests**

`backend/tests/test_repair_dates.py` (R2 part; Task 3 appends R1 tests):

```python
from datetime import date

from app.data.repair.dates import r2_precision


def test_aac_day_15_is_month_and_july_1_is_year():
    assert r2_precision("aac", date(2004, 3, 15), 2004) == "month"
    assert r2_precision("aac", date(2004, 7, 1), 2004) == "year"
    assert r2_precision("aac", date(2004, 3, 14), 2004) == "day"


def test_other_sources_keep_day_precision():
    assert r2_precision("nps", date(2004, 7, 1), 2004) == "day"
    assert r2_precision("avalanche", date(2004, 3, 15), 2004) == "day"


def test_year_only_and_missing():
    assert r2_precision("aac", None, 2004.0) == "year"
    assert r2_precision("nps", None, None) == "unknown"
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_repair_dates.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`backend/app/data/repair/dates.py` (R2 plus an R2-only `run_r2r1`; Task 3 rewrites the file to add R1 and make `run_r2r1` run R1 first):

```python
"""R1 (AAC year repair from source_id neighbours) and R2 (date precision)."""

from __future__ import annotations

import argparse
from datetime import date

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.validate import ValidationReport

R2_VERSION = "r2-v1"


def r2_precision(family: SourceFamily, d: date | None, year: float | None) -> str:
    if d is None:
        return "year" if year is not None else "unknown"
    if family == "aac" and d.day == 15:
        return "month"
    if family == "aac" and (d.month, d.day) == (7, 1):
        return "year"
    return "day"


async def run_r2r1(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["source", "date", "year"])
    report = ValidationReport("repair:r2")
    changes: list[Change] = []
    for r in rows:
        d = r["date"] if isinstance(r["date"], date) else None
        year = float(str(r["year"])) if r["year"] is not None else None
        precision = r2_precision(source_family(r["source"] if isinstance(r["source"], str) else None), d, year)
        report.accept()
        changes.append(Change(int(str(r["accident_id"])), "date_precision", precision, "r2"))
    return await run_step(conn, step="r2", rule_version=R2_VERSION, changes=changes, report=report, apply=args.apply)
```

`backend/app/data/repair/__main__.py`:

```python
"""python -m app.data.repair <step> [--apply]. Dry-run by default; prints counts only."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import dates

Step = Callable[[AsyncConnection, argparse.Namespace], Awaitable[dict[str, object]]]
STEPS: dict[str, Step] = {"r2r1": dates.run_r2r1}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.data.repair")
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--review-file", help="decisions CSV for *-import steps")
    parser.add_argument("--out", help="output path for *-export steps")
    return parser


async def _main(args: argparse.Namespace) -> None:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            result = await STEPS[args.step](conn, args)
    finally:
        await engine.dispose()
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    asyncio.run(_main(build_parser().parse_args()))
```

Do not `--apply` the R2-only step on any database: R1 must run first (Task 3), and the runbook only runs `r2r1` after Task 3 lands.

Append `"app.data.repair.dates", "app.data.repair.__main__"` to the strict mypy block.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_repair_dates.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/data/repair/dates.py backend/app/data/repair/__main__.py backend/tests/test_repair_dates.py backend/pyproject.toml
git commit -m "feat(repair): R2 date precision rules and repair CLI"
```

---

### Task 3: R1 AAC year repair from `source_id` neighbours

**Files:**
- Modify: `backend/app/data/repair/dates.py`, `backend/tests/test_repair_dates.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `R1_VERSION = "r1-v1"`, `SUSPECT_FROM_YEAR = 2019`, `NEIGHBOURS = 10`, `MIN_SIDE = 5`, `MAX_SPREAD = 2`, `source_sort_key(source_id: str | None) -> int | None`, `@dataclass(frozen=True) AacRow(accident_id: int, sort_key: int | None, date: date | None, year: int | None)`, `@dataclass(frozen=True) YearFix(accident_id: int, year: int | None, year_lo: int | None, year_hi: int | None, date_precision: str, new_date: date | None, excluded_reason: str | None)`, `r1_year_fixes(rows: Sequence[AacRow]) -> list[YearFix]`, `changes_for(fix: YearFix, *, old_date: date | None) -> list[Change]`, `async run_r2r1(conn, args) -> dict[str, object]` (final form). `[assumes D10]`

- [ ] **Step 1: Failing tests** — append to `backend/tests/test_repair_dates.py`:

```python
from app.data.repair.dates import AacRow, changes_for, r1_year_fixes, source_sort_key


def _trusted(start_key: int, years: list[int]) -> list[AacRow]:
    return [AacRow(1000 + i, start_key + i, date(y, 5, 2), y) for i, y in enumerate(years)]


def test_sort_key_takes_the_trailing_integer():
    assert source_sort_key("ANAC-2004-0123") == 123
    assert source_sort_key("  88 ") == 88
    assert source_sort_key("no digits") is None
    assert source_sort_key(None) is None


def test_confident_neighbours_keep_day_and_month():
    rows = _trusted(0, [2003] * 10) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (2003, 2003, 2003, "day", date(2003, 5, 2))
    assert fix.excluded_reason is None


def test_narrow_spread_becomes_year_precision_on_july_1():
    rows = _trusted(0, [2002] * 5 + [2003] * 5) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2003] * 5 + [2004] * 5)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (2003, 2002, 2004, "year", date(2003, 7, 1))


def test_wide_spread_is_unverified_and_date_is_untouched():
    rows = _trusted(0, [1995] * 10) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2004] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.date_precision, fix.excluded_reason, fix.new_date) == ("unknown", "year_unverified", None)
    assert [c.field for c in changes_for(fix, old_date=date(2023, 5, 2))] == [
        "year_lo", "year_hi", "year_source", "date_precision", "excluded_reason"
    ]


def test_row_past_last_trusted_id_is_unverified():
    rows = _trusted(0, [2018] * 20) + [AacRow(1, 500, date(2019, 6, 3), 2019)]
    [fix] = r1_year_fixes(rows)
    assert fix.excluded_reason == "year_unverified"
    assert fix.new_date is None


def test_unparseable_source_id_is_unverified():
    rows = _trusted(0, [2003] * 20) + [AacRow(1, None, date(2023, 5, 2), 2023)]
    [fix] = r1_year_fixes(rows)
    assert fix.excluded_reason == "year_unverified"


def test_feb_29_into_a_non_leap_year_falls_back_to_month_precision():
    rows = _trusted(0, [2003] * 10) + [AacRow(1, 10, date(2024, 2, 29), 2024)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.new_date, fix.date_precision) == (date(2003, 2, 15), "month")


def test_confident_fix_changes_date_and_year():
    rows = _trusted(0, [2003] * 10) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    fields = {c.field: c.new_value for c in changes_for(fix, old_date=date(2023, 5, 2))}
    assert fields["date"] == date(2003, 5, 2) and fields["year"] == 2003
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_repair_dates.py -q` → FAIL (`ImportError: cannot import name 'AacRow'`).

- [ ] **Step 3: Implement** — replace `backend/app/data/repair/dates.py` with:

```python
"""R1 (AAC year repair from source_id neighbours) and R2 (date precision).

AAC source_id rises monotonically with publication year (audit 2026-09-27), so the years
of the trusted rows around a suspect row bound its real year. No LLM, no guessing: a row
whose neighbours disagree by more than two years, or that has too few neighbours on
either side, is left dated as it is and excluded as year_unverified (D10).
"""

from __future__ import annotations

import argparse
import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median_low

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.validate import ValidationReport

R1_VERSION = "r1-v1"
R2_VERSION = "r2-v1"
SUSPECT_FROM_YEAR = 2019
NEIGHBOURS = 10
MIN_SIDE = 5
MAX_SPREAD = 2
YEAR_SOURCE = "aac_source_id_neighbours"
_TRAILING_INT = re.compile(r"(\d+)\s*$")


def r2_precision(family: SourceFamily, d: date | None, year: float | None) -> str:
    if d is None:
        return "year" if year is not None else "unknown"
    if family == "aac" and d.day == 15:
        return "month"
    if family == "aac" and (d.month, d.day) == (7, 1):
        return "year"
    return "day"


def source_sort_key(source_id: str | None) -> int | None:
    match = _TRAILING_INT.search(source_id or "")
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class AacRow:
    accident_id: int
    sort_key: int | None
    date: date | None
    year: int | None


@dataclass(frozen=True)
class YearFix:
    accident_id: int
    year: int | None
    year_lo: int | None
    year_hi: int | None
    date_precision: str
    new_date: date | None
    excluded_reason: str | None


def _unverified(row: AacRow, lo: int | None = None, hi: int | None = None) -> YearFix:
    return YearFix(row.accident_id, None, lo, hi, "unknown", None, "year_unverified")


def _same_day_in(year: int, d: date) -> tuple[date, str]:
    try:
        moved = d.replace(year=year)
    except ValueError:
        return date(year, 2, 15), "month"
    return moved, r2_precision("aac", moved, year)


def r1_year_fixes(rows: Sequence[AacRow]) -> list[YearFix]:
    trusted = sorted(
        (r.sort_key, r.year)
        for r in rows
        if r.sort_key is not None and r.year is not None and r.year < SUSPECT_FROM_YEAR
    )
    keys = [k for k, _ in trusted]
    fixes: list[YearFix] = []
    for row in rows:
        if row.year is None or row.year < SUSPECT_FROM_YEAR:
            continue
        if row.sort_key is None:
            fixes.append(_unverified(row))
            continue
        i = bisect_left(keys, row.sort_key)
        left = [y for _, y in trusted[max(0, i - NEIGHBOURS) : i]]
        right = [y for _, y in trusted[i : i + NEIGHBOURS]]
        if len(left) < MIN_SIDE or len(right) < MIN_SIDE:
            fixes.append(_unverified(row))
            continue
        years = left + right
        lo, hi, year = min(years), max(years), median_low(years)
        if hi - lo > MAX_SPREAD:
            fixes.append(_unverified(row, lo, hi))
        elif lo == hi and row.date is not None:
            new_date, precision = _same_day_in(year, row.date)
            fixes.append(YearFix(row.accident_id, year, lo, hi, precision, new_date, None))
        else:
            fixes.append(YearFix(row.accident_id, year, lo, hi, "year", date(year, 7, 1), None))
    return fixes


def changes_for(fix: YearFix, *, old_date: date | None) -> list[Change]:
    a = fix.accident_id
    changes = [
        Change(a, "year_lo", fix.year_lo, "r1"),
        Change(a, "year_hi", fix.year_hi, "r1"),
        Change(a, "year_source", YEAR_SOURCE, "r1"),
        Change(a, "date_precision", fix.date_precision, "r1"),
        Change(a, "excluded_reason", fix.excluded_reason, "r1"),
    ]
    if fix.new_date is not None and fix.new_date != old_date:
        changes += [Change(a, "date", fix.new_date, "r1"), Change(a, "year", fix.year, "r1")]
    return changes


async def run_r2r1(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["source", "source_id", "date", "year"])
    families = {int(str(r["accident_id"])): source_family(r["source"] if isinstance(r["source"], str) else None) for r in rows}
    aac = [
        AacRow(
            int(str(r["accident_id"])),
            source_sort_key(r["source_id"] if isinstance(r["source_id"], str) else None),
            r["date"] if isinstance(r["date"], date) else None,
            r["date"].year if isinstance(r["date"], date) else (int(float(str(r["year"]))) if r["year"] is not None else None),
        )
        for r in rows
        if families[int(str(r["accident_id"]))] == "aac"
    ]
    fixes = {f.accident_id: f for f in r1_year_fixes(aac)}
    r1_changes: list[Change] = []
    r2_changes: list[Change] = []
    r1_report, r2_report = ValidationReport("repair:r1"), ValidationReport("repair:r2")
    for r in rows:
        a = int(str(r["accident_id"]))
        d = r["date"] if isinstance(r["date"], date) else None
        if a in fixes:
            fix = fixes[a]
            r1_changes += changes_for(fix, old_date=d)
            if fix.excluded_reason:
                r1_report.quarantine(str(a), fix.excluded_reason)
            else:
                r1_report.accept()
            continue
        year = float(str(r["year"])) if r["year"] is not None else None
        precision = r2_precision(families[a], d, year)
        r2_changes.append(Change(a, "date_precision", precision, "r2"))
        if precision == "unknown":
            r2_changes.append(Change(a, "excluded_reason", "year_unverified", "r2"))
            r2_report.quarantine(str(a), "no_date")
        else:
            r2_report.accept()
    r1 = await run_step(conn, step="r1", rule_version=R1_VERSION, changes=r1_changes, report=r1_report, apply=args.apply)
    r2 = await run_step(conn, step="r2", rule_version=R2_VERSION, changes=r2_changes, report=r2_report, apply=args.apply)
    return {"r1": r1, "r2": r2}
```

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_repair_dates.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/data/repair/dates.py backend/tests/test_repair_dates.py
git commit -m "feat(repair): R1 AAC year repair from source_id neighbours (unverified rows keep their date)"
```

---

### Task 4: R9 severity scale, hand-check exports (R1 years, R9 severity), verification cells

**Files:**
- Create: `backend/app/data/repair/severity.py`, `backend/tests/test_repair_severity.py`, `backend/tests/verify/test_phase2a_repair.py`
- Modify: `backend/app/data/repair/__main__.py`, `backend/pyproject.toml`, `CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `R9_VERSION = "r9-v1"`, `severity_scale(family: SourceFamily, injury_severity: str | None) -> str`, `agreement(decisions: dict[int, str]) -> float`, CLI steps `r9`, `r9-export`, `r9-import`, `r1-export`, `r1-import`. Hand-check results are logged as `source_ingest_log` rows with `source = 'repair:r9-check'` / `'repair:r1-check'` and the agreement in `validation_report`.

- [ ] **Step 1: Failing tests**

`backend/tests/test_repair_severity.py`:

```python
import pytest

from app.data.repair.severity import agreement, severity_scale


def test_nps_is_a_mortality_source():
    assert severity_scale("nps", "Fatal") == "fatal_only"
    assert severity_scale("nps", None) == "fatal_only"


def test_other_sources_are_full_scale_when_severity_is_present():
    assert severity_scale("aac", "Serious") == "full"
    assert severity_scale("aac", "  ") == "unknown"
    assert severity_scale("avalanche", None) == "unknown"


def test_agreement_rate():
    assert agreement({1: "yes", 2: "yes", 3: "no", 4: "yes"}) == 0.75
    with pytest.raises(ValueError):
        agreement({})
```

`backend/tests/verify/test_phase2a_repair.py` (PR 2a-1 cells; PR 2a-2 appends):

```python
"""Phase 2a acceptance cells (spec R-table "Verification" column), as analyst."""

import asyncio
import os

import asyncpg
import pytest

pytestmark = pytest.mark.db
URL = os.environ.get("VERIFY_DATABASE_URL")


def fetch(sql: str) -> list[asyncpg.Record]:
    if not URL:
        pytest.skip("VERIFY_DATABASE_URL not set")

    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(URL)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_r1_2023_aac_count_is_at_most_twice_the_2015_2018_mean():
    [row] = fetch(
        "WITH y AS (SELECT extract(year FROM date)::int AS yr, count(*) AS n FROM accidents "
        "WHERE source ILIKE 'aac%' AND excluded_reason IS NULL GROUP BY 1) "
        "SELECT coalesce((SELECT n FROM y WHERE yr = 2023), 0) AS y2023, "
        "(SELECT avg(n) FROM y WHERE yr BETWEEN 2015 AND 2018) AS base"
    )
    print(dict(row))
    assert row["y2023"] <= 2 * row["base"]


def test_r1_hand_check_at_least_90_percent_within_one_year():
    [row] = fetch(
        "SELECT (validation_report->>'agreement')::float AS a FROM source_ingest_log "
        "WHERE source = 'repair:r1-check' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1"
    )
    assert row["a"] >= 0.90


def test_r2_counts_are_near_the_audit():
    rows = fetch(
        "SELECT date_precision AS p, count(*) AS n FROM accidents WHERE source ILIKE 'aac%' GROUP BY 1"
    )
    counts = {r["p"]: r["n"] for r in rows}
    print(counts)
    assert 1400 <= counts.get("month", 0) <= 1750
    assert 100 <= counts.get("year", 0) <= 200


def test_every_row_has_a_precision_and_unknown_pairs_with_year_unverified():
    [row] = fetch(
        "SELECT count(*) FILTER (WHERE date_precision IS NULL) AS missing, "
        "count(*) FILTER (WHERE date_precision = 'unknown' AND excluded_reason IS DISTINCT FROM 'year_unverified') AS unpaired "
        "FROM accidents"
    )
    assert (row["missing"], row["unpaired"]) == (0, 0)


def test_r9_severity_scale_set_and_agreement_recorded():
    [row] = fetch("SELECT count(*) FILTER (WHERE severity_scale IS NULL) AS missing FROM accidents")
    assert row["missing"] == 0
    [check] = fetch(
        "SELECT (validation_report->>'agreement')::float AS a FROM source_ingest_log "
        "WHERE source = 'repair:r9-check' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1"
    )
    print({"r9_agreement": check["a"]})
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_repair_severity.py -q` → FAIL.

- [ ] **Step 3: Implement**

`backend/app/data/repair/severity.py`:

```python
"""R9: which severity scale each row's injury_severity is on. NPS records are a mortality
dataset (every row fatal), so its severity cannot be compared with AAC's."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.review import export_sample, read_decisions
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

R9_VERSION = "r9-v1"
YES_NO = frozenset({"yes", "no"})


def severity_scale(family: SourceFamily, injury_severity: str | None) -> str:
    if family == "nps":
        return "fatal_only"
    return "full" if injury_severity and injury_severity.strip() else "unknown"


def agreement(decisions: dict[int, str]) -> float:
    if not decisions:
        raise ValueError("no decisions")
    return sum(1 for v in decisions.values() if v == "yes") / len(decisions)


async def run_r9(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["source", "injury_severity"])
    report = ValidationReport("repair:r9")
    changes = []
    for r in rows:
        scale = severity_scale(
            source_family(r["source"] if isinstance(r["source"], str) else None),
            r["injury_severity"] if isinstance(r["injury_severity"], str) else None,
        )
        report.accept()
        changes.append(Change(int(str(r["accident_id"])), "severity_scale", scale, "r9"))
    return await run_step(conn, step="r9", rule_version=R9_VERSION, changes=changes, report=report, apply=args.apply)


async def _record_check(conn: AsyncConnection, source: str, decisions: dict[int, str]) -> dict[str, object]:
    rate = agreement(decisions)
    report = ValidationReport(source)
    for accident_id, value in decisions.items():
        if value == "yes":
            report.accept()
        else:
            report.quarantine(str(accident_id), "disagree")
    run_id = await start_run(conn, source=source, window_start=None, window_end=None, content_sha256=None)
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=0)
    await conn.execute(
        text(
            "UPDATE source_ingest_log SET validation_report = validation_report || "
            "jsonb_build_object('agreement', CAST(:rate AS double precision)) WHERE run_id = :run_id"
        ),
        {"rate": rate, "run_id": run_id},
    )
    return {"source": source, "n": len(decisions), "agreement": rate}


async def run_r9_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = [r for r in await fetch_accidents(conn, ["source", "injury_severity", "date"]) if str(r["source"]).lower().startswith("aac")]
    out = Path(args.out or "../data/review/severity_check.csv")
    n = export_sample(rows, n=50, seed=9, path=out, columns=["accident_id", "date", "injury_severity"], decision_column="agree")
    return {"exported": n, "path": str(out), "instructions": "set agree=yes|no after reading each AAC report"}


async def run_r9_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = read_decisions(Path(args.review_file), key="accident_id", decision_column="agree", allowed=YES_NO)
    return await _record_check(conn, "repair:r9-check", decisions)


async def run_r1_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = [
        r for r in await fetch_accidents(conn, ["source", "date", "year_lo", "year_hi", "year_source", "source_url"])
        if r["year_source"] == "aac_source_id_neighbours" and isinstance(r["date"], date)
    ]
    out = Path(args.out or "../data/review/r1_year_check.csv")
    n = export_sample(
        rows, n=40, seed=1, path=out,
        columns=["accident_id", "date", "year_lo", "year_hi", "source_url"], decision_column="within_one_year",
    )
    return {
        "exported": n,
        "path": str(out),
        "instructions": "look up each report in the AAC Publications archive (year and title only); within_one_year=yes|no",
    }


async def run_r1_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = read_decisions(Path(args.review_file), key="accident_id", decision_column="within_one_year", allowed=YES_NO)
    return await _record_check(conn, "repair:r1-check", decisions)
```

In `backend/app/data/repair/__main__.py`: `from app.data.repair import dates, severity` and extend `STEPS` with `"r9": severity.run_r9, "r9-export": severity.run_r9_export, "r9-import": severity.run_r9_import, "r1-export": severity.run_r1_export, "r1-import": severity.run_r1_import`.

Append `"app.data.repair.severity"` to the strict mypy block.

`CHANGELOG.md` under `## [Unreleased]`:

```
### Phase 2a accident repair, part 1 (PR 2a-1) — <merge date>

- `app/data/repair/`: one audited, idempotent writer for accident repairs (`internal.accident_revisions` per changed field, keyed by rule version), a dry-run-first CLI (`python -m app.data.repair`), and seeded owner review CSVs in gitignored `data/review/`.
- R2 date precision (AAC day-15 → month, Jul-1 → year), R1 AAC year repair from `source_id` neighbours (rows with too few or disagreeing neighbours keep their date and are excluded as `year_unverified`), R9 severity scale (NPS is fatal-only), with 40-row (R1) and 50-row (R9) hand checks recorded in `source_ingest_log`.
- `ingest` gains column-level UPDATE on the repair columns of `accidents` (never `description`, `source`, `source_id`).
```

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → PASS; `uv run pytest -m db --co -q tests/verify | tail -1` shows the new cells collected.

- [ ] **Step 5: Commit**

```bash
git add backend/app/data/repair/severity.py backend/app/data/repair/__main__.py backend/tests/test_repair_severity.py \
  backend/tests/verify/test_phase2a_repair.py backend/pyproject.toml CHANGELOG.md
git commit -m "feat(repair): R9 severity scale, R1/R9 hand-check round trips, 2a-1 acceptance cells"
```

---

# PR 2a-2 — `feat/p2a-repair-geo-dedupe`

### Task 5: Text similarity and distance helpers

**Files:**
- Create: `backend/app/pipelines/textsim.py`, `backend/app/pipelines/geo.py`, `backend/tests/test_textsim.py`, `backend/tests/test_geo.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `jaro(a: str, b: str) -> float`, `jaro_winkler(a: str, b: str, *, prefix_scale: float = 0.1) -> float`, `normalize_text(s: str | None) -> str`, `place_key(s: str | None) -> str`; `haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float`, `haversine_km_many(lat: float, lon: float, lats: NDArray[np.float64], lons: NDArray[np.float64]) -> NDArray[np.float64]`.

- [ ] **Step 1: Failing tests**

`backend/tests/test_textsim.py`:

```python
import pytest

from app.pipelines.textsim import jaro, jaro_winkler, normalize_text, place_key


def test_classic_jaro_winkler_values():
    assert jaro("MARTHA", "MARHTA") == pytest.approx(0.9444, abs=1e-4)
    assert jaro_winkler("MARTHA", "MARHTA") == pytest.approx(0.9611, abs=1e-4)
    assert jaro_winkler("DIXON", "DICKSONX") == pytest.approx(0.8133, abs=1e-4)


def test_edge_cases():
    assert jaro_winkler("", "") == 1.0
    assert jaro_winkler("abc", "") == 0.0
    assert jaro_winkler("same", "same") == 1.0


def test_normalize_and_place_key_expand_abbreviations_and_drop_generic_words():
    assert normalize_text("Mt. St. Helens!") == "mount saint helens"
    assert place_key("Mt. Rainier") == "rainier"
    assert place_key("Longs Peak") == "longs"
    assert place_key("Peak") == "peak"
    assert place_key(None) == ""
```

`backend/tests/test_geo.py`:

```python
import numpy as np
import pytest

from app.pipelines.geo import haversine_km, haversine_km_many


def test_one_degree_of_latitude():
    assert haversine_km(40.0, -105.0, 41.0, -105.0) == pytest.approx(111.19, abs=0.05)


def test_vectorized_matches_scalar():
    lats, lons = np.array([41.0, 40.0]), np.array([-105.0, -104.0])
    out = haversine_km_many(40.0, -105.0, lats, lons)
    assert out[0] == pytest.approx(haversine_km(40.0, -105.0, 41.0, -105.0))
    assert out[1] == pytest.approx(haversine_km(40.0, -105.0, 40.0, -104.0))
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`backend/app/pipelines/textsim.py`:

```python
"""Jaro-Winkler and place-name keys, built in-house (matching is core to the project)."""

from __future__ import annotations

import re

_ABBREV = {"mt": "mount", "mtn": "mountain", "pk": "peak", "st": "saint", "ft": "fort"}
_GENERIC = frozenset({"mount", "mountain", "peak", "the"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    a_hit = [False] * len(a)
    b_hit = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hit[j] and b[j] == ch:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    a_seq = [ch for ch, hit in zip(a, a_hit) if hit]
    b_seq = [ch for ch, hit in zip(b, b_hit) if hit]
    transpositions = sum(x != y for x, y in zip(a_seq, b_seq)) / 2
    return (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3


def jaro_winkler(a: str, b: str, *, prefix_scale: float = 0.1) -> float:
    j = jaro(a, b)
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return j + prefix * prefix_scale * (1 - j)


def normalize_text(s: str | None) -> str:
    tokens = _NON_ALNUM.sub(" ", (s or "").lower()).split()
    return " ".join(_ABBREV.get(t, t) for t in tokens)


def place_key(s: str | None) -> str:
    tokens = normalize_text(s).split()
    kept = [t for t in tokens if t not in _GENERIC]
    return " ".join(kept) if kept else " ".join(tokens)
```

`backend/app/pipelines/geo.py`:

```python
from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def haversine_km_many(lat: float, lon: float, lats: NDArray[np.float64], lons: NDArray[np.float64]) -> NDArray[np.float64]:
    p1, p2 = np.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons - lon)
    h = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    out: NDArray[np.float64] = 2 * EARTH_RADIUS_KM * np.arcsin(np.minimum(1.0, np.sqrt(h)))
    return out
```

Append `"app.pipelines.textsim", "app.pipelines.geo"` to the strict mypy block.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_textsim.py tests/test_geo.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/textsim.py backend/app/pipelines/geo.py backend/tests/test_textsim.py backend/tests/test_geo.py backend/pyproject.toml && git commit -m "feat(pipelines): in-house Jaro-Winkler, place keys, haversine"`

---

### Task 6: Migration `0005`, GNIS summits loader

**Files:**
- Create: `backend/alembic/versions/0005_gnis_and_duplicate_decisions.py`, `backend/app/models/reference.py`, `backend/app/pipelines/gnis.py`, `backend/tests/test_gnis.py`, `backend/tests/test_migration_0005_0006.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB): `public.gnis_summits(gnis_id integer PK, name text NOT NULL, name_key text NOT NULL, state_code char(2) NOT NULL, lat double precision NOT NULL, lon double precision NOT NULL, loaded_run_id uuid)`, index `ix_gnis_summits_name_key(name_key)`; `internal.duplicate_decisions(low_id integer, high_id integer, score double precision NOT NULL, decision text CHECK IN ('merge','distinct'), decided_by text CHECK IN ('auto','owner'), rule_version text NOT NULL, run_id uuid, decided_at timestamptz default now(), PK(low_id, high_id), CHECK (low_id < high_id))`.
- Produces (Python): `STATE_CODES: dict[str, str]` (state name → USPS code, 50 states + DC), `state_code(text: str | None) -> str | None` (accepts names and codes), `@dataclass(frozen=True) Summit(gnis_id: int, name: str, name_key: str, state_code: str, lat: float, lon: float)`, `parse_domestic_names(lines: Iterable[str], report: ValidationReport) -> list[Summit]`, `open_gnis(path: Path) -> Iterator[str]` (zip or txt), `async load_summits(conn, summits: list[Summit], *, run_id) -> int`, CLI `python -m app.pipelines.gnis --file PATH`.

- [ ] **Step 1: Failing tests**

`backend/tests/test_gnis.py`:

```python
import zipfile

import pytest

from app.pipelines.gnis import open_gnis, parse_domestic_names, state_code
from app.pipelines.validate import ValidationReport

HEADER = "feature_id|feature_name|feature_class|state_name|state_numeric|county_name|prim_lat_dec|prim_long_dec|map_name"
LINES = [
    HEADER,
    "1|Fixture Peak|Summit|Colorado|08|Boulder|40.25|-105.6|x",
    "2|Fixture Lake|Lake|Colorado|08|Boulder|40.2|-105.6|x",
    "3|Nowhere Summit|Summit|Colorado|08|Boulder|0|0|x",
    "4|Island Summit|Summit|Guam|66|Guam|13.4|144.7|x",
]


def test_only_valid_us_summits_are_kept_and_the_rest_counted():
    report = ValidationReport("gnis")
    summits = parse_domestic_names(LINES, report)
    assert [(s.gnis_id, s.name_key, s.state_code) for s in summits] == [(1, "fixture", "CO")]
    assert report.quarantined == {"outside_us": 1, "unknown_state": 1}


def test_header_drift_fails_loudly():
    with pytest.raises(ValueError, match="missing"):
        parse_domestic_names(["feature_id|name|class", "1|x|Summit"], ValidationReport("gnis"))


def test_state_code_accepts_names_and_codes():
    assert state_code("Alaska") == "AK"
    assert state_code(" wa ") == "WA"
    assert state_code("British Columbia") is None


def test_open_gnis_reads_the_national_member_of_a_zip(tmp_path):
    path = tmp_path / "gnis.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Text/DomesticNames_National.txt", "\n".join(LINES))
    assert list(open_gnis(path))[0].startswith("feature_id|")
```

`backend/tests/test_migration_0005_0006.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0005_tables_and_checks():
    with migrated_db("head") as name:
        command.check(_alembic_cfg(name))
        run_sql(name, "INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) "
                      "VALUES (1, 'Fixture Peak', 'fixture', 'CO', 40.25, -105.6)")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO internal.duplicate_decisions (low_id, high_id, score, decision, decided_by, rule_version) "
                          "VALUES (5, 4, 0.95, 'merge', 'auto', 'r5-v1')")
```

(Task 11 puts the `0006` view tests in their own file.)

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError`, missing tables).

- [ ] **Step 3: Implement the migration and models**

`backend/alembic/versions/0005_gnis_and_duplicate_decisions.py`:

```python
"""USGS GNIS summits (public domain reference data) and R5 duplicate decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_gnis_and_duplicate_decisions"
down_revision = "0004_phase2a_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gnis_summits",
        sa.Column("gnis_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("state_code", sa.CHAR(2), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("loaded_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_gnis_summits_name_key", "gnis_summits", ["name_key"])
    op.create_table(
        "duplicate_decisions",
        sa.Column("low_id", sa.Integer(), nullable=False),
        sa.Column("high_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("low_id", "high_id"),
        sa.CheckConstraint("low_id < high_id", name="duplicate_decisions_order_check"),
        sa.CheckConstraint("decision IN ('merge', 'distinct')", name="duplicate_decisions_decision_check"),
        sa.CheckConstraint("decided_by IN ('auto', 'owner')", name="duplicate_decisions_by_check"),
        schema="internal",
    )


def downgrade() -> None:
    bind = op.get_bind()
    owner_rows = bind.exec_driver_sql(
        "SELECT count(*) FROM internal.duplicate_decisions WHERE decided_by = 'owner'"
    ).scalar_one()
    if owner_rows:
        raise RuntimeError(f"refusing to downgrade 0005: {owner_rows} owner duplicate decisions would be lost")
    op.drop_table("duplicate_decisions", schema="internal")
    op.drop_index("ix_gnis_summits_name_key", "gnis_summits")
    op.drop_table("gnis_summits")
```

`backend/app/models/reference.py`:

```python
"""Reference and review tables for the Phase 2a repairs (migration 0005)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, Float, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GnisSummit(Base):
    __tablename__ = "gnis_summits"
    __table_args__ = (Index("ix_gnis_summits_name_key", "name_key"),)

    gnis_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text)
    state_code: Mapped[str] = mapped_column(CHAR(2))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    loaded_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class DuplicateDecision(Base):
    __tablename__ = "duplicate_decisions"
    __table_args__ = {"schema": "internal"}

    low_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    high_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    score: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`backend/app/models/__init__.py`: `from app.models import reference  # noqa: F401`. Add `"app.models.reference"` to the models mypy block (the one without `disallow_subclassing_any`).

- [ ] **Step 4: Implement the GNIS parser/loader**

`backend/app/pipelines/gnis.py`:

```python
"""USGS GNIS DomesticNames (public domain) → gnis_summits. Reads a downloaded national
file (pipe-delimited, optionally zipped); the header is checked so a format change fails
loudly instead of loading shifted columns."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import uuid
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, last_ok_rows_in, sha256_rows, start_run, write_quarantine
from app.pipelines.textsim import place_key
from app.pipelines.validate import ValidationReport, batch_gate, coord_problem

SOURCE = "gnis_summits"
REQUIRED = ("feature_id", "feature_name", "feature_class", "state_name", "prim_lat_dec", "prim_long_dec")
STATE_CODES: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
_BY_LOWER = {k.lower(): v for k, v in STATE_CODES.items()} | {v.lower(): v for v in STATE_CODES.values()}


def state_code(value: str | None) -> str | None:
    return _BY_LOWER.get((value or "").strip().lower())


@dataclass(frozen=True)
class Summit:
    gnis_id: int
    name: str
    name_key: str
    state_code: str
    lat: float
    lon: float


def open_gnis(path: Path) -> Iterator[str]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            member = next(n for n in archive.namelist() if n.rsplit("/", 1)[-1].startswith("DomesticNames_National"))
            with archive.open(member) as raw:
                yield from io.TextIOWrapper(raw, encoding="utf-8-sig")
    else:
        with path.open(encoding="utf-8-sig") as fh:
            yield from fh


def parse_domestic_names(lines: Iterable[str], report: ValidationReport) -> list[Summit]:
    reader = csv.DictReader((line.rstrip("\n") for line in lines), delimiter="|", quoting=csv.QUOTE_NONE)
    missing = set(REQUIRED) - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"GNIS header changed; missing {sorted(missing)}")
    summits: list[Summit] = []
    for row in reader:
        if row["feature_class"] != "Summit":
            continue
        ref = row["feature_id"]
        code = state_code(row["state_name"])
        if code is None:
            report.quarantine(ref, "unknown_state", state=row["state_name"])
            continue
        try:
            lat, lon = float(row["prim_lat_dec"]), float(row["prim_long_dec"])
        except ValueError:
            report.quarantine(ref, "bad_coords")
            continue
        problem = coord_problem(lat, lon)
        if problem is not None:
            report.quarantine(ref, problem)
            continue
        report.accept()
        summits.append(Summit(int(ref), row["feature_name"], place_key(row["feature_name"]), code, lat, lon))
    return summits


async def load_summits(conn: AsyncConnection, summits: list[Summit], *, run_id: uuid.UUID) -> int:
    await conn.execute(
        text(
            "INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon, loaded_run_id) "
            "VALUES (:gnis_id, :name, :name_key, :state_code, :lat, :lon, :run_id) "
            "ON CONFLICT (gnis_id) DO UPDATE SET name = EXCLUDED.name, name_key = EXCLUDED.name_key, "
            "state_code = EXCLUDED.state_code, lat = EXCLUDED.lat, lon = EXCLUDED.lon, loaded_run_id = EXCLUDED.loaded_run_id"
        ),
        [s.__dict__ | {"run_id": run_id} for s in summits],
    )
    await conn.execute(text("DELETE FROM gnis_summits WHERE gnis_id <> ALL(:ids)"), {"ids": [s.gnis_id for s in summits]})
    return len(summits)


async def main(path: Path) -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    report = ValidationReport(SOURCE)
    summits = parse_domestic_names(open_gnis(path), report)
    sha = sha256_rows([(s.gnis_id, s.name, s.state_code, s.lat, s.lon) for s in summits])
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            previous = await last_ok_rows_in(conn, SOURCE)
            problems = batch_gate(report, previous_rows_in=previous, count_tolerance=0.05, max_quarantine_share=0.01)
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha)
            await write_quarantine(conn, run_id, report)
            if problems:
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=problems)
                return {"status": "rejected", "problems": problems, "report": report.summary()}
            n = await load_summits(conn, summits, run_id=run_id)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
            return {"status": "ok", "rows_upserted": n, "report": report.summary()}
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load USGS GNIS summits")
    parser.add_argument("--file", type=Path, required=True)
    print(json.dumps(asyncio.run(main(parser.parse_args().file)), sort_keys=True))
```

Append `"app.pipelines.gnis"` to the strict mypy block.

Grants (`grants_phase2.sql`, before `RESET ROLE;`):

```sql
-- Plan 2 (PR 2a-2)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.gnis_summits TO ingest;
GRANT SELECT, INSERT, UPDATE ON internal.duplicate_decisions TO ingest;
```

`verify_roles_phase2.sql` `ingest_writes`: add `('public.gnis_summits','INSERT'), ('public.gnis_summits','UPDATE'), ('public.gnis_summits','DELETE'), ('internal.duplicate_decisions','INSERT'), ('internal.duplicate_decisions','UPDATE')`.

- [ ] **Step 5: Run** — `cd backend && uv run pytest tests/test_gnis.py tests/test_migration_0005_0006.py tests/test_roles_phase2.py tests/test_migrations.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0005_gnis_and_duplicate_decisions.py backend/app/models/ backend/app/pipelines/gnis.py \
  backend/tests/test_gnis.py backend/tests/test_migration_0005_0006.py backend/pyproject.toml backend/db/roles/
git commit -m "feat(pipelines): 0005 GNIS summits + duplicate decisions; GNIS DomesticNames loader"
```

---

### Task 7: R3a and R3 geocode repair

**Files:**
- Create: `backend/app/data/repair/geocode.py`, `backend/tests/test_repair_geocode.py`
- Modify: `backend/app/data/repair/__main__.py`, `backend/pyproject.toml`, `backend/tests/verify/test_phase2a_repair.py`

**Interfaces:**
- Consumes: `gnis.Summit`, `gnis.state_code` (Task 6), `textsim.place_key`, `geo.haversine_km` (Task 5), `validate.in_us`, `framework` (Task 1).
- Produces: `R3_VERSION = "r3-v1"`, `MOVE_KM = 50.0`, `FOREIGN_TERMS: dict[str, str]`, `@dataclass(frozen=True) GeoRow(accident_id: int, family: SourceFamily, mountain: str | None, state: str | None, lat: float | None, lon: float | None, mp_route_id: int | None)`, `KEEP` sentinel, `@dataclass(frozen=True) GeoFix(accident_id: int, location: tuple[float, float] | None | KeepType, precision: str, method: str, country: str | None)`, `SummitIndex(summits)` with `.lookup(key: str, state: str | None) -> list[Summit]`, `foreign_country(*texts: str | None) -> str | None`, `find_fallback_points(rows: Sequence[GeoRow], *, min_rows: int = 5, min_names: int = 3) -> set[tuple[float, float]]`, `geocode_fix(row: GeoRow, index: SummitIndex, fallback: set[tuple[float, float]]) -> GeoFix`, `changes_for(fix: GeoFix) -> list[Change]`, CLI steps `r3`, `r3-clusters`.

- [ ] **Step 1: Failing tests**

`backend/tests/test_repair_geocode.py`:

```python
from app.data.repair.geocode import (
    KEEP,
    GeoRow,
    SummitIndex,
    changes_for,
    find_fallback_points,
    foreign_country,
    geocode_fix,
)
from app.pipelines.gnis import Summit

RAINIER = Summit(10, "Mount Rainier", "rainier", "WA", 46.853, -121.760)
DENALI = Summit(11, "Denali", "denali", "AK", 63.069, -151.007)
BALDY_CO = Summit(12, "Bald Mountain", "bald", "CO", 39.4, -105.9)
BALDY_OR = Summit(13, "Bald Mountain", "bald", "OR", 44.6, -122.1)
INDEX = SummitIndex([RAINIER, DENALI, BALDY_CO, BALDY_OR])


def row(i=1, family="aac", mountain=None, state=None, lat=None, lon=None, mp=None):
    return GeoRow(i, family, mountain, state, lat, lon, mp)


def test_r3a_moves_a_city_centroid_to_its_summit():
    fix = geocode_fix(row(mountain="Mt. Rainier", state="WA", lat=47.6, lon=-122.2), INDEX, set())
    assert (fix.location, fix.precision, fix.method, fix.country) == ((46.853, -121.760), "area", "gnis_summit", "US")


def test_mckinley_alias_resolves_to_denali():
    fix = geocode_fix(row(mountain="Mount McKinley", lat=37.8, lon=-122.0), INDEX, set())
    assert fix.location == (63.069, -151.007)


def test_r3a_leaves_a_close_row_to_r3():
    fix = geocode_fix(row(mountain="Mount Rainier", state="WA", lat=46.85, lon=-121.75), INDEX, set())
    assert (fix.location, fix.precision, fix.method) == (KEEP, "area", "source_geocode")


def test_ambiguous_name_far_from_all_candidates_is_nulled():
    fix = geocode_fix(row(mountain="Bald Mountain", lat=34.0, lon=-118.2), INDEX, set())
    assert (fix.location, fix.precision, fix.method) == (None, "unknown", "gnis_ambiguous")


def test_ambiguous_name_near_one_candidate_is_kept():
    fix = geocode_fix(row(mountain="Bald Mountain", lat=39.41, lon=-105.9), INDEX, set())
    assert fix.location is KEEP


def test_us_names_that_contain_foreign_words_stay_us():
    assert foreign_country("Organ Mountains", "New Mexico") is None
    assert foreign_country("Indian Peaks", "CO") is None


def test_foreign_places_get_a_country_and_no_coordinates():
    assert foreign_country("Aconcagua", None) == "AR"
    fix = geocode_fix(row(mountain="Mount Robson, British Columbia", lat=53.1, lon=-119.2), INDEX, set())
    assert (fix.location, fix.precision, fix.country) == (None, "unknown", "CA")


def test_fallback_cluster_rows_become_region_fallback_with_state():
    rows = [row(i, mountain=f"Place {i}", state="CO", lat=39.739, lon=-104.990) for i in range(6)]
    points = find_fallback_points(rows)
    assert points == {(39.739, -104.99)}
    fix = geocode_fix(rows[0], INDEX, points)
    assert (fix.location, fix.precision, fix.method, fix.country) == (None, "region_fallback", "fallback_state", "US")


def test_alaska_text_below_55n_is_fallback():
    fix = geocode_fix(row(mountain="Some Glacier", state="AK", lat=47.6, lon=-122.3), INDEX, set())
    assert (fix.precision, fix.location) == ("region_fallback", None)


def test_source_defaults():
    assert geocode_fix(row(family="nps", lat=36.5, lon=-118.3), INDEX, set()).precision == "park_centroid"
    assert geocode_fix(row(family="avalanche", lat=39.6, lon=-105.9), INDEX, set()).precision == "exact"
    assert geocode_fix(row(mp=900000001, lat=40.0, lon=-105.3), INDEX, set()).precision == "crag"
    assert geocode_fix(row(lat=None, lon=None), INDEX, set()).method == "no_coords"


def test_changes_skip_location_when_kept():
    fix = geocode_fix(row(family="avalanche", lat=39.6, lon=-105.9), INDEX, set())
    assert [c.field for c in changes_for(fix)] == ["geocode_precision", "geocode_method", "country"]
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `backend/app/data/repair/geocode.py`:

```python
"""R3a (city/park-centroid misgeocodes against GNIS summits) then R3 (fallback clusters,
foreign places, Alaska text below 55°N, per-source precision labels)."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import LOCATION, Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.geo import haversine_km
from app.pipelines.gnis import Summit, state_code
from app.pipelines.textsim import normalize_text, place_key
from app.pipelines.validate import ValidationReport, in_us

R3_VERSION = "r3-v1"
MOVE_KM = 50.0
ALASKA_MIN_LAT = 55.0
ALIASES: dict[str, tuple[str, ...]] = {"mckinley": ("denali",), "denali": ("mckinley",)}
# Terms that are also US place names are left out on purpose ("Ontario Peak" CA,
# "Patagonia" AZ, "Norway" ME, "China Peak" CA); "new mexico" is stripped before matching.
FOREIGN_TERMS: dict[str, str] = {
    "canada": "CA", "british columbia": "CA", "alberta": "CA", "yukon": "CA", "robson": "CA",
    "squamish": "CA", "bugaboos": "CA", "mexico": "MX", "el potrero chico": "MX", "orizaba": "MX",
    "nepal": "NP", "everest": "NP", "ama dablam": "NP", "pakistan": "PK", "k2": "PK", "peru": "PE",
    "huascaran": "PE", "argentina": "AR", "aconcagua": "AR", "fitz roy": "AR", "chile": "CL", "bolivia": "BO",
    "ecuador": "EC", "chimborazo": "EC", "cotopaxi": "EC", "tibet": "CN", "tanzania": "TZ",
    "kilimanjaro": "TZ", "switzerland": "CH", "matterhorn": "CH", "chamonix": "FR", "mont blanc": "FR",
    "dolomites": "IT", "new zealand": "NZ", "scotland": "GB", "iceland": "IS", "kyrgyzstan": "KG",
    "elbrus": "RU",
}


class KeepType(Enum):
    KEEP = "keep"


KEEP = KeepType.KEEP


@dataclass(frozen=True)
class GeoRow:
    accident_id: int
    family: SourceFamily
    mountain: str | None
    state: str | None
    lat: float | None
    lon: float | None
    mp_route_id: int | None


@dataclass(frozen=True)
class GeoFix:
    accident_id: int
    location: tuple[float, float] | None | KeepType
    precision: str
    method: str
    country: str | None


class SummitIndex:
    def __init__(self, summits: Sequence[Summit]) -> None:
        self._by_key: dict[str, list[Summit]] = defaultdict(list)
        for s in summits:
            self._by_key[s.name_key].append(s)

    def lookup(self, key: str, state: str | None) -> list[Summit]:
        hits = [s for k in (key, *ALIASES.get(key, ())) for s in self._by_key.get(k, [])]
        return [s for s in hits if s.state_code == state] if state else hits


def foreign_country(*texts: str | None) -> str | None:
    haystack = f" {' '.join(normalize_text(t) for t in texts if t)} ".replace(" new mexico ", " ")
    for term, code in FOREIGN_TERMS.items():
        if f" {term} " in haystack:
            return code
    return None


def _point(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 3), round(lon, 3)


def find_fallback_points(rows: Sequence[GeoRow], *, min_rows: int = 5, min_names: int = 3) -> set[tuple[float, float]]:
    names: dict[tuple[float, float], set[str]] = defaultdict(set)
    counts: dict[tuple[float, float], int] = defaultdict(int)
    for r in rows:
        if r.lat is None or r.lon is None:
            continue
        p = _point(r.lat, r.lon)
        counts[p] += 1
        names[p].add(place_key(r.mountain))
    return {p for p, n in counts.items() if n >= min_rows and len(names[p]) >= min_names}


def _r3a(row: GeoRow, index: SummitIndex, state: str | None) -> GeoFix | None:
    key = place_key(row.mountain)
    if not key or row.lat is None or row.lon is None:
        return None
    hits = index.lookup(key, state)
    if len(hits) == 1:
        s = hits[0]
        if haversine_km(row.lat, row.lon, s.lat, s.lon) > MOVE_KM:
            return GeoFix(row.accident_id, (s.lat, s.lon), "area", "gnis_summit", "US")
        return None
    if len(hits) > 1 and state is None:
        if all(haversine_km(row.lat, row.lon, s.lat, s.lon) > MOVE_KM for s in hits):
            return GeoFix(row.accident_id, None, "unknown", "gnis_ambiguous", None)
    return None


def geocode_fix(row: GeoRow, index: SummitIndex, fallback: set[tuple[float, float]]) -> GeoFix:
    state = state_code(row.state)
    foreign = foreign_country(row.mountain, row.state)
    if foreign is not None:
        return GeoFix(row.accident_id, None, "unknown", "foreign_place", foreign)
    moved = _r3a(row, index, state)
    if moved is not None:
        return moved
    lat, lon = row.lat, row.lon
    if lat is None or lon is None:
        return GeoFix(row.accident_id, KEEP, "unknown", "no_coords", "US" if state else None)
    if _point(lat, lon) in fallback or (state == "AK" and lat < ALASKA_MIN_LAT):
        hits = index.lookup(place_key(row.mountain), state) if state else []
        if len(hits) == 1:
            return GeoFix(row.accident_id, (hits[0].lat, hits[0].lon), "area", "gnis_summit", "US")
        if state is not None:
            return GeoFix(row.accident_id, None, "region_fallback", "fallback_state", "US")
        return GeoFix(row.accident_id, None, "unknown", "fallback_no_state", None)
    if not in_us(lat, lon):
        return GeoFix(row.accident_id, None, "unknown", "outside_us", None)
    if row.family == "nps":
        return GeoFix(row.accident_id, KEEP, "park_centroid", "nps_source", "US")
    if row.mp_route_id is not None:
        return GeoFix(row.accident_id, KEEP, "crag", "route_link", "US")
    if row.family == "avalanche":
        return GeoFix(row.accident_id, KEEP, "exact", "source_coords", "US")
    return GeoFix(row.accident_id, KEEP, "area", "source_geocode", "US")


def changes_for(fix: GeoFix) -> list[Change]:
    changes: list[Change] = []
    if not isinstance(fix.location, KeepType):
        changes.append(Change(fix.accident_id, LOCATION, fix.location, fix.method))
    changes += [
        Change(fix.accident_id, "geocode_precision", fix.precision, fix.method),
        Change(fix.accident_id, "geocode_method", fix.method, fix.method),
        Change(fix.accident_id, "country", fix.country, fix.method),
    ]
    return changes


async def _load(conn: AsyncConnection) -> tuple[list[GeoRow], SummitIndex]:
    raw = await fetch_accidents(conn, ["source", "mountain", "state", "latitude", "longitude", "mp_route_id"])
    rows = [
        GeoRow(
            int(str(r["accident_id"])),
            source_family(r["source"] if isinstance(r["source"], str) else None),
            r["mountain"] if isinstance(r["mountain"], str) else None,
            r["state"] if isinstance(r["state"], str) else None,
            float(str(r["latitude"])) if r["latitude"] is not None else None,
            float(str(r["longitude"])) if r["longitude"] is not None else None,
            int(str(r["mp_route_id"])) if r["mp_route_id"] is not None else None,
        )
        for r in raw
    ]
    summits = [
        Summit(int(g), str(n), str(k), str(s), float(la), float(lo))
        for g, n, k, s, la, lo in (await conn.execute(text("SELECT gnis_id, name, name_key, state_code, lat, lon FROM gnis_summits"))).all()
    ]
    if not summits:
        raise SystemExit("gnis_summits is empty: run python -m app.pipelines.gnis first")
    return rows, SummitIndex(summits)


async def run_r3_clusters(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows, _ = await _load(conn)
    points = find_fallback_points(rows)
    sizes = {f"{lat},{lon}": sum(1 for r in rows if r.lat is not None and r.lon is not None and _point(r.lat, r.lon) == (lat, lon)) for lat, lon in points}
    return {"fallback_points": len(points), "rows_at_fallback_points": sum(sizes.values()), "points": sizes}


async def run_r3(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows, index = await _load(conn)
    fallback = find_fallback_points(rows)
    report = ValidationReport("repair:r3")
    changes: list[Change] = []
    for r in rows:
        fix = geocode_fix(r, index, fallback)
        if fix.precision in ("unknown", "region_fallback"):
            report.quarantine(str(r.accident_id), fix.method)
        else:
            report.accept()
        changes += changes_for(fix)
    return await run_step(conn, step="r3", rule_version=R3_VERSION, changes=changes, report=report, apply=args.apply)
```

`run_r3_clusters` prints coordinates of fallback points only (no names or text), for the owner's cluster review before `r3 --apply`.

In `__main__.py`: `from app.data.repair import dates, geocode, severity` and register `"r3": geocode.run_r3, "r3-clusters": geocode.run_r3_clusters`. Append `"app.data.repair.geocode"` to mypy strict.

Append to `backend/tests/verify/test_phase2a_repair.py`:

```python
NAMED = {"denali": "AK", "mckinley": "AK", "rainier": "WA", "hood": "OR", "shasta": "CA", "grand teton": "WY", "washington": "NH"}


def test_r3a_named_peaks_are_near_their_summit():
    from app.pipelines.geo import haversine_km
    from app.pipelines.textsim import place_key

    summits = {(r["name_key"], r["state_code"]): (r["lat"], r["lon"]) for r in fetch(
        "SELECT name_key, state_code, lat, lon FROM gnis_summits WHERE name_key IN "
        "('denali','mckinley','rainier','hood','shasta','grand teton','washington')")}
    far = []
    for r in fetch("SELECT accident_id, mountain, latitude, longitude FROM accidents WHERE latitude IS NOT NULL"):
        key = place_key(r["mountain"])
        if key in NAMED:
            target = summits.get((key, NAMED[key])) or summits.get(("denali", "AK"))
            if target and haversine_km(r["latitude"], r["longitude"], *target) > 50:
                far.append(r["accident_id"])
    assert far == []


def test_r3_no_precise_rows_at_fallback_points_and_no_alaska_text_below_55n():
    [row] = fetch(
        "WITH pts AS (SELECT round(latitude::numeric, 3) la, round(longitude::numeric, 3) lo FROM accidents "
        "WHERE latitude IS NOT NULL GROUP BY 1, 2 HAVING count(*) >= 5 AND count(DISTINCT lower(mountain)) >= 3) "
        "SELECT (SELECT count(*) FROM accidents a JOIN pts ON round(a.latitude::numeric, 3) = pts.la "
        " AND round(a.longitude::numeric, 3) = pts.lo WHERE a.geocode_precision IN ('exact','crag','area')) AS precise_at_fallback, "
        "(SELECT count(*) FROM accidents WHERE state IN ('AK','Alaska') AND latitude < 55) AS ak_below_55"
    )
    assert (row["precise_at_fallback"], row["ak_below_55"]) == (0, 0)
```

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_repair_geocode.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/data/repair/geocode.py backend/app/data/repair/__main__.py backend/tests/test_repair_geocode.py backend/tests/verify/test_phase2a_repair.py backend/pyproject.toml && git commit -m "feat(repair): R3a GNIS summit moves and R3 fallback/foreign/Alaska geocode repair"`

---

### Task 8: R4 activity classes

**Files:**
- Create: `backend/app/data/repair/activity.py`, `backend/tests/test_repair_activity.py`, `data/golden/README.md`, `data/golden/activity_v1.csv` (header only; runbook fills)
- Modify: `backend/app/data/repair/__main__.py`, `backend/pyproject.toml`, `backend/tests/verify/test_phase2a_repair.py`

**Interfaces:**
- Produces: `R4_VERSION = "r4-v1"`, `SKI_APPROACH_RADIUS_M = 1000`, `classify_activity(family: SourceFamily, activity: str | None, text: str, near_climb_terrain: bool) -> tuple[str, str | None] | None` (class, inclusion flag; `None` = unmapped), CLI steps `r4`, `r4-golden-export`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_repair_activity.py`:

```python
import argparse
import asyncio

import pytest

from app.data.repair import activity
from app.data.repair.activity import classify_activity


def test_nps_only_rock_climbing_counts():
    assert classify_activity("nps", "rock_climbing", "", False) == ("climbing", None)
    assert classify_activity("nps", "Rock Climbing", "", False) == ("climbing", None)
    assert classify_activity("nps", "hiking", "", False) == ("non_climbing", None)


def test_avalanche_ice_climbing_is_climbing():
    assert classify_activity("avalanche", "ice_climbing", "", False) == ("climbing", None)


@pytest.mark.parametrize(
    "text,near,expected",
    [
        ("they were on a summit attempt and switched to crampons", True, ("climbing_approach", "ski_approach_best_effort")),
        ("they were on a summit attempt and switched to crampons", False, ("non_climbing", None)),
        ("climbing the skin track to the ridge", True, ("non_climbing", None)),
        ("a day of lift-served skiing", True, ("non_climbing", None)),
    ],
)
def test_ski_rows_need_the_lexicon_and_nearby_climbing_terrain(text, near, expected):
    assert classify_activity("avalanche", "backcountry skiing", text, near) == expected


def test_unmapped_activity_returns_none():
    assert classify_activity("aac", "paragliding", "", False) is None


def test_unmapped_activity_blocks_apply(monkeypatch):
    async def fake_rows(conn):
        return [(1, "aac", "paragliding", "", False)]

    monkeypatch.setattr(activity, "_rows", fake_rows)
    with pytest.raises(SystemExit, match="paragliding"):
        asyncio.run(activity.run_r4(None, argparse.Namespace(apply=True)))  # type: ignore[arg-type]
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/data/repair/activity.py`:

```python
"""R4: versioned activity classes. Ski/snowmobile rows count as climbing approaches only
when the narrative uses climbing vocabulary (uphill-skinning phrasing excluded) and the
point is within 1 km of alpine/ice/mixed terrain; unknown vocabularies stop the run."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.review import export_sample
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.geo import haversine_km_many
from app.pipelines.textsim import normalize_text
from app.pipelines.validate import ValidationReport

R4_VERSION = "r4-v1"
SKI_APPROACH_RADIUS_M = 1000
CLIMBING = frozenset({
    "climbing", "rock climbing", "ice climbing", "mixed climbing", "alpine climbing", "mountaineering",
    "trad climbing", "sport climbing", "bouldering", "top rope", "aid climbing", "ice", "rock", "alpine",
})
SKI_LIKE = frozenset({
    "skiing", "backcountry skiing", "ski touring", "snowboarding", "backcountry snowboarding", "splitboarding",
    "snowmobiling", "snowmobile", "sidecountry skiing", "ski mountaineering",
})
NON_CLIMBING = frozenset({
    "hiking", "snowshoeing", "hunting", "sledding", "snowbiking", "residential", "highway", "other",
    "inbounds skiing", "ranger", "motorized", "walking", "scrambling off trail",
})
CLIMB_LEXICON = ("ice climb", "couloir climb", "summit attempt", "alpine route", "rappel", "crampon")
SKIN_EXCLUSIONS = ("skin track", "skinning", "climbing skins", "on skins", "skinned up")


def classify_activity(
    family: SourceFamily, activity: str | None, text: str, near_climb_terrain: bool
) -> tuple[str, str | None] | None:
    act = normalize_text(activity)
    if family == "nps":
        return ("climbing", None) if act == "rock climbing" else ("non_climbing", None)
    if act in CLIMBING:
        return ("climbing", None)
    if act in SKI_LIKE:
        narrative = normalize_text(text)
        lexicon = any(normalize_text(p) in narrative for p in CLIMB_LEXICON)
        skinning = any(normalize_text(p) in narrative for p in SKIN_EXCLUSIONS)
        if lexicon and not skinning and near_climb_terrain:
            return ("climbing_approach", "ski_approach_best_effort")
        return ("non_climbing", None)
    if act in NON_CLIMBING:
        return ("non_climbing", None)
    return None


async def _rows(conn: AsyncConnection) -> list[tuple[int, SourceFamily, str | None, str, bool]]:
    accidents = await fetch_accidents(conn, ["source", "activity", "description", "tags", "latitude", "longitude"])
    terrain = np.array(
        (await conn.execute(text(
            "SELECT DISTINCT l.latitude, l.longitude FROM mp_routes r JOIN mp_locations l ON l.mp_id = r.location_id "
            "WHERE l.latitude IS NOT NULL AND (r.type ILIKE '%alpine%' OR r.type ILIKE '%ice%' OR r.type ILIKE '%mixed%')"
        ))).all(),
        dtype=np.float64,
    ).reshape(-1, 2)
    out = []
    for r in accidents:
        near = False
        if r["latitude"] is not None and r["longitude"] is not None and len(terrain):
            d = haversine_km_many(float(str(r["latitude"])), float(str(r["longitude"])), terrain[:, 0], terrain[:, 1])
            near = bool((d * 1000 <= SKI_APPROACH_RADIUS_M).any())
        narrative = f"{r['description'] or ''} {r['tags'] or ''}"
        out.append((int(str(r["accident_id"])), source_family(str(r["source"])), r["activity"] if isinstance(r["activity"], str) else None, narrative, near))
    return out


async def run_r4(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await _rows(conn)
    report = ValidationReport("repair:r4")
    changes: list[Change] = []
    unmapped: Counter[str] = Counter()
    for accident_id, family, act, narrative, near in rows:
        result = classify_activity(family, act, narrative, near)
        if result is None:
            unmapped[f"{family}:{normalize_text(act)}"] += 1
            report.quarantine(str(accident_id), "unmapped_activity")
            continue
        report.accept()
        cls, flag = result
        changes += [
            Change(accident_id, "activity_class", cls, "r4"),
            Change(accident_id, "activity_rule_version", R4_VERSION, "r4"),
            Change(accident_id, "inclusion_flag", flag, "r4"),
        ]
    if unmapped and args.apply:
        raise SystemExit(f"unmapped activity values, extend activity.py first: {dict(unmapped)}")
    summary = await run_step(conn, step="r4", rule_version=R4_VERSION, changes=changes, report=report, apply=args.apply)
    return summary | {"unmapped": dict(unmapped)}


async def run_r4_golden_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = [{"accident_id": a, "family": f, "activity": act} for a, f, act, _, _ in await _rows(conn)]
    out = Path(args.out or "../data/review/activity_golden_candidates.csv")
    n = export_sample(rows, n=60, seed=4, path=out, columns=["accident_id", "family", "activity"], decision_column="label")
    return {"exported": n, "path": str(out), "labels": "climbing|climbing_approach|non_climbing"}
```

`data/golden/README.md`:

```markdown
# Golden sets

Hand labels used by the `-m db` acceptance tests. Each file holds `accident_id` plus labels only: no narrative text, place names, or MP data. Labels are made by the owner reading the source record, never by a model.

- `activity_v1.csv` — `accident_id,label` (`climbing|climbing_approach|non_climbing`), 60 rows, R4 must score ≥95%.
- `experience_v1.csv` — `accident_id,exp_years_climbing,exp_stated_level,exp_first_season,guided`, 80 rows, R12 precision ≥95% on filled values.
```

`data/golden/activity_v1.csv`: the single line `accident_id,label`.

In `__main__.py` register `"r4": activity.run_r4, "r4-golden-export": activity.run_r4_golden_export` (import `activity`). Append `"app.data.repair.activity"` to strict mypy.

Append to `backend/tests/verify/test_phase2a_repair.py`:

```python
def golden(name: str) -> list[dict[str, str]]:
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "data" / "golden" / name
    with path.open() as fh:
        return list(csv.DictReader(fh))


def test_r4_golden_set_at_least_95_percent():
    labels = {int(r["accident_id"]): r["label"] for r in golden("activity_v1.csv")}
    assert len(labels) >= 60, "label the golden set first (runbook Task 13)"
    ids = ",".join(str(i) for i in labels)
    got = {r["accident_id"]: r["activity_class"] for r in fetch(f"SELECT accident_id, activity_class FROM accidents WHERE accident_id IN ({ids})")}
    hits = sum(got.get(i) == label for i, label in labels.items())
    assert hits / len(labels) >= 0.95
```

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_repair_activity.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/data/repair/activity.py backend/app/data/repair/__main__.py backend/tests/test_repair_activity.py backend/tests/verify/test_phase2a_repair.py data/golden/ backend/pyproject.toml && git commit -m "feat(repair): R4 versioned activity classes with ski-approach rule"`

---

### Task 9: R5 deduplication with auto-decision bands and owner review

**Files:**
- Create: `backend/app/data/repair/dedupe.py`, `backend/tests/test_repair_dedupe.py`
- Modify: `backend/app/data/repair/__main__.py`, `backend/pyproject.toml`, `backend/tests/verify/test_phase2a_repair.py`

**Interfaces:**
- Consumes: Tasks 1, 5, 6 (`internal.duplicate_decisions`), 7 (R3 must have run).
- Produces: `R5_BASE = "r5-v1"`, `MERGE_AT = 0.90`, `DISTINCT_AT = 0.40`, `@dataclass(frozen=True) DupRow(accident_id: int, family: SourceFamily, date: date | None, lat: float | None, lon: float | None, accident_type: str | None, severity: str | None, place: str, date_precision: str | None, geocode_precision: str | None, exact_key: tuple[object, ...])`, `pair_score(a: DupRow, b: DupRow) -> float`, `candidate_pairs(rows) -> list[tuple[int, int, float]]` (low id first, fuzzy only), `exact_groups(rows) -> list[list[int]]`, `band(score: float) -> Literal["merge", "distinct", "review"]`, `groups(ids: Iterable[int], merges: Iterable[tuple[int, int]]) -> list[list[int]]`, `canonical(members: Sequence[DupRow]) -> int`, `rule_version(decisions: Mapping[tuple[int, int], str]) -> str`, CLI steps `r5` (auto decisions + export review CSV + apply groups), `r5-import`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_repair_dedupe.py`:

```python
from datetime import date

import pytest

from app.data.repair.dedupe import (
    DupRow,
    band,
    candidate_pairs,
    canonical,
    exact_groups,
    groups,
    pair_score,
    rule_version,
)


def r(i, d=date(2010, 7, 1), lat=40.0, lon=-105.0, typ="fall", sev="serious", place="Fixture Peak",
      fam="aac", dp="day", gp="area", key=None):
    return DupRow(i, fam, d, lat, lon, typ, sev, place, dp, gp, key or (i,))


def test_identical_facts_score_1_and_far_apart_rows_are_not_candidates():
    assert pair_score(r(1), r(2)) == 1.0
    rows = [r(1), r(2, d=date(2010, 7, 5)), r(3, lat=40.2)]
    assert [(a, b) for a, b, _ in candidate_pairs(rows)] == []


def test_unknown_components_score_half():
    assert pair_score(r(1, typ=None, sev=None), r(2, typ=None, sev=None)) == pytest.approx(0.85)


def test_bands():
    assert (band(0.95), band(0.40), band(0.41), band(0.89)) == ("merge", "distinct", "review", "review")


def test_exact_groups_are_within_source_only():
    rows = [r(1, key=("k",)), r(2, key=("k",)), r(3, key=("k",), fam="nps")]
    assert exact_groups(rows) == [[1, 2]]


def test_groups_are_transitive():
    assert groups([1, 2, 3, 4], [(1, 2), (2, 3)]) == [[1, 2, 3]]


def test_canonical_precedence_day_then_geocode_then_aac_then_lowest_id():
    assert canonical([r(5, dp="month"), r(9, dp="day")]) == 9
    assert canonical([r(5, gp="region_fallback"), r(9, gp="exact")]) == 9
    assert canonical([r(5, fam="nps"), r(9, fam="aac")]) == 9
    assert canonical([r(9), r(5)]) == 5


def test_new_owner_decision_produces_a_new_rule_version():
    a = rule_version({(1, 2): "merge"})
    b = rule_version({(1, 2): "distinct"})
    assert a != b and a.startswith("r5-v1+") and rule_version({(1, 2): "merge"}) == a
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/data/repair/dedupe.py`:

```python
"""R5: exact duplicates merge automatically; fuzzy pairs (±2 days, ≤5 km) are scored and
auto-decided at ≥0.90 (merge) and ≤0.40 (distinct); only the band between goes to the
owner. The rule version carries a hash of the decision set, so a changed decision is
re-applied instead of being skipped as already recorded."""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.geo import haversine_km
from app.pipelines.textsim import jaro_winkler, normalize_text, place_key
from app.pipelines.validate import ValidationReport

R5_BASE = "r5-v1"
MERGE_AT, DISTINCT_AT = 0.90, 0.40
MAX_DAYS, MAX_KM = 2, 5.0
WEIGHTS = {"date": 0.30, "distance": 0.25, "type": 0.15, "severity": 0.15, "name": 0.15}
GEO_RANK = {"exact": 0, "crag": 1, "area": 2, "park_centroid": 3, "region_fallback": 4, "unknown": 5}
SOURCE_RANK = {"aac": 0, "avalanche": 1, "nps": 2}


@dataclass(frozen=True)
class DupRow:
    accident_id: int
    family: SourceFamily
    date: date | None
    lat: float | None
    lon: float | None
    accident_type: str | None
    severity: str | None
    place: str
    date_precision: str | None
    geocode_precision: str | None
    exact_key: tuple[object, ...]


def _same(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.5
    return 1.0 if normalize_text(a) == normalize_text(b) else 0.0


def _coords(r: DupRow) -> tuple[float, float] | None:
    return (r.lat, r.lon) if r.lat is not None and r.lon is not None else None


def pair_score(a: DupRow, b: DupRow) -> float:
    gap = abs((a.date - b.date).days) if a.date is not None and b.date is not None else MAX_DAYS + 1
    ca, cb = _coords(a), _coords(b)
    dist = haversine_km(ca[0], ca[1], cb[0], cb[1]) if ca is not None and cb is not None else MAX_KM
    name = jaro_winkler(a.place, b.place) if a.place and b.place else 0.5
    score = (
        WEIGHTS["date"] * max(0.0, 1 - gap / (MAX_DAYS + 1))
        + WEIGHTS["distance"] * max(0.0, 1 - dist / MAX_KM)
        + WEIGHTS["type"] * _same(a.accident_type, b.accident_type)
        + WEIGHTS["severity"] * _same(a.severity, b.severity)
        + WEIGHTS["name"] * name
    )
    return round(score, 6)


def band(score: float) -> Literal["merge", "distinct", "review"]:
    if score >= MERGE_AT:
        return "merge"
    if score <= DISTINCT_AT:
        return "distinct"
    return "review"


def exact_groups(rows: Sequence[DupRow]) -> list[list[int]]:
    by_key: dict[tuple[object, ...], list[int]] = defaultdict(list)
    for row in rows:
        by_key[(row.family, *row.exact_key)].append(row.accident_id)
    return sorted(sorted(ids) for ids in by_key.values() if len(ids) > 1)


def candidate_pairs(rows: Sequence[DupRow]) -> list[tuple[int, int, float]]:
    usable: list[tuple[date, tuple[float, float], DupRow]] = []
    for r in rows:
        c = _coords(r)
        if r.date is not None and c is not None:
            usable.append((r.date, c, r))
    usable.sort(key=lambda t: t[0])
    exact = [frozenset(g) for g in exact_groups(rows)]
    pairs = []
    for i, (da, ca, a) in enumerate(usable):
        for db, cb, b in usable[i + 1 :]:
            if (db - da).days > MAX_DAYS:
                break
            if haversine_km(ca[0], ca[1], cb[0], cb[1]) > MAX_KM:
                continue
            lo, hi = sorted((a.accident_id, b.accident_id))
            if any({lo, hi} <= g for g in exact):
                continue
            pairs.append((lo, hi, pair_score(a, b)))
    return sorted(pairs)


def groups(ids: Iterable[int], merges: Iterable[tuple[int, int]]) -> list[list[int]]:
    parent = {i: i for i in ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in merges:
        parent[find(a)] = find(b)
    members: dict[int, list[int]] = defaultdict(list)
    for i in parent:
        members[find(i)].append(i)
    return sorted(sorted(m) for m in members.values() if len(m) > 1)


def canonical(members: Sequence[DupRow]) -> int:
    best = min(
        members,
        key=lambda r: (
            0 if r.date_precision == "day" else 1,
            GEO_RANK.get(r.geocode_precision or "unknown", 5),
            SOURCE_RANK[r.family],
            r.accident_id,
        ),
    )
    return best.accident_id


def rule_version(decisions: Mapping[tuple[int, int], str]) -> str:
    digest = hashlib.sha256("".join(f"{a},{b},{d};" for (a, b), d in sorted(decisions.items())).encode()).hexdigest()
    return f"{R5_BASE}+{digest[:10]}"


async def _rows(conn: AsyncConnection) -> list[DupRow]:
    raw = await fetch_accidents(conn, [
        "source", "date", "latitude", "longitude", "accident_type", "activity", "injury_severity",
        "mountain", "route", "description", "date_precision", "geocode_precision",
    ])
    rows = []
    for r in raw:
        digest = hashlib.md5(str(r["description"] or "").encode(), usedforsecurity=False).hexdigest()
        rows.append(DupRow(
            int(str(r["accident_id"])), source_family(str(r["source"])),
            r["date"] if isinstance(r["date"], date) else None,
            float(str(r["latitude"])) if r["latitude"] is not None else None,
            float(str(r["longitude"])) if r["longitude"] is not None else None,
            r["accident_type"] if isinstance(r["accident_type"], str) else None,
            r["injury_severity"] if isinstance(r["injury_severity"], str) else None,
            place_key(f"{r['mountain'] or ''} {r['route'] or ''}"),
            r["date_precision"] if isinstance(r["date_precision"], str) else None,
            r["geocode_precision"] if isinstance(r["geocode_precision"], str) else None,
            (r["date"], r["latitude"], r["longitude"], r["accident_type"], r["activity"], r["injury_severity"],
             r["mountain"], r["route"], digest),
        ))
    return rows


async def _require_r3(conn: AsyncConnection) -> None:
    n = (await conn.execute(text("SELECT count(*) FROM internal.accident_revisions WHERE rule_version = 'r3-v1'"))).scalar_one()
    if not n:
        raise SystemExit("R5 runs after R1–R3: apply r2r1 and r3 first")


async def run_r5(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    await _require_r3(conn)
    rows = await _rows(conn)
    by_id = {r.accident_id: r for r in rows}
    owner = {
        (int(a), int(b)): str(d)
        for a, b, d in (await conn.execute(text(
            "SELECT low_id, high_id, decision FROM internal.duplicate_decisions WHERE decided_by = 'owner'"))).all()
    }
    decided: dict[tuple[int, int], tuple[str, float, str]] = {}  # pair -> (decision, score, by)
    review: list[tuple[int, int, float]] = []
    for group in exact_groups(rows):
        for other in group[1:]:
            decided[(group[0], other)] = ("merge", 1.0, "auto")
    for lo, hi, score in candidate_pairs(rows):
        verdict = band(score)
        if (lo, hi) in owner:
            decided[(lo, hi)] = (owner[(lo, hi)], score, "owner")
        elif verdict == "review":
            review.append((lo, hi, score))
        else:
            decided[(lo, hi)] = (verdict, score, "auto")
    if args.apply:
        for (lo, hi), (d, score, by) in decided.items():
            if by == "owner":
                continue
            await conn.execute(text(
                "INSERT INTO internal.duplicate_decisions (low_id, high_id, score, decision, decided_by, rule_version) "
                "VALUES (:lo, :hi, :s, :d, 'auto', :v) ON CONFLICT (low_id, high_id) DO UPDATE SET "
                "score = EXCLUDED.score, decision = EXCLUDED.decision, rule_version = EXCLUDED.rule_version "
                "WHERE internal.duplicate_decisions.decided_by = 'auto'"),
                {"lo": lo, "hi": hi, "s": score, "d": d, "v": R5_BASE})
    decisions = {pair: d for pair, (d, _, _) in decided.items()}
    if review:
        out = Path(args.out or "../data/review/duplicates.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["low_id", "high_id", "score", "date_a", "date_b", "type_a", "type_b", "severity_a", "severity_b", "place_a", "place_b", "decision"])
            for lo, hi, score in review:
                a, b = by_id[lo], by_id[hi]
                w.writerow([lo, hi, score, a.date, b.date, a.accident_type, b.accident_type, a.severity, b.severity, a.place, b.place, ""])
    merges = [pair for pair, d in decisions.items() if d == "merge"]
    changes: list[Change] = []
    report = ValidationReport("repair:r5")
    for group in groups(by_id, merges):
        head = canonical([by_id[i] for i in group])
        for member in group:
            changes += [
                Change(member, "incident_group_id", head, "r5"),
                Change(member, "is_canonical", member == head, "r5"),
            ]
            report.accept()
    version = rule_version(decisions)
    summary = await run_step(conn, step="r5", rule_version=version, changes=changes, report=report, apply=args.apply)
    return summary | {"auto_decisions": len(decisions), "review_pairs": len(review), "groups": len(groups(by_id, merges))}


async def run_r5_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    # Keyed by the pair, not by one id: a low id can sit in several review pairs, so the
    # single-key review.read_decisions does not fit here.
    path = Path(args.review_file)
    with path.open(newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))
    decided: dict[tuple[int, int], tuple[str, float]] = {}
    for number, row in enumerate(raw, start=1):
        value = (row.get("decision") or "").strip().lower()
        if value not in ("merge", "distinct"):
            raise ValueError(f"{path.name} row {number}: decision must be merge or distinct, got {value!r}")
        decided[(int(row["low_id"]), int(row["high_id"]))] = (value, float(row["score"]))
    for (lo, hi), (d, score) in decided.items():
        await conn.execute(text(
            "INSERT INTO internal.duplicate_decisions (low_id, high_id, score, decision, decided_by, rule_version) "
            "VALUES (:lo, :hi, :s, :d, 'owner', :v) ON CONFLICT (low_id, high_id) DO UPDATE SET "
            "decision = EXCLUDED.decision, decided_by = 'owner', decided_at = now()"),
            {"lo": lo, "hi": hi, "s": score, "d": d, "v": R5_BASE})
    return {"imported": len(decided)}
```

Register `"r5": dedupe.run_r5, "r5-import": dedupe.run_r5_import` in `__main__.py`. Append `"app.data.repair.dedupe"` to strict mypy.

Append to `backend/tests/verify/test_phase2a_repair.py`:

```python
def test_r5_no_exact_duplicates_among_canonical_rows():
    [row] = fetch(
        "SELECT count(*) AS n FROM (SELECT source, date, latitude, longitude, accident_type, activity, injury_severity, "
        "mountain, route, md5(coalesce(description, '')) FROM accidents WHERE is_canonical "
        "GROUP BY 1,2,3,4,5,6,7,8,9,10 HAVING count(*) > 1) d"
    )
    assert row["n"] == 0


def test_r5_owner_review_band_fully_decided():
    [row] = fetch("SELECT count(*) AS n FROM internal.duplicate_decisions WHERE decision NOT IN ('merge','distinct')")
    assert row["n"] == 0
```

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_repair_dedupe.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/data/repair/dedupe.py backend/app/data/repair/__main__.py backend/tests/test_repair_dedupe.py backend/tests/verify/test_phase2a_repair.py backend/pyproject.toml && git commit -m "feat(repair): R5 dedupe with auto bands, owner review CSV, canonical precedence"`

---

### Task 10: R12 structured experience facts

**Files:**
- Create: `backend/app/data/repair/experience.py`, `backend/tests/test_repair_experience.py`, `data/golden/experience_v1.csv` (header only)
- Modify: `backend/app/data/repair/__main__.py`, `backend/pyproject.toml`, `backend/tests/verify/test_phase2a_repair.py`

**Interfaces:**
- Produces: `R12_VERSION = "r12-v1"`, `@dataclass(frozen=True) Experience(years: int | None, level: str, first_season: bool | None, guided: str)`, `extract_experience(text: str | None) -> Experience`, CLI steps `r12`, `r12-golden-export`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_repair_experience.py`:

```python
from app.data.repair.experience import Experience, extract_experience


def test_years_of_climbing():
    assert extract_experience("She had 12 years of climbing experience.").years == 12
    assert extract_experience("with five years climbing").years == 5
    assert extract_experience("the 12 year old route").years is None


def test_levels_and_conflicts():
    assert extract_experience("An experienced leader").level == "experienced"
    assert extract_experience("a beginner on his second trip").level == "novice"
    assert extract_experience("an experienced leader with a novice partner").level == "unknown"


def test_first_season_and_guided():
    assert extract_experience("It was her first season of ice climbing.").first_season is True
    assert extract_experience("The client was roped to the guide.").guided == "guided"


def test_negated_forms_do_not_match_the_positive_class():
    e = extract_experience("The inexperienced pair were unguided and went without a guide.")
    assert (e.level, e.guided) == ("novice", "unguided")


def test_nothing_stated_is_unknown_not_a_guess():
    assert extract_experience(None) == Experience(None, "unknown", None, "unknown")
    assert extract_experience("They fell on the descent.") == Experience(None, "unknown", None, "unknown")
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/data/repair/experience.py`:

```python
"""R12: experience facts stated in the record, by versioned regex. Unstated stays
NULL/unknown; two conflicting statements resolve to unknown (precision over recall).
Scoring never requires these columns."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.review import export_sample
from app.pipelines.validate import ValidationReport

R12_VERSION = "r12-v1"
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_NUM = r"(\d{1,2}|" + "|".join(_WORDS) + r")"
YEARS = re.compile(_NUM + r"\+?\s+years?\s+(?:of\s+)?(?:\w+\s+)?(?:climbing|mountaineering|experience)", re.I)
LEVELS = {
    "novice": re.compile(r"\b(?:novice|beginner|inexperienced)\b", re.I),
    "expert": re.compile(r"\bexpert\b", re.I),
    "experienced": re.compile(r"(?<!in)\bexperienced\b", re.I),
}
FIRST = re.compile(r"\bfirst\s+(?:season|year)\b", re.I)
UNGUIDED = re.compile(r"\b(?:unguided|independent(?:ly)?|without\s+a\s+guide)\b", re.I)
GUIDED = re.compile(r"\b(?:guided|client|clients)\b|\bthe\s+guide\b", re.I)


@dataclass(frozen=True)
class Experience:
    years: int | None
    level: str
    first_season: bool | None
    guided: str


def extract_experience(text: str | None) -> Experience:
    if not text:
        return Experience(None, "unknown", None, "unknown")
    numbers = {int(m) if m.isdigit() else _WORDS[m.lower()] for m in (g.group(1) for g in YEARS.finditer(text))}
    years = numbers.pop() if len(numbers) == 1 else None
    if years is not None and not 0 <= years <= 80:
        years = None
    levels = {name for name, pattern in LEVELS.items() if pattern.search(text)}
    level = levels.pop() if len(levels) == 1 else "unknown"
    first = True if FIRST.search(text) else None
    if UNGUIDED.search(text):
        guided = "unguided"
    elif GUIDED.search(text):
        guided = "guided"
    else:
        guided = "unknown"
    return Experience(years, level, first, guided)


async def run_r12(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["description"])
    report = ValidationReport("repair:r12")
    changes: list[Change] = []
    for r in rows:
        a = int(str(r["accident_id"]))
        e = extract_experience(r["description"] if isinstance(r["description"], str) else None)
        if e == Experience(None, "unknown", None, "unknown"):
            report.quarantine(str(a), "nothing_stated")
        else:
            report.accept()
        changes += [
            Change(a, "exp_years_climbing", e.years, "r12"),
            Change(a, "exp_stated_level", e.level, "r12"),
            Change(a, "exp_first_season", e.first_season, "r12"),
            Change(a, "guided", e.guided, "r12"),
            Change(a, "exp_rule_version", R12_VERSION, "r12"),
        ]
    return await run_step(conn, step="r12", rule_version=R12_VERSION, changes=changes, report=report, apply=args.apply)


async def run_r12_golden_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["source"])
    out = Path(args.out or "../data/review/experience_golden_candidates.csv")
    n = export_sample(rows, n=80, seed=12, path=out, columns=["accident_id"], decision_column="exp_years_climbing")
    return {"exported": n, "path": str(out), "columns_to_fill": "exp_years_climbing,exp_stated_level,exp_first_season,guided"}
```

The "nothing_stated" quarantine entries are counts for the report; R12 still writes the explicit `unknown`s.

`data/golden/experience_v1.csv`: the single line `accident_id,exp_years_climbing,exp_stated_level,exp_first_season,guided`.

Register `"r12": experience.run_r12, "r12-golden-export": experience.run_r12_golden_export`. Append `"app.data.repair.experience"` to strict mypy.

Append to the verify file:

```python
def test_r12_golden_precision_on_filled_values():
    gold = {int(r["accident_id"]): r for r in golden("experience_v1.csv")}
    assert len(gold) >= 80, "label the golden set first (runbook Task 13)"
    ids = ",".join(str(i) for i in gold)
    rows = fetch(f"SELECT accident_id, exp_years_climbing, exp_stated_level, exp_first_season, guided FROM accidents WHERE accident_id IN ({ids})")
    filled = correct = 0
    for r in rows:
        g = gold[r["accident_id"]]
        for col, missing in (("exp_years_climbing", None), ("exp_stated_level", "unknown"), ("exp_first_season", None), ("guided", "unknown")):
            if r[col] != missing:
                filled += 1
                correct += str(r[col]).lower() == g[col].strip().lower()
    print({"filled": filled, "correct": correct})
    assert filled == 0 or correct / filled >= 0.95
```

- [ ] **Step 4: Run** — PASS on `uv run pytest tests/test_repair_experience.py -q`, mypy, ruff.
- [ ] **Step 5: Commit** — `git add backend/app/data/repair/experience.py backend/app/data/repair/__main__.py backend/tests/test_repair_experience.py backend/tests/verify/test_phase2a_repair.py data/golden/experience_v1.csv backend/pyproject.toml && git commit -m "feat(repair): R12 stated experience facts by versioned rules"`

---

### Task 11: `accidents_clean` views (migration `0006`), grants, docs

**Files:**
- Create: `backend/alembic/versions/0006_accidents_clean.py`, `backend/tests/test_migration_0006.py`
- Modify: `backend/db/roles/grants_phase2.sql`, `backend/tests/verify/test_phase2a_repair.py`, `CHANGELOG.md`, `data/DATABASE_STRUCTURE.md`, `CLAUDE.md`

**Interfaces:**
- Produces: views `public.accidents_clean` (columns: `accident_id, source, source_id, date, year, date_precision, year_lo, year_hi, state, country, mountain, route, latitude, longitude, coordinates, elevation_meters, geocode_precision, accident_type, activity, activity_class, inclusion_flag, injury_severity, severity_scale, incident_group_id, mp_route_id, exp_years_climbing, exp_stated_level, exp_first_season, guided, source_url`) and `public.accidents_clean_daily` (same, `date_precision = 'day'`). Phase 3 reads these; later plans' loaders (plan 3 R11) fill the same columns.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0006.py`:

```python
import asyncio

import asyncpg

from tests.pgtest import migrated_db, pg_url, requires_pg

pytestmark = requires_pg

SEED = """
INSERT INTO accidents (accident_id, source, date, is_canonical, activity_class, country, excluded_reason, date_precision) VALUES
  (1, 'AAC', '2010-07-02', true,  'climbing',          'US', NULL,              'day'),
  (2, 'AAC', '2010-07-15', true,  'climbing_approach', 'US', NULL,              'month'),
  (3, 'AAC', '2010-07-02', false, 'climbing',          'US', NULL,              'day'),
  (4, 'AAC', '2010-07-02', true,  'non_climbing',      'US', NULL,              'day'),
  (5, 'AAC', '2010-07-02', true,  'climbing',          'CA', NULL,              'day'),
  (6, 'AAC', '2023-07-02', true,  'climbing',          'US', 'year_unverified', 'unknown');
"""


def _ids(name: str, sql: str) -> list[int]:
    async def go() -> list[int]:
        conn = await asyncpg.connect(pg_url(name))
        try:
            return [r[0] for r in await conn.fetch(sql)]
        finally:
            await conn.close()

    return asyncio.run(go())


def test_0006_clean_views_apply_every_rule_and_hide_narratives():
    with migrated_db("head", SEED) as name:
        assert _ids(name, "SELECT accident_id FROM accidents_clean ORDER BY 1") == [1, 2]
        assert _ids(name, "SELECT accident_id FROM accidents_clean_daily ORDER BY 1") == [1]
        cols = _ids(name, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'accidents_clean' "
                          "AND column_name IN ('description', 'tags', 'age_range')")
        assert cols == [0]
```

- [ ] **Step 2: Run to verify failure** — FAIL (`relation "accidents_clean" does not exist`).

- [ ] **Step 3: Implement** `backend/alembic/versions/0006_accidents_clean.py`:

```python
"""accidents_clean (Phase 3's incident definition) and accidents_clean_daily (the
day-precision set CM trains on). No narrative columns: the views are what trainer reads."""

from alembic import op

revision = "0006_accidents_clean"
down_revision = "0005_gnis_and_duplicate_decisions"
branch_labels = None
depends_on = None

COLUMNS = (
    "accident_id, source, source_id, date, year, date_precision, year_lo, year_hi, state, country, mountain, "
    "route, latitude, longitude, coordinates, elevation_meters, geocode_precision, accident_type, activity, "
    "activity_class, inclusion_flag, injury_severity, severity_scale, incident_group_id, mp_route_id, "
    "exp_years_climbing, exp_stated_level, exp_first_season, guided, source_url"
)


def upgrade() -> None:
    op.execute(
        f"CREATE VIEW accidents_clean AS SELECT {COLUMNS} FROM accidents "
        "WHERE is_canonical AND activity_class IN ('climbing', 'climbing_approach') "
        "AND country = 'US' AND excluded_reason IS NULL"
    )
    op.execute("CREATE VIEW accidents_clean_daily AS SELECT * FROM accidents_clean WHERE date_precision = 'day'")


def downgrade() -> None:
    op.execute("DROP VIEW accidents_clean_daily")
    op.execute("DROP VIEW accidents_clean")
```

`grants_phase2.sql`: `GRANT SELECT ON public.accidents_clean, public.accidents_clean_daily TO trainer;` (the `app` role already gets SELECT through `migrator`'s default privileges).

Append to the verify file:

```python
def test_clean_set_has_no_unverified_or_non_us_rows():
    [row] = fetch(
        "SELECT count(*) FILTER (WHERE date_precision = 'unknown') AS unknown, "
        "count(*) FILTER (WHERE country IS DISTINCT FROM 'US') AS foreign FROM accidents_clean"
    )
    assert (row["unknown"], row["foreign"]) == (0, 0)


def test_clean_daily_is_day_only():
    [row] = fetch("SELECT count(*) FILTER (WHERE date_precision <> 'day') AS n FROM accidents_clean_daily")
    assert row["n"] == 0
```

Docs: `CHANGELOG.md` entry "Phase 2a accident repair, part 2 (PR 2a-2)" listing GNIS summits, R3a/R3, R4, R5 (auto bands + owner review), R12, `accidents_clean`/`accidents_clean_daily`, migrations `0005`/`0006`. `data/DATABASE_STRUCTURE.md`: `gnis_summits`, `internal.duplicate_decisions`, the two views and their rule. `CLAUDE.md` "Database and migrations": revisions list gains `0004`–`0006`; add "Repairs: `uv run python -m app.data.repair <step>` (dry run; `--apply` writes, as `ingest`)."

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_no_scrapers.py` → green.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0006_accidents_clean.py backend/tests/ backend/db/roles/grants_phase2.sql CHANGELOG.md data/DATABASE_STRUCTURE.md CLAUDE.md && git commit -m "feat(db): 0006 accidents_clean and accidents_clean_daily views"`

---

### Task 12: OWNER/AGENT RUNBOOK — apply PR 2a-1 (R2/R1, R9) on a branch, hand checks, prod

From `/Users/sebastianfrazier/Developer/SafeAscent/backend`, `split_pg_url` defined (foundations Task 8 Step 1). Every repair runs as `ingest` and prints counts only.

- [ ] **Step 1 (owner/agent): New Neon branch `p2a-1-rehearsal` from `main`; apply grants**

```bash
BRANCH_HOST='<p2a-1-rehearsal direct host>'
( set -a; . ./.env.owner; set +a
  split_pg_url "$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  psql "$PG_URL_NOPASS" -X -q -f db/roles/grants_phase2.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles_phase2.sql )
```

Expected: `phase 2 grants applied`, `ALL PHASE 2 ROLE CHECKS PASSED`.

- [ ] **Step 2 (owner/agent): Confirm the source vocabulary** (source_family refuses unknown values)

```bash
( set -a; . ./.env.analyst; set +a
  U="${ANALYST_DATABASE_URL/postgresql+asyncpg:/postgresql:}"; U="${U/ssl=/sslmode=}"
  split_pg_url "$(printf '%s' "$U" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  psql "$PG_URL_NOPASS" -XAt -c "SELECT source, count(*) FROM accidents GROUP BY 1 ORDER BY 1" \
    -c "SELECT count(*) FILTER (WHERE source_id ~ '[0-9]+\s*$') AS parseable, count(*) FROM accidents WHERE source ILIKE 'aac%'" )
```

Expected: every source maps to `aac|avalanche|nps` per `sources.py`; nearly all AAC `source_id`s parseable. If not, stop: the agent extends `source_family` / `source_sort_key` in a reviewed commit.

- [ ] **Step 3 (owner/agent): Dry run, then apply on the branch**

```bash
ING() { ( set -a; . ./.env.ingest; set +a
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.data.repair "$@" ); }
ING r2r1
ING r2r1 --apply
ING r2r1 --apply
ING r9 --apply
```

Expected: dry run shows `proposed` counts (e.g. `date_precision=month` near 1,567 for AAC, `date_precision=year` near 123) and `mode: dry_run`; first apply `applied > 0`; the second apply `applied: 0` with everything `already_recorded` (idempotent).

- [ ] **Step 4 (owner): Hand checks**

```bash
ING r1-export --out ../data/review/r1_year_check.csv
ING r9-export --out ../data/review/severity_check.csv
```

Open both CSVs in a spreadsheet. R1: for each row look up the report in the AAC Publications archive (year and title only) and set `within_one_year` to `yes`/`no`. R9: read the AAC report and set `agree` to `yes`/`no` for the stored severity. Save, then:

```bash
ING r1-import --review-file ../data/review/r1_year_check.csv
ING r9-import --review-file ../data/review/severity_check.csv
```

Expected: `{"agreement": …}` for each. R1 must be ≥0.90; if not, stop (rule change needed). If R9 < 0.90, open a follow-up issue for a rule-based re-extraction (spec R9) — it does not block.

- [ ] **Step 5 (owner/agent): Acceptance cells on the branch**

```bash
( set -a; . ./.env.analyst; set +a
  VERIFY_DATABASE_URL="$(printf '%s' "$ANALYST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#; s#postgresql\+asyncpg:#postgresql:#; s#ssl=#sslmode=#")" \
    uv run pytest -m db tests/verify/test_phase2a_repair.py -q -k "r1 or r2 or r9 or every_row" -s )
```

Expected: all pass; printed counts recorded in the PR.

- [ ] **Step 6 (owner): Prod** — repeat Steps 1, 3, 5 with the prod hosts (no `sed`). The hand-check imports are re-run against prod with the same CSVs (the sampled accident ids are identical because seeds and data match). Delete the branch.

---

### Task 13: OWNER/AGENT RUNBOOK — apply PR 2a-2 (GNIS, R3a/R3, R4, R5, R12, views)

- [ ] **Step 1 (owner/agent): Branch `p2a-2-rehearsal`, migrate, grants** — as foundations Task 9 Step 2 (migrator `alembic upgrade head` → `0006_accidents_clean (head)`, `alembic check` clean), then `grants_phase2.sql` and both verify scripts.

- [ ] **Step 2 (owner/agent): Download and load GNIS** (open public-domain file; not scraping)

```bash
curl -fL -o /tmp/gnis_national.zip \
  https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames/DomesticNames_National_Text.zip
unzip -l /tmp/gnis_national.zip | grep -i DomesticNames_National
( set -a; . ./.env.ingest; set +a
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.gnis --file /tmp/gnis_national.zip )
```

If `curl` returns 404, take the current "National … Text" link from https://www.usgs.gov/us-board-on-geographic-names/download-gnis-data. Expected: `"status": "ok"` with roughly 60–80K summits accepted. A header error means the file format changed: stop and update `REQUIRED`.

- [ ] **Step 3 (owner): Review fallback clusters, then R3**

```bash
ING r3-clusters
ING r3
```

Look at the cluster list (coordinates and row counts only): each point should be a city/park centroid, not a real crag. If a real crag appears (e.g. a popular cliff with many distinct route names), tell the agent; `min_names` or an explicit exclusion list changes in a reviewed commit. Then `ING r3 --apply` twice (second: `applied: 0`). Run the verify cells `-k r3`.

- [ ] **Step 4 (owner): R4 vocabulary and golden set**

`ING r4` — if `unmapped` is non-empty, the agent extends the vocabularies in `activity.py` (reviewed commit, new test rows), and you repeat. Then `ING r4-golden-export --out ../data/review/activity_golden_candidates.csv`; label each row `climbing|climbing_approach|non_climbing` by reading the source record; copy **only** `accident_id,label` into `data/golden/activity_v1.csv` (commit it on the PR branch). `ING r4 --apply`, then verify `-k r4`.

- [ ] **Step 5 (owner): R5 with review**

`ING r5 --apply --out ../data/review/duplicates.csv` → writes auto decisions and exports the uncertain band. Fill `decision` (`merge|distinct`) for every row, then `ING r5-import --review-file ../data/review/duplicates.csv` and `ING r5 --apply` again (new rule version because the decision set changed). Spot-check 30 auto-merged pairs: `SELECT low_id, high_id, score FROM internal.duplicate_decisions WHERE decided_by='auto' AND decision='merge' ORDER BY random() LIMIT 30` as analyst, compare the two source records; all 30 must be real duplicates (spec: 100%). Verify `-k r5`.

- [ ] **Step 6 (owner): R12 golden set and apply** — `ING r12-golden-export …`, fill the four columns by reading each record, copy ids + labels into `data/golden/experience_v1.csv`, commit; `ING r12 --apply`; verify `-k r12`.

- [ ] **Step 7 (owner/agent): Full acceptance, then prod** — `uv run pytest -m db tests/verify -q -s` on the branch: all pass. Repeat Steps 1–6 on prod (the review CSV imports and golden files are reused, no relabelling). Delete the branch. Record in the PR: counts per step, R4/R12 golden scores, R5 review size, and that the 2023 AAC check passes.

---

## Self-review

- Spec coverage: R1 (T3, T12), R2 (T2–T3), R9 (T4), R3a/R3 (T6–T7), R4 (T8), R5 (T9), R12 (T10), `accidents_clean` (T11), every verification cell as `-m db` (T4, T7–T11), milestones 2a-1/2a-2 (runbooks T12–T13). R6–R8, R10, R11 are in plans 1, 3, 5.
- Placeholders: the runbook's angle-bracket hosts come from the Console. Golden CSVs are created header-only on purpose; filling them is an owner step whose output is committed.
- Type consistency: `Change`/`apply_changes`/`run_step`/`fetch_accidents` used identically everywhere; CLI steps all have the `Step` signature.
- The R9 hand-check agreement is stored in `validation_report.agreement`, which the verify cells read.
