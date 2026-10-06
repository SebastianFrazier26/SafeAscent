import asyncio
import csv
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import review
from app.pipelines import accident_facts
from app.pipelines.accident_facts import (
    COLUMNS,
    REFRESH_SINCE,
    US_STATES,
    Candidate,
    apply_route_ids,
    insert_new,
    match_route,
    month_gaps,
    parse_facts,
    unresolved_review_rows,
)
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
BASE = {
    "source": "AAC", "source_id": "ANAC-2025-001", "date": "2025-02-10", "date_precision": "day", "state": "CO",
    "mountain": "Fixture Peak", "route": "Fixture Route", "mp_route_id": "", "latitude": "40.25",
    "longitude": "-105.6", "geocode_precision": "crag", "activity": "climbing", "accident_type": "fall",
    "injury_severity": "serious", "exp_years_climbing": "", "exp_stated_level": "unknown", "exp_first_season": "",
    "guided": "unknown", "source_url": "https://publications.americanalpineclub.org/fixture",
    "summary": "Leader fell on the crux pitch.",
}


def _row(**overrides: str) -> dict[str, str]:
    return BASE | overrides


def _csv(tmp_path, *rows: dict[str, str], name: str = "facts.csv", encoding: str = "utf-8"):
    path = tmp_path / name
    with path.open("w", newline="", encoding=encoding) as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _parse(tmp_path, *rows: dict[str, str]):
    report = ValidationReport("t")
    return parse_facts(_csv(tmp_path, *rows), today=TODAY, report=report), report


def test_template_header_is_the_contract():
    from pathlib import Path

    template = Path(__file__).resolve().parents[2] / "data" / "manual" / "accident_facts.template.csv"
    assert template.read_text(encoding="utf-8").strip() == ",".join(COLUMNS)


def test_good_row_parses(tmp_path):
    [row], report = _parse(tmp_path, BASE)
    assert (row.source_id, row.date, row.latitude, row.exp_years_climbing, row.mp_route_id) == (
        "ANAC-2025-001", date(2025, 2, 10), 40.25, None, None)
    assert (report.accepted, dict(report.quarantined)) == (1, {})


def test_excel_bom_header_is_accepted(tmp_path):
    report = ValidationReport("t")
    assert len(parse_facts(_csv(tmp_path, BASE, encoding="utf-8-sig"), today=TODAY, report=report)) == 1


def test_wrong_header_refuses_the_file(tmp_path):
    path = tmp_path / "facts.csv"
    path.write_text("source,source_id\nAAC,1\n")
    with pytest.raises(ValueError, match="header must be exactly"):
        parse_facts(path, today=TODAY, report=ValidationReport("t"))


def test_future_foreign_long_summary_and_http_rows_are_quarantined(tmp_path):
    rows, report = _parse(
        tmp_path,
        _row(date="2026-09-29", source_id="A2"),
        _row(latitude="50.0", longitude="-115.5", source_id="A3"),
        _row(summary="x" * 201, source_id="A4"),
        _row(source_url="http://publications.americanalpineclub.org/fixture", source_id="A5"),
    )
    assert rows == []
    assert report.quarantined == {"future": 1, "outside_us": 1, "invalid": 2}


def test_point_inside_the_coarse_us_box_with_a_foreign_state_is_outside_us(tmp_path):
    # 49.0,-116.0 is inside validate.US_BOXES (southern BC is in the box); the state settles it.
    rows, report = _parse(tmp_path, _row(latitude="49.0", longitude="-116.0", state="BC"))
    assert rows == [] and report.quarantined == {"outside_us": 1}


def test_point_with_an_unmapped_state_is_unknown_country(tmp_path):
    rows, report = _parse(tmp_path, _row(state="Narnia"))
    assert rows == [] and report.quarantined == {"unknown_country": 1}


def test_blank_coordinates_with_a_us_state_are_allowed_as_unknown_geocode(tmp_path):
    [parsed], _ = _parse(tmp_path, _row(latitude="", longitude="", geocode_precision="unknown", state="Colorado"))
    assert parsed.latitude is None and parsed.geocode_precision == "unknown"


