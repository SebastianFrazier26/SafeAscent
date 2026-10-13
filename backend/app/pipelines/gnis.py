"""USGS GNIS DomesticNames (public domain) → gnis_summits. Reads a downloaded national
file (pipe-delimited, optionally zipped); it never fetches anything. The header is checked so
a format change fails loudly instead of loading shifted columns.

Names are stored exactly as GNIS publishes them. The DomesticNames file carries no alternate
names, and as of the 2026-09-29 file the Alaska summit is listed as "Mount McKinley" (feature
1414314), so alias handling (Denali) belongs to the geocoder, not to a rename here.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import re
import uuid
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import astuple, dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.db import ingest_engine
from app.pipelines.ingest_log import finish_run, last_ok_rows_in, last_ok_sha, sha256_rows, start_run, write_quarantine
from app.pipelines.textsim import place_key
from app.pipelines.validate import ValidationReport, batch_gate, coord_problem

SOURCE = "gnis_summits"
NATIONAL_MEMBER = "DomesticNames_National"
REQUIRED = ("feature_id", "feature_name", "feature_class", "state_name", "prim_lat_dec", "prim_long_dec")
MAX_GNIS_ID = 2**31 - 1
_GNIS_ID = re.compile(r"[0-9]{1,10}")
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
# G20: US territories are outside the accident data's scope (and outside validate.US_BOXES), so
# their summits are skipped and counted like non-Summit rows, not quarantined as defects. On the
# 2026-09-29 national file they are 532 summits; quarantining them with the 459 blank-state rows
# would put the first load at ~1.4%, past the 1% share gate.
TERRITORIES = frozenset(
    {
        "american samoa",
        "commonwealth of the northern mariana islands",
        "guam",
        "puerto rico",
        "united states virgin islands",
    }
)
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
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = [
                n for n in archive.namelist()
                if n.rsplit("/", 1)[-1].startswith(NATIONAL_MEMBER) and n.endswith(".txt")
            ]
            if len(members) != 1:
                raise ValueError(f"{path}: expected one {NATIONAL_MEMBER}*.txt member, found {members}")
            with archive.open(members[0]) as raw:
                yield from io.TextIOWrapper(raw, encoding="utf-8-sig")
    else:
        with path.open(encoding="utf-8-sig") as fh:
            yield from fh


def _gnis_id(value: str) -> int | None:
    if not _GNIS_ID.fullmatch(value):
        return None
    number = int(value)
    return number if 1 <= number <= MAX_GNIS_ID else None


def parse_domestic_names(
    lines: Iterable[str], report: ValidationReport, *, skipped: Counter[str] | None = None
) -> list[Summit]:
    """Summits in the 50 states and DC. Rows that are not summits, and territory summits, are
    out of scope: skipped (counted in `skipped` when given), not quarantined."""
    reader = csv.DictReader((line.rstrip("\r\n") for line in lines), delimiter="|", quoting=csv.QUOTE_NONE)
    missing = set(REQUIRED) - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"GNIS header changed; missing {sorted(missing)}")
    summits: list[Summit] = []
    seen: set[int] = set()
    for row in reader:
        if row["feature_class"] != "Summit":
            continue
        ref = row["feature_id"] or ""
        state = row["state_name"] or ""
        if state.strip().lower() in TERRITORIES:
            if skipped is not None:
                skipped["territory"] += 1
            continue
        gnis_id = _gnis_id(ref)
        if gnis_id is None:
            report.quarantine(ref, "bad_id")
            continue
        if gnis_id in seen:
            report.quarantine(ref, "duplicate_id")
            continue
        name = (row["feature_name"] or "").strip()
        if not name:
            report.quarantine(ref, "missing_name")
            continue
        code = state_code(state)
        if code is None:
            report.quarantine(ref, "unknown_state", state=state)
            continue
        try:
            lat, lon = float(row["prim_lat_dec"] or ""), float(row["prim_long_dec"] or "")
        except ValueError:
            report.quarantine(ref, "bad_coords")
            continue
        problem = coord_problem(lat, lon)
        if problem is not None:
            report.quarantine(ref, problem)
            continue
        report.accept()
        seen.add(gnis_id)
        summits.append(Summit(gnis_id, name, place_key(name), code, lat, lon))
    return summits


async def load_summits(conn: AsyncConnection, summits: list[Summit], *, run_id: uuid.UUID) -> int:
    """Make gnis_summits mirror `summits`: upsert every row stamped with run_id, then delete
    rows this run did not stamp (a feature GNIS dropped). Keyed on loaded_run_id rather than
    `gnis_id <> ALL(ids)`, which compares every row against a ~70k-element array."""
    if not summits:
        raise ValueError("refusing to empty gnis_summits: no summits to load")
    await conn.execute(
        text(
            "INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon, loaded_run_id) "
            "VALUES (:gnis_id, :name, :name_key, :state_code, :lat, :lon, :run_id) "
            "ON CONFLICT (gnis_id) DO UPDATE SET name = EXCLUDED.name, name_key = EXCLUDED.name_key, "
            "state_code = EXCLUDED.state_code, lat = EXCLUDED.lat, lon = EXCLUDED.lon, "
            "loaded_run_id = EXCLUDED.loaded_run_id"
        ),
        [
            {"gnis_id": s.gnis_id, "name": s.name, "name_key": s.name_key, "state_code": s.state_code,
             "lat": s.lat, "lon": s.lon, "run_id": run_id}
            for s in summits
        ],
    )
    await conn.execute(
        text("DELETE FROM gnis_summits WHERE loaded_run_id IS DISTINCT FROM :run_id"), {"run_id": run_id}
    )
    return len(summits)


async def main(path: Path) -> dict[str, object]:
    report = ValidationReport(SOURCE)
    skipped: Counter[str] = Counter()
    summits = parse_domestic_names(open_gnis(path), report, skipped=skipped)
    sha = sha256_rows([astuple(s) for s in summits])
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if await last_ok_sha(conn, SOURCE) == sha:
                return {"status": "noop", "rows_upserted": 0}
            previous = await last_ok_rows_in(conn, SOURCE)
            problems = batch_gate(report, previous_rows_in=previous, count_tolerance=0.05, max_quarantine_share=0.01)
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha)
            await write_quarantine(conn, run_id, report)
            if problems:
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=problems)
                return {"status": "rejected", "problems": problems, "report": report.summary(), "skipped": dict(skipped)}
            n = await load_summits(conn, summits, run_id=run_id)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
            return {"status": "ok", "rows_upserted": n, "report": report.summary(), "skipped": dict(skipped)}
    finally:
        await engine.dispose()


def _existing_file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"{value} is not a file")
    return path


def cli() -> None:
    parser = argparse.ArgumentParser(description="Load USGS GNIS summits from a downloaded DomesticNames file")
    parser.add_argument("--file", type=_existing_file, required=True)
    print(json.dumps(asyncio.run(main(parser.parse_args().file)), sort_keys=True))


if __name__ == "__main__":
    cli()
