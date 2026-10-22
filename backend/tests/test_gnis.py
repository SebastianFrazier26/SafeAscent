import ast
import asyncio
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.pipelines.gnis as gnis
from app.pipelines.gnis import STATE_CODES, open_gnis, parse_domestic_names, state_code
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

# The real national file's header (checked against DomesticNames_National.txt of 2026-09-29).
HEADER = (
    "feature_id|feature_name|feature_class|state_name|state_numeric|county_name|county_numeric|map_name|"
    "date_created|date_edited|bgn_type|bgn_authority|bgn_date|prim_lat_dms|prim_long_dms|prim_lat_dec|"
    "prim_long_dec|source_lat_dms|source_long_dms|source_lat_dec|source_long_dec"
)


def _line(fid: str, name: str, cls: str, state: str, lat: str, lon: str) -> str:
    return f"{fid}|{name}|{cls}|{state}|00|C|001|m|01/01/1980|||||N|W|{lat}|{lon}||||"


LINES = [
    HEADER,
    _line("1", "Fixture Peak", "Summit", "Colorado", "40.25", "-105.6"),
    _line("2", "Fixture Lake", "Lake", "Colorado", "40.2", "-105.6"),
    _line("3", "Nowhere Summit", "Summit", "Colorado", "0", "0"),
    _line("4", "Island Summit", "Summit", "Guam", "13.4", "144.7"),
    _line("5", "Blank State Summit", "Summit", "", "0", "0"),
]


def test_only_valid_us_summits_are_kept_and_the_rest_counted():
    report = ValidationReport("gnis_summits")
    summits = parse_domestic_names(LINES, report)
    assert [(s.gnis_id, s.name_key, s.state_code) for s in summits] == [(1, "fixture", "CO")]
    assert report.quarantined == {"outside_us": 1, "unknown_state": 1}


def test_territory_summits_are_out_of_scope_not_defects():
    # G20: measured on the 2026-09-29 national file, territory summits (PR, VI, GU, AS, MP: 532)
    # plus blank-state rows would push the quarantine share past the 1% gate on the first load.
    report = ValidationReport("gnis_summits")
    parse_domestic_names(LINES, report)
    assert report.rows_in == 3
    assert all(issue.row_ref != "4" for issue in report.issues)


def test_bad_ids_names_and_coordinates_are_quarantined_not_raised():
    report = ValidationReport("gnis_summits")
    summits = parse_domestic_names(
        [
            HEADER,
            _line("x7", "Bad Id", "Summit", "Colorado", "40.1", "-105.1"),
            _line("0", "Zero Id", "Summit", "Colorado", "40.1", "-105.1"),
            _line("9999999999", "Huge Id", "Summit", "Colorado", "40.1", "-105.1"),
            _line("8", "  ", "Summit", "Colorado", "40.1", "-105.1"),
            _line("9", "Bad Lat", "Summit", "Colorado", "north", "-105.1"),
            _line("10", "Inf Lat", "Summit", "Colorado", "inf", "-105.1"),
            _line("11", "Good", "Summit", "Utah", "40.1", "-111.1"),
            _line("11", "Good Again", "Summit", "Utah", "40.2", "-111.2"),
        ],
        report,
    )
    assert [s.gnis_id for s in summits] == [11]
    assert report.quarantined == {
        "bad_id": 3, "missing_name": 1, "bad_coords": 1, "not_finite": 1, "duplicate_id": 1,
    }


def test_a_rejected_row_does_not_claim_its_id():
    report = ValidationReport("gnis_summits")
    summits = parse_domestic_names(
        [
            HEADER,
            _line("11", "Bad Lat", "Summit", "Utah", "north", "-111.1"),
            _line("11", "Good", "Summit", "Utah", "40.1", "-111.1"),
            _line("11", "Good Again", "Summit", "Utah", "40.2", "-111.2"),
        ],
        report,
    )
    assert [(s.gnis_id, s.name) for s in summits] == [(11, "Good")]
    assert report.quarantined == {"bad_coords": 1, "duplicate_id": 1}


def test_names_are_kept_as_gnis_publishes_them():
    # GNIS (2026-09-29 file) lists the Alaska summit as "Mount McKinley" and carries no alternate
    # names; the loader stores the official name and leaves alias handling to the geocoder.
    report = ValidationReport("gnis_summits")
    (summit,) = parse_domestic_names(
        [HEADER, _line("1414314", "Mount McKinley", "Summit", "Alaska", "63.0691018", "-151.0062617")], report
    )
    assert (summit.name, summit.name_key, summit.state_code) == ("Mount McKinley", "mckinley", "AK")


def test_header_drift_fails_loudly():
    with pytest.raises(ValueError, match="missing"):
        parse_domestic_names(["feature_id|name|class", "1|x|Summit"], ValidationReport("gnis"))