def test_blank_coordinates_need_a_us_state(tmp_path):
    blank = {"latitude": "", "longitude": "", "geocode_precision": "unknown"}
    rows, report = _parse(
        tmp_path,
        _row(**blank, state="BC", source_id="B1"),
        _row(**blank, state="Narnia", source_id="B2"),
        _row(latitude="", longitude="", geocode_precision="crag", source_id="B3"),
    )
    assert rows == []
    assert report.quarantined == {"unknown_country": 2, "precision_without_coords": 1}


def test_half_a_coordinate_pair_is_quarantined(tmp_path):
    rows, report = _parse(tmp_path, _row(longitude=""))
    assert rows == [] and report.quarantined == {"missing_coords": 1}


def test_us_states_come_from_the_gnis_list():
    assert {"co", "colorado", "dc", "wy"} <= US_STATES and "bc" not in US_STATES and len(US_STATES) == 102


def test_unmapped_values_are_quarantined_at_parse_not_at_insert(tmp_path):
    rows, report = _parse(
        tmp_path,
        _row(injury_severity="grave", source_id="S1"),
        _row(activity="unknown", source_id="S2"),
        _row(activity="other", source_id="S3"),
        _row(activity="ski mountaineering", source_id="S4"),
        _row(activity="paragliding", source_id="S5"),
        _row(source="Blog", source_id="S6"),
    )
    assert rows == []
    assert report.quarantined == {
        "unmapped_severity": 1, "activity_needs_review": 3, "unmapped_activity": 1, "unmapped_source": 1,
    }


def test_values_longer_than_their_column_are_invalid(tmp_path):
    rows, report = _parse(
        tmp_path,
        _row(source="AAC" + "x" * 48, source_id="L1"),
        _row(mountain="m" * 256, source_id="L2"),
        _row(route="r" * 256, source_id="L3"),
        _row(activity="a" * 101, source_id="L4"),
        _row(accident_type="t" * 101, source_id="L5"),
        _row(injury_severity="s" * 51, source_id="L6"),
        _row(source_id="i" * 101),
        _row(mountain="m" * 255, route="r" * 255, source_id="OK"),
    )
    assert [r.source_id for r in rows] == ["OK"]
    assert report.quarantined == {"invalid": 7}


def test_blank_experience_level_and_guided_take_the_unknown_default(tmp_path):
    [row], report = _parse(tmp_path, _row(exp_stated_level="", guided="", exp_first_season="", accident_type=""))
    assert (row.exp_stated_level, row.guided, row.exp_first_season, row.accident_type) == ("unknown", "unknown", None, None)
    assert report.quarantined == {}


def test_duplicate_and_overlong_rows_are_quarantined(tmp_path):
    path = tmp_path / "facts.csv"
    line = ",".join(BASE[c] for c in COLUMNS)
    path.write_text(",".join(COLUMNS) + "\n" + line + "\n" + line + "\n" + line + ",extra\n")
    report = ValidationReport("t")
    assert len(parse_facts(path, today=TODAY, report=report)) == 1
    assert report.quarantined == {"duplicate_in_file": 1, "invalid": 1}


def test_month_gaps_list_only_closed_empty_months():
    dates = [date(2024, 8, 3), date(2024, 10, 1), date(2024, 12, 2)]
    assert month_gaps(dates, since=REFRESH_SINCE, today=date(2024, 12, 15)) == ["2024-09", "2024-11"]
    assert month_gaps([], since=REFRESH_SINCE, today=date(2024, 8, 31)) == []


def test_route_match_needs_exactly_one_equal_name():
    near = Candidate(1, "Fixture Route", 120.0)
    assert match_route("The Fixture Rte", [near, Candidate(2, "Other Line", 50.0)]).mp_route_id == 1
    twins = match_route("Fixture Route", [near, Candidate(3, "fixture route", 400.0)])
    assert (twins.mp_route_id, twins.reason) == (None, "ambiguous")
    weak = match_route("Northeast Ridge", [Candidate(4, "Northwest Ridge", 10.0)])
    assert (weak.mp_route_id, weak.reason) == (None, "no_name_match")
    assert match_route("Fixture Route", []).reason == "no_candidate"
    assert match_route("", [near]).reason == "no_route_text"
    assert match_route("Ellingwood Arête", [Candidate(5, "Ellingwood Arete", 10.0)]).mp_route_id == 5


ROUTES_SEED = """
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000100, 'Fixture Area', 40.251, -105.601);
INSERT INTO mp_routes (mp_route_id, name, location_id, type, latitude, longitude) VALUES
  (900000001, 'Fixture Route', 900000100, 'Trad', 40.2505, -105.6005),
  (900000002, 'Twin Line', 900000100, 'Trad', 40.2502, -105.6002),
  (900000003, 'Twin Line', NULL, 'Trad', 40.2508, -105.6008),
  (900000004, 'Fixture Route', NULL, 'Trad', 40.30, -105.60),
  (900000005, 'Area Point Route', 900000100, 'Ice', NULL, NULL);
INSERT INTO accidents (accident_id, source, source_id, date) VALUES (800000001, 'AAC', 'LEGACY-1', '2025-03-01');
INSERT INTO internal.accidents_raw (accident_id, source, source_id, date) VALUES (800000001, 'AAC', 'LEGACY-1', '2025-03-01');
"""


async def _with_engine(url, fn):
    engine = create_async_engine(url)
    try:
        return await fn(engine)
    finally:
        await engine.dispose()


@requires_pg
def test_insert_is_new_rows_only(tmp_path):
    async def scenario(engine):
        report = ValidationReport("t")
        rows = parse_facts(_csv(tmp_path, BASE), today=TODAY, report=report)
        async with engine.begin() as conn:
            first = await insert_new(conn, rows, report=report)
        report2 = ValidationReport("t")
        rows2 = parse_facts(_csv(tmp_path, BASE), today=TODAY, report=report2)
        async with engine.begin() as conn:
            second = await insert_new(conn, rows2, report=report2)
            stored = (await conn.execute(text(
                "SELECT activity_class, severity_scale, country, is_canonical, date_precision, tz, mp_route_id, "
                "description, geocode_method, exp_stated_level FROM accidents WHERE source_id = 'ANAC-2025-001'"
            ))).all()
        return first, second, report2, stored

    with migrated_db() as name:
        first, second, report2, stored = asyncio.run(_with_engine(sa_url(name), scenario))
    assert (first, second) == (1, 0)
    assert (report2.rows_in, report2.accepted, dict(report2.quarantined)) == (1, 0, {"already_present": 1})
    assert [tuple(r) for r in stored] == [
        ("climbing", "full", "US", True, "day", "America/Denver", None, "Leader fell on the crux pitch.",
         "facts_csv", "unknown")
    ]