def test_crlf_lines_parse_like_lf():
    report = ValidationReport("gnis_summits")
    summits = parse_domestic_names([line + "\r\n" for line in LINES], report)
    assert [s.gnis_id for s in summits] == [1]


def test_state_code_accepts_names_and_codes():
    assert state_code("Alaska") == "AK"
    assert state_code(" wa ") == "WA"
    assert state_code("District of Columbia") == "DC"
    assert state_code("British Columbia") is None
    assert state_code(None) is None
    assert len(STATE_CODES) == 51


def test_open_gnis_reads_the_national_member_of_a_zip(tmp_path):
    path = tmp_path / "gnis.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("DomesticNames_National_Text.xml", "<x/>")
        z.writestr("Text/DomesticNames_National.txt", "﻿" + "\r\n".join(LINES))
    lines = list(open_gnis(path))
    assert lines[0].startswith("feature_id|")
    assert [s.gnis_id for s in parse_domestic_names(lines, ValidationReport("g"))] == [1]


def test_open_gnis_reads_a_plain_text_file(tmp_path):
    path = tmp_path / "DomesticNames_National.txt"
    path.write_text("﻿" + "\n".join(LINES), encoding="utf-8")
    assert next(iter(open_gnis(path))).startswith("feature_id|")


def test_open_gnis_refuses_a_zip_without_the_national_file(tmp_path):
    path = tmp_path / "gnis.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Text/DomesticNames_AK.txt", HEADER)
    with pytest.raises(ValueError, match="DomesticNames_National"):
        list(open_gnis(path))


def test_loader_imports_no_network_client():
    tree = ast.parse(Path(gnis.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"httpx", "requests", "urllib", "aiohttp", "http", "socket"}


def _write(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _summits(url: str) -> list[tuple[object, ...]]:
    async def fetch() -> list[tuple[object, ...]]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(text("SELECT gnis_id, name, state_code FROM gnis_summits ORDER BY gnis_id"))
                return [tuple(r) for r in rows.all()]
        finally:
            await engine.dispose()

    return asyncio.run(fetch())


def _clean(n: int, start: int = 100) -> list[str]:
    return [_line(str(i), f"Peak {i}", "Summit", "Utah", "40.1", "-111.1") for i in range(start, start + n)]


@requires_pg
def test_main_loads_replaces_and_noops_on_a_repeat(tmp_path, monkeypatch):
    with migrated_db() as name:
        url = sa_url(name)
        monkeypatch.setattr(gnis, "ingest_engine", lambda: create_async_engine(url))
        first = _write(tmp_path, "a.txt", [HEADER, *_clean(100)])
        out = asyncio.run(gnis.main(first))
        assert (out["status"], out["rows_upserted"]) == ("ok", 100)
        assert asyncio.run(gnis.main(first))["status"] == "noop"

        # A newer file renames one summit and drops another: the table mirrors the file.
        newer = [HEADER, *_clean(99, start=101)]
        newer[1] = _line("101", "Renamed Peak", "Summit", "Utah", "40.1", "-111.1")
        newer.append(_line("100000", "Added Peak", "Summit", "Utah", "40.1", "-111.1"))
        out = asyncio.run(gnis.main(_write(tmp_path, "b.txt", newer)))
        assert (out["status"], out["rows_upserted"]) == ("ok", 100)
        rows = _summits(url)
        assert len(rows) == 100
        assert rows[0] == (101, "Renamed Peak", "UT")
        assert rows[-1] == (100000, "Added Peak", "UT")


@requires_pg
def test_reloading_an_older_file_restores_it(tmp_path, monkeypatch):
    with migrated_db() as name:
        url = sa_url(name)
        monkeypatch.setattr(gnis, "ingest_engine", lambda: create_async_engine(url))
        a = _write(tmp_path, "a.txt", [HEADER, *_clean(100)])
        b = _write(tmp_path, "b.txt", [HEADER, *_clean(100, start=300)])
        for path in (a, b, a):
            out = asyncio.run(gnis.main(path))
            assert (out["status"], out["rows_upserted"]) == ("ok", 100)
        assert [r[0] for r in _summits(url)] == list(range(100, 200))
        assert asyncio.run(gnis.main(a))["status"] == "noop"


@requires_pg
def test_a_rejected_batch_leaves_the_table_untouched(tmp_path, monkeypatch):
    with migrated_db() as name:
        url = sa_url(name)
        monkeypatch.setattr(gnis, "ingest_engine", lambda: create_async_engine(url))
        assert asyncio.run(gnis.main(_write(tmp_path, "a.txt", [HEADER, *_clean(100)])))["status"] == "ok"
        # 50 rows is a >5% swing from the last ok run's 100.
        out = asyncio.run(gnis.main(_write(tmp_path, "b.txt", [HEADER, *_clean(50, start=500)])))
        assert out["status"] == "rejected"
        assert [r[0] for r in _summits(url)] == list(range(100, 200))