@requires_pg
def test_route_links_only_on_a_unique_strong_match(tmp_path):
    rows_in = [
        _row(source_id="R1", latitude="40.2504", longitude="-105.6004", route="Fixture Route"),
        _row(source_id="R2", latitude="40.2504", longitude="-105.6004", route="Twin Line"),
        _row(source_id="R3", latitude="40.2504", longitude="-105.6004", route="Fixture Rte Direct"),
        _row(source_id="R4", latitude="", longitude="", geocode_precision="unknown"),
        _row(source_id="R5", mp_route_id="900000004"),
        _row(source_id="R6", mp_route_id="123"),
        _row(source_id="R7", latitude="40.2504", longitude="-105.6004", route="Area Point Route"),
        _row(source_id="R8", activity="hiking", route="Twin Line"),
        _row(source_id="R9", geocode_precision="region_fallback", route="Fixture Route"),
    ]

    async def scenario(engine):
        report = ValidationReport("t")
        rows = parse_facts(_csv(tmp_path, *rows_in), today=TODAY, report=report)
        async with engine.begin() as conn:
            n = await insert_new(conn, rows, report=report)
            pending = await unresolved_review_rows(conn)
            linked = dict((await conn.execute(text(
                "SELECT source_id, mp_route_id FROM accidents WHERE source_id LIKE 'R%' ORDER BY 1"))).all())
        return n, report, pending, linked

    with migrated_db(seed_sql=ROUTES_SEED) as name:
        n, report, pending, linked = asyncio.run(_with_engine(sa_url(name), scenario))
    assert n == 8 and report.quarantined == {"unknown_route": 1}
    assert linked == {"R1": 900000001, "R2": None, "R3": None, "R4": None, "R5": 900000004, "R7": 900000005,
                      "R8": None, "R9": None}
    reasons = {str(r["source_id"]): r["reason"] for r in pending}
    assert reasons == {"R2": "ambiguous", "R3": "no_name_match", "R4": "no_coords", "R9": "untrusted_point"}
    twins = next(r for r in pending if r["source_id"] == "R2")
    assert set(str(twins["candidate_ids"]).split(" | ")) >= {"900000002", "900000003"}
    assert "900000004" not in str(twins["candidate_ids"])  # ~5.5 km away, outside the radius
    assert all("summary" not in r and "description" not in r for r in pending)


@requires_pg
def test_main_logs_gaps_writes_the_review_file_and_dry_run_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path / "review")
    facts = _csv(tmp_path, _row(source_id="M1", route="Twin Line", latitude="40.2504", longitude="-105.6004"),
                 _row(source_id="M2", date="2024-09-15"))

    async def scenario(url):
        monkeypatch.setattr(accident_facts, "ingest_engine", lambda: create_async_engine(url))
        dry = await accident_facts.main(facts, today=TODAY, dry_run=True)
        loaded = await accident_facts.main(facts, today=TODAY, dry_run=False)
        again_dry = await accident_facts.main(facts, today=TODAY, dry_run=True)
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                logs = (await conn.execute(text(
                    "SELECT validation_report, rows_upserted FROM source_ingest_log WHERE source = 'accident_facts'"
                ))).all()
        finally:
            await engine.dispose()
        return dry, loaded, again_dry, logs

    with migrated_db(seed_sql=ROUTES_SEED) as name:
        dry, loaded, again_dry, logs = asyncio.run(scenario(sa_url(name)))
    assert (dry["would_insert"], dry["review_pending"]) == (2, 1)
    assert loaded["inserted"] == 2 and loaded["review_pending"] == 1
    assert again_dry["would_insert"] == 0
    assert again_dry["report"]["quarantined"] == {"already_present": 2}
    [(summary, upserted)] = logs
    assert upserted == 2 and summary["review_pending"] == 1
    gap_line = next(p for p in summary["problems"] if p.startswith("post-2024 gap aac"))
    assert "2024-08" in gap_line and "2024-09" not in gap_line and "2025-02" not in gap_line
    review_file = tmp_path / "review" / accident_facts.REVIEW_NAME
    raw = review_file.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    header = raw.decode("utf-8-sig").splitlines()[0].split(",")
    assert header == [*accident_facts.REVIEW_COLUMNS, "note"]
    assert "Leader fell" not in raw.decode("utf-8-sig")


@requires_pg
def test_review_file_is_never_replaced_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path / "review")
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / accident_facts.REVIEW_NAME).write_text("owner notes\n")
    facts = _csv(tmp_path, _row(source_id="W1", route="Twin Line", latitude="40.2504", longitude="-105.6004"))

    async def scenario(url):
        monkeypatch.setattr(accident_facts, "ingest_engine", lambda: create_async_engine(url))
        with pytest.raises(FileExistsError):
            await accident_facts.main(facts, today=TODAY, dry_run=False)
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text("SELECT count(*) FROM accidents WHERE source_id = 'W1'"))).scalar_one()
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=ROUTES_SEED) as name:
        assert asyncio.run(scenario(sa_url(name))) == 0  # the refused write rolled the load back
    assert (tmp_path / "review" / accident_facts.REVIEW_NAME).read_text() == "owner notes\n"


@requires_pg
def test_apply_route_ids_fills_only_null_links_on_r11_rows(tmp_path):
    async def scenario(engine):
        report = ValidationReport("t")
        first = parse_facts(_csv(tmp_path, _row(source_id="A1", route="Twin Line"),
                                 _row(source_id="A2", mp_route_id="900000001")), today=TODAY, report=report)
        async with engine.begin() as conn:
            await insert_new(conn, first, report=report)
        filled = [
            _row(source_id="A1", mp_route_id="900000002"),
            _row(source_id="A2", mp_route_id="900000002"),
            _row(source_id="LEGACY-1", date="2025-03-01", mp_route_id="900000002"),
            _row(source_id="NOT-LOADED", mp_route_id="900000002"),
            _row(source_id="A9", mp_route_id="777"),
        ]
        report2 = ValidationReport("t")
        rows = parse_facts(_csv(tmp_path, *filled, name="filled.csv"), today=TODAY, report=report2)
        async with engine.begin() as conn:
            dry = await apply_route_ids(conn, rows, report=ValidationReport("t"), apply=False)
            unchanged = (await conn.execute(text("SELECT mp_route_id FROM accidents WHERE source_id = 'A1'"))).scalar_one()
            n = await apply_route_ids(conn, rows, report=report2, apply=True, run_id=uuid.uuid4())
            linked = dict((await conn.execute(text(
                "SELECT source_id, mp_route_id FROM accidents WHERE source_id IN ('A1', 'A2', 'LEGACY-1')"))).all())
            audit = (await conn.execute(text(
                "SELECT a.source_id, r.field, r.old_value, r.new_value, r.method, r.rule_version "
                "FROM internal.accident_revisions r JOIN accidents a USING (accident_id)"))).all()
        return dry, unchanged, n, report2, linked, audit

    with migrated_db(seed_sql=ROUTES_SEED) as name:
        dry, unchanged, n, report2, linked, audit = asyncio.run(_with_engine(sa_url(name), scenario))
    assert (dry, unchanged, n) == (1, None, 1)
    assert [tuple(r) for r in audit] == [("A1", "mp_route_id", None, "900000002", "facts_csv", "r11-route-v1")]
    assert linked == {"A1": 900000002, "A2": 900000001, "LEGACY-1": None}
    assert report2.quarantined == {"route_already_set": 1, "not_an_r11_row": 1, "not_loaded": 1, "unknown_route": 1}


def test_insert_columns_match_the_grant_and_the_verify_list_exactly():
    import re
    from pathlib import Path

    roles = Path(__file__).resolve().parents[1] / "db" / "roles"
    grant = re.search(r"GRANT INSERT \(([^)]*)\)\s+ON public\.accidents TO ingest;", (roles / "grants_phase2.sql").read_text())
    verify = re.search(r"a\.attname IN \(([^)]*)\)\s+AS may_insert", (roles / "verify_roles_phase2.sql").read_text())
    assert grant is not None and verify is not None
    granted = [c.strip() for c in grant.group(1).split(",")]
    verified = [c.strip().strip("'") for c in verify.group(1).split(",")]
    assert granted == list(accident_facts.INSERT_COLUMNS) == verified
    assert "accident_id" not in granted


def test_source_case_and_spacing_do_not_make_a_new_key(tmp_path):
    rows, report = _parse(tmp_path, _row(source="AAC"), _row(source=" aac "))
    assert len(rows) == 1 and report.quarantined == {"duplicate_in_file": 1}


@requires_pg
def test_lowercase_source_matches_the_stored_spelling(tmp_path):
    async def scenario(engine):
        report = ValidationReport("t")
        rows = parse_facts(_csv(tmp_path, _row(source="aac", source_id="LEGACY-1", date="2025-03-01"),
                                _row(source="aac", source_id="C1")), today=TODAY, report=report)
        async with engine.begin() as conn:
            n = await insert_new(conn, rows, report=report)
            stored = (await conn.execute(text("SELECT source FROM accidents WHERE source_id = 'C1'"))).scalar_one()
        return n, report, stored

    with migrated_db(seed_sql=ROUTES_SEED) as name:
        n, report, stored = asyncio.run(_with_engine(sa_url(name), scenario))
    assert (n, stored) == (1, "AAC")
    assert report.quarantined == {"already_present": 1}


@requires_pg
def test_gap_log_ignores_year_precision_dates():
    seed = (
        "INSERT INTO accidents (accident_id, source, date, date_precision) VALUES "
        "(800000101, 'AAC', '2024-09-01', 'year'), (800000102, 'AAC', '2024-10-05', 'day'), "
        "(800000103, 'AAC', '2024-12-01', 'month');"
    )

    async def scenario(engine):
        async with engine.connect() as conn:
            return await accident_facts._gap_log(conn, {"aac"}, today=date(2025, 1, 15))

    with migrated_db(seed_sql=seed) as name:
        lines = asyncio.run(_with_engine(sa_url(name), scenario))
    assert lines == ["post-2024 gap aac: 2024-08,2024-09,2024-11"]


@requires_pg
def test_overwritten_review_file_keeps_earlier_unresolved_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path / "review")
    twin = {"route": "Twin Line", "latitude": "40.2504", "longitude": "-105.6004"}
    first = _csv(tmp_path, _row(source_id="X1", **twin), name="first.csv")
    second = _csv(tmp_path, _row(source_id="X2", **twin), name="second.csv")

    async def scenario(url):
        monkeypatch.setattr(accident_facts, "ingest_engine", lambda: create_async_engine(url))
        await accident_facts.main(first, today=TODAY, dry_run=False)
        dry = await accident_facts.main(second, today=TODAY, dry_run=True)
        loaded = await accident_facts.main(second, today=TODAY, dry_run=False, overwrite=True)
        return dry, loaded

    with migrated_db(seed_sql=ROUTES_SEED) as name:
        dry, loaded = asyncio.run(scenario(sa_url(name)))
    assert dry["review_pending"] == loaded["review_pending"] == 2
    with (tmp_path / "review" / accident_facts.REVIEW_NAME).open(encoding="utf-8-sig", newline="") as fh:
        assert sorted(r["source_id"] for r in csv.DictReader(fh)) == ["X1", "X2"]


@requires_pg
def test_writes_wait_for_the_load_lock_and_dry_run_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path / "review")
    facts = _csv(tmp_path, _row(source_id="K1"))
    fill = _csv(tmp_path, _row(source_id="K1", mp_route_id="900000001"), name="fill.csv")

    async def scenario(url):
        monkeypatch.setattr(accident_facts, "ingest_engine", lambda: create_async_engine(url))
        holder = create_async_engine(url)
        try:
            async with holder.connect() as conn:
                await conn.execute(text(accident_facts.LOCK_SQL))
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(accident_facts.main(facts, today=TODAY, dry_run=False), 2)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(
                        accident_facts.main(fill, today=TODAY, dry_run=False, route_ids=True), 2)
                dry = await asyncio.wait_for(accident_facts.main(facts, today=TODAY, dry_run=True), 20)
                await conn.rollback()
            loaded = await accident_facts.main(facts, today=TODAY, dry_run=False)
            async with holder.connect() as conn:
                logs = (await conn.execute(text("SELECT count(*) FROM source_ingest_log"))).scalar_one()
        finally:
            await holder.dispose()
        return dry, loaded, logs

    with migrated_db(seed_sql=ROUTES_SEED) as name:
        dry, loaded, logs = asyncio.run(scenario(sa_url(name)))
    assert dry["would_insert"] == 1 and loaded["inserted"] == 1
    assert logs == 1  # the timed-out runs rolled back whole
