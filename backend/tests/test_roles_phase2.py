"""Prod order, rehearsed: 0003 → create_roles.sql → create_roles_phase2.sql → migrator
upgrades to head → grants_phase2.sql → verify scripts."""

import argparse
import asyncio
import csv
import os
import subprocess
import uuid
from datetime import date

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import activity, dates, dedupe, experience, geocode, review, severity
from app.data.repair.checks import record_check
from app.data.repair.framework import LOCATION, Change, run_step
from app.pipelines import accident_facts, cell_conditions, gnis, localday, mp_tick_aggregates, mp_ticks_quarantine
from app.pipelines.validate import ValidationReport
from tests.pgtest import requires_pg
from tests.test_gnis import HEADER as GNIS_HEADER
from tests.test_gnis import _line as gnis_line
from tests.test_mp_tick_aggregates import _export
from tests.test_migrations import (
    ANALYST_FIXTURE_SQL,
    PASSWORDS,
    PHASE2_PASSWORD_ENV,
    PHASE2_PASSWORDS,
    ROLE_PASSWORD_ENV,
    ROLES_DIR,
    _alembic_cfg_as,
    _as,
    _denied,
    _fetch_row,
    _owner_db,
    _psql,
    _require_psql,
    _role_url,
    fresh_db,  # noqa: F401  (fixture)
    role_cleanup,  # noqa: F401  (fixture)
)

pytestmark = requires_pg
JOB_TODAY = date(2026, 9, 28)
CREATE_PHASE2 = ROLES_DIR / "create_roles_phase2.sql"
GRANTS_PHASE2 = ROLES_DIR / "grants_phase2.sql"
VERIFY_PHASE2 = ROLES_DIR / "verify_roles_phase2.sql"

# M4 (final review): the real R8 job, loader and repair writer run as ingest, so a grant the real SQL needs
# fails here rather than on the first prod run.
JOB_SEED_SQL = """
INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000002, 'Fixture Ice', 900000100, 'Ice');
INSERT INTO mp_ticks (tick_id, route_id, climber_name, tick_date, created_at) VALUES
  (1, '900000002', 'c', '2025-01-04', '2026-02-08 10:00'),
  (2, '900000999', 'c', '2025-01-04', '2026-02-08 10:00');
INSERT INTO accidents (accident_id, source, date, latitude, longitude) VALUES
  (900000001, 'AAC', '2023-05-02', 40.0, -105.0), (900000002, 'NPS', '2010-07-01', 36.5, -118.3);
"""


def _run_real_jobs_as_ingest(ingest_url: str, tmp_path, monkeypatch) -> None:
    sa_ingest = ingest_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    async def r8() -> dict[str, int]:
        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                return await mp_ticks_quarantine.run(conn, today=JOB_TODAY)
        finally:
            await engine.dispose()

    assert asyncio.run(r8()) == {"clean": 1, "orphan_route": 1}
    export = _export(tmp_path, [(900000002, "2025-01", "lead", 3)], [(900000002, 3, 1)])
    monkeypatch.setattr(mp_tick_aggregates, "ingest_engine", lambda: create_async_engine(sa_ingest))
    first = asyncio.run(mp_tick_aggregates.main(export, today=JOB_TODAY, max_quarantine_share=0.1, dry_run=False))
    again = asyncio.run(mp_tick_aggregates.main(export, today=JOB_TODAY, max_quarantine_share=0.1, dry_run=False))
    assert first["status"] == "ok" and first["rows_upserted"] == 2
    assert again["status"] == "noop"

    async def repair() -> dict[str, object]:
        engine = create_async_engine(sa_ingest)
        changes = [
            Change(900000001, "date_precision", "day", "fixture"),
            Change(900000001, LOCATION, None, "fixture"),
            Change(900000002, LOCATION, (36.6, -118.29), "fixture"),
        ]
        try:
            async with engine.begin() as conn:
                return await run_step(
                    conn, step="fixture", rule_version="fixture-v1", changes=changes,
                    report=ValidationReport(source="repair:fixture"), apply=True,
                )
        finally:
            await engine.dispose()

    assert asyncio.run(repair())["applied"] == 3

    async def r2r1() -> dict[str, object]:
        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                return await dates.run_r2r1(conn, argparse.Namespace(apply=True))
        finally:
            await engine.dispose()

    # G15: the accidents_raw reads and the R1/R2 column writes, all as ingest.
    out = asyncio.run(r2r1())
    assert out["year_unverified"] == 1
    assert out["r2"]["applied"] == 1

    async def r9_and_check() -> tuple[dict[str, object], dict[str, object]]:
        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                return (
                    await severity.run_r9(conn, argparse.Namespace(apply=True)),
                    await record_check(conn, "repair:r9-check", {900000001: "yes"}),
                )
        finally:
            await engine.dispose()

    r9, check = asyncio.run(r9_and_check())
    assert (r9["applied"], check["agreement"]) == (2, 1.0)

    # G15: the GNIS loader's upsert and replace-delete, as ingest.
    monkeypatch.setattr(gnis, "ingest_engine", lambda: create_async_engine(sa_ingest))
    summits = tmp_path / "gnis.txt"
    summits.write_text("\n".join([GNIS_HEADER, gnis_line("1", "Fixture Peak", "Summit", "Colorado", "40.25", "-105.6")]))
    assert asyncio.run(gnis.main(summits))["status"] == "ok"
    summits.write_text("\n".join([GNIS_HEADER, gnis_line("2", "Other Peak", "Summit", "Colorado", "40.2", "-105.6")]))
    assert asyncio.run(gnis.main(summits))["rows_upserted"] == 1

    # R3 and its check exports as ingest: snapshot, gnis_summits and revisions reads, the label writes.
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def r3() -> tuple[dict[str, object], ...]:
        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                out = await geocode.run_r3(conn, argparse.Namespace(apply=True))
                exports = argparse.Namespace(out=None, overwrite=False)
                return (
                    out,
                    await geocode.run_r3a_export(conn, exports),
                    await geocode.run_r3_plot_export(conn, exports),
                    await geocode.run_r3_ambiguous_export(conn, exports),
                )
        finally:
            await engine.dispose()

    r3_out, r3a, plot, ambiguous = asyncio.run(r3())
    # The fixture repair above moved both points under an audited rule, so R3 keeps them but
    # cannot vouch for them: labels only, both untrusted.
    assert r3_out["applied"] == 6 and r3_out["methods"] == {"moved_by_other_rule": 2}
    assert (r3a["exported"], plot["strata"]["unknown"], ambiguous["exported"]) == (0, 1, 0)

    # R4 and its exports as ingest: snapshot activity, the MP terrain columns, the class writes.
    async def r4() -> tuple[dict[str, object], ...]:
        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                out = await activity.run_r4(conn, argparse.Namespace(apply=True))
                exports = argparse.Namespace(out=None, overwrite=False)
                return (
                    out,
                    await activity.run_r4_review_export(conn, exports),
                    await activity.run_r4_golden_export(conn, exports),
                )
        finally:
            await engine.dispose()

    r4_out, r4_review, r4_golden = asyncio.run(r4())
    # The seed's snapshot rows carry no activity: both wait for the owner, nothing is guessed.
    assert (r4_out["review_pending"], r4_review["exported"], sum(r4_golden["strata"].values())) == (2, 2, 0)

    # R5 and R12 as ingest (final review 2a-2 minor; G15): R5's snapshot/label reads, its
    # require_r3/require_r4 run-log reads and decisions upsert, R12's narrative read and writes.
    async def r5_r12() -> tuple[dict[str, object], dict[str, object]]:
        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                return (
                    await dedupe.run_r5(conn, argparse.Namespace(apply=True, out=None, overwrite=False)),
                    await experience.run_r12(conn, argparse.Namespace(apply=True)),
                )
        finally:
            await engine.dispose()

    r5_out, r12_out = asyncio.run(r5_r12())
    assert (r5_out["mode"], r5_out["review_pending"], r5_out["path"]) == ("apply", 0, None)
    assert r12_out["mode"] == "apply"

    # Plan 3 Task 3: the conditions upsert as ingest — INSERT, then the ON CONFLICT UPDATE whose
    # precedence WHERE reads the stored row.
    async def conditions() -> list[int]:
        def row(kind: cell_conditions.RecordKind, source: str) -> cell_conditions.DayRow:
            return cell_conditions.DayRow(1, "America/Denver", date(2026, 9, 20), 5.0, -1.0, 0.0, 0.0, 4.0, 9.0,
                                          True, kind, "m", source)

        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                return [
                    await cell_conditions.upsert_rows(conn, [row(kind, source)], run_id=uuid.uuid4())
                    for kind, source in [("stopgap", "open_meteo_forecast"), ("era5", "open_meteo_archive"),
                                         ("forecast", "open_meteo_forecast")]
                ]
        finally:
            await engine.dispose()

    assert asyncio.run(conditions()) == [1, 1, 0]

    # Plan 3 Task 4: the register job as ingest — accidents.tz through its column grant, the clean
    # views, mp_locations through its three-column grant, the registry and the ingest log.
    async def register() -> dict[str, object]:
        engine = create_async_engine(sa_ingest)
        try:
            async with engine.begin() as conn:
                # The repairs above leave no clean trusted point; promote one, as R3/R4 would.
                await conn.execute(text("UPDATE accidents SET activity_class = 'climbing', geocode_precision = 'crag' "
                                        "WHERE accident_id = 900000002"))
            return await localday.register_logged(engine)
        finally:
            await engine.dispose()

    registered = asyncio.run(register())
    assert registered["accident_tz_changed"] == registered["accident_tz_assigned"] == 1
    assert (registered["incident_series"], registered["route_series"]) == (1, 0)

    # Plan 3 Task 10: the R11 loader as ingest — the column-level INSERT (accident_id from the
    # column default), the route-match reads, the run log and quarantine, then --apply-route-ids.
    monkeypatch.setattr(accident_facts, "ingest_engine", lambda: create_async_engine(sa_ingest))

    def facts_csv(name: str, mp_route_id: str) -> object:
        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=accident_facts.COLUMNS)
            writer.writeheader()
            writer.writerow({
                "source": "AAC", "source_id": "R11-ROLE-1", "date": "2025-02-10", "date_precision": "day",
                "state": "CO", "route": "Fixture Ice", "mp_route_id": mp_route_id, "latitude": "40.0",
                "longitude": "-105.0", "geocode_precision": "crag", "activity": "ice climbing",
                "injury_severity": "minor", "source_url": "https://example.org/r11", "summary": "Fixture fall.",
            })
        return path

    loaded = asyncio.run(accident_facts.main(facts_csv("r11.csv", ""), today=JOB_TODAY, dry_run=False))
    assert (loaded["inserted"], loaded["review_pending"]) == (1, 1)
    dry = asyncio.run(accident_facts.main(facts_csv("r11.csv", ""), today=JOB_TODAY, dry_run=True))
    assert dry["report"]["quarantined"] == {"already_present": 1}
    fill = facts_csv("r11-fill.csv", "900000002")
    assert asyncio.run(accident_facts.main(fill, today=JOB_TODAY, dry_run=False, route_ids=True))["updated"] == 1


# PostGIS grants PUBLIC SELECT on spatial_ref_sys and its views; those belong to the
# extension, not to any grant this project makes, so the check skips them as verify_roles.sql does.
TRAINER_STATE_SQL = (
    "SELECT r.rolcanlogin, "
    "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "        WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p', 'v', 'm') "
    "          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass "
    "                          AND d.objid = c.oid AND d.deptype = 'e') "
    "          AND has_table_privilege('trainer', c.oid, 'SELECT')) "
    "FROM pg_roles r WHERE r.rolname = 'trainer'"
)


def test_phase2_roles_least_privilege(role_cleanup, fresh_db, tmp_path, monkeypatch):  # noqa: F811
    _require_psql()
    owner_url = _owner_db(fresh_db)
    cfg = _alembic_cfg_as(fresh_db, owner_url)
    command.upgrade(cfg, "0003_hist_insufficient_data")
    _as(owner_url, ANALYST_FIXTURE_SQL)

    early = _psql(owner_url, CREATE_PHASE2, PHASE2_PASSWORD_ENV)
    assert early.returncode != 0
    assert "run create_roles.sql first" in early.stderr

    ok = _psql(owner_url, ROLES_DIR / "create_roles.sql", ROLE_PASSWORD_ENV)
    assert ok.returncode == 0, ok.stderr

    for bad in ("short-pw", "SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy" + "x" * 8, "md5" + "0" * 32):
        refused = _psql(owner_url, CREATE_PHASE2, {"INGEST_PASSWORD": bad})
        assert refused.returncode != 0
        assert "INGEST_PASSWORD must be a plaintext password of at least 32 characters" in refused.stderr
        assert bad not in refused.stdout + refused.stderr

    unset = subprocess.run(
        [_require_psql(), owner_url, "-X", "-q", "-f", str(CREATE_PHASE2)],
        env={k: v for k, v in os.environ.items() if k != "INGEST_PASSWORD"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert unset.returncode != 0
    assert "INGEST_PASSWORD is not set" in unset.stderr
    assert _fetch_row(fresh_db, "SELECT count(*) FROM pg_roles WHERE rolname IN ('ingest', 'trainer')") == [0]

    created = _psql(owner_url, CREATE_PHASE2, PHASE2_PASSWORD_ENV)
    assert created.returncode == 0, created.stderr
    assert "roles ingest and trainer created" in created.stdout
    for plaintext in PHASE2_PASSWORDS.values():
        assert plaintext not in created.stdout + created.stderr
    stored = _fetch_row(fresh_db, "SELECT rolpassword FROM pg_authid WHERE rolname = 'ingest'")
    assert str(stored[0]).startswith("SCRAM-SHA-256$")
    assert _fetch_row(fresh_db, "SELECT rolpassword FROM pg_authid WHERE rolname = 'trainer'") == [None]

    # -e echoes every statement psql sends, so this proves a rerun stops at the secret-free
    # guard and no CREATE ROLE ... PASSWORD reaches the server log.
    rerun = subprocess.run(
        [_require_psql(), owner_url, "-X", "-q", "-e", "-f", str(CREATE_PHASE2)],
        env={**os.environ, **PHASE2_PASSWORD_ENV},
        capture_output=True,
        text=True,
        check=False,
    )
    assert rerun.returncode != 0
    assert "ingest/trainer already exist; do not rerun create_roles_phase2.sql" in rerun.stderr
    for plaintext in PHASE2_PASSWORDS.values():
        assert plaintext not in rerun.stdout + rerun.stderr

    migrator_url = _role_url(fresh_db, "migrator", PASSWORDS["migrator"])
    command.upgrade(_alembic_cfg_as(fresh_db, migrator_url), "head")

    granted = _psql(owner_url, GRANTS_PHASE2, {})
    assert granted.returncode == 0, granted.stderr
    again = _psql(owner_url, GRANTS_PHASE2, {})
    assert again.returncode == 0, again.stderr  # idempotent

    for script in ("verify_roles.sql", "verify_roles_phase2.sql"):
        verified = _psql(owner_url, ROLES_DIR / script, {})
        assert verified.returncode == 0, verified.stdout + verified.stderr
        assert "PASSED" in verified.stdout

    ingest = _role_url(fresh_db, "ingest", PHASE2_PASSWORDS["ingest"])
    app = _role_url(fresh_db, "app", PASSWORDS["app"])
    analyst = _role_url(fresh_db, "analyst", "test-analyst-pw")

    _as(ingest, "INSERT INTO source_ingest_log (run_id, source, status) VALUES (gen_random_uuid(), 't', 'running')")
    _as(ingest, "UPDATE source_ingest_log SET status = 'ok' WHERE source = 't'")
    _as(
        ingest,
        "INSERT INTO internal.ingest_quarantine (run_id, source, row_ref, reason) "
        "SELECT run_id, source, 'r1', 'x' FROM source_ingest_log",
    )
    _as(
        ingest,
        "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) "
        "VALUES (900000001, 'total', 'all', 1)",
    )
    _denied(ingest, "UPDATE internal.mp_tick_aggregates SET tick_count = 2 WHERE false")
    _denied(ingest, "DELETE FROM internal.ingest_quarantine")
    _as(ingest, "SELECT accident_id, source, source_id, date, year, mountain, state, latitude, longitude, mp_route_id, "
        "activity FROM internal.accidents_raw")
    _denied(ingest, "SELECT location FROM internal.accidents_raw")
    _denied(ingest, "SELECT route FROM internal.accidents_raw")
    _denied(ingest, "SELECT description FROM internal.accidents_raw")
    _denied(ingest, "SELECT * FROM internal.accidents_raw")
    _as(ingest, "UPDATE mp_ticks SET quarantine_reason = NULL WHERE false")
    _as(owner_url, JOB_SEED_SQL)
    # The seed lands after 0004's snapshot; the migrator owns accidents_raw, so it stands in for it.
    _as(migrator_url, "INSERT INTO internal.accidents_raw (accident_id, source, source_id, date, year, latitude, longitude) "
        "SELECT accident_id, source, source_id, date, year, latitude, longitude FROM accidents")
    _run_real_jobs_as_ingest(ingest, tmp_path, monkeypatch)
    _as(ingest, "SELECT count(*) FROM accidents")
    _as(ingest, "SELECT mp_id, latitude, longitude FROM mp_locations")
    _denied(ingest, "SELECT name FROM mp_locations")
    _denied(ingest, "SELECT * FROM mp_locations")
    _as(ingest, "UPDATE accidents SET date_precision = 'day' WHERE false")
    _as(ingest, "INSERT INTO internal.accident_revisions (accident_id, field, method, rule_version, run_id) SELECT accident_id, 'f', 'm', 'v', gen_random_uuid() FROM accidents WHERE false")
    _denied(ingest, "UPDATE accidents SET description = 'x' WHERE false")
    _denied(ingest, "UPDATE accidents SET source_id = 'x' WHERE false")
    _as(ingest, "UPDATE accidents SET tz = NULL WHERE tz IS NOT NULL AND false")
    _denied(ingest, "DELETE FROM accidents WHERE false")
    # R11 (D15): the INSERT probe commits nothing. Never accident_id or a narrative column.
    _as(ingest, "BEGIN; INSERT INTO accidents (source, source_id, description) VALUES ('AAC', 'probe', 'x'); ROLLBACK")
    assert _fetch_row(fresh_db, "SELECT count(*) FROM accidents WHERE source_id = 'probe'") == [0]
    _denied(ingest, "INSERT INTO accidents (accident_id, source) VALUES (1, 'AAC')")
    _denied(ingest, "INSERT INTO accidents (source, location) VALUES ('AAC', 'x')")
    _denied(ingest, "INSERT INTO accidents (source, tags) VALUES ('AAC', 'x')")
    _as(ingest, "UPDATE accidents SET mp_route_id = NULL WHERE false")
    _denied(ingest, "UPDATE accidents SET route = 'x' WHERE false")
    _denied(ingest, "UPDATE internal.accident_revisions SET method = 'x' WHERE false")
    _as(ingest, "INSERT INTO internal.duplicate_decisions (low_id, high_id, score, decision, decided_by, rule_version) "
        "VALUES (1, 2, 0.9, 'merge', 'auto', 'r5-v1') ON CONFLICT (low_id, high_id) DO UPDATE SET score = EXCLUDED.score")
    _as(ingest, "UPDATE internal.duplicate_decisions SET decision = 'distinct' WHERE low_id = 1")
    _denied(ingest, "DELETE FROM internal.duplicate_decisions")
    _denied(ingest, "TRUNCATE gnis_summits")
    _denied(ingest, "SELECT climber_name FROM mp_ticks")
    _denied(ingest, "SELECT * FROM mp_ticks")
    _denied(ingest, "UPDATE mp_ticks SET climber_name = 'x' WHERE false")
    _denied(ingest, "UPDATE accidents SET accident_id = accident_id WHERE false")
    _denied(ingest, "DELETE FROM source_ingest_log")
    _denied(ingest, "UPDATE historical_predictions SET risk_score = 1 WHERE false")
    _denied(ingest, "CREATE TABLE internal.nope (x int)")
    _denied(ingest, "CREATE TABLE public.nope (x int)")

    # trainer cannot log in and holds no table privilege anywhere until Phase 3 (D13).
    assert _fetch_row(fresh_db, TRAINER_STATE_SQL) == [False, False]

    # 0006: app and analyst read the clean views (default privileges); ingest since plan 3 (0007),
    # trainer in Phase 3 (D13, TRAINER_STATE_SQL above covers views).
    clean_views = "SELECT count(*) FROM accidents_clean; SELECT count(*) FROM accidents_clean_daily"
    _as(app, clean_views)
    _as(analyst, clean_views)
    _as(ingest, clean_views)
    _as(ingest, "SELECT count(*) FROM accident_conditions")
    _denied(ingest, "DELETE FROM accidents_clean WHERE false")

    # Plan 3 (0007): ingest writes conditions (DELETE only for the 3-year prune) and upserts the
    # series registry and normals, which it never deletes; app and analyst only read.
    _as(ingest, "INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) "
        "VALUES (1, 'UTC', '2020-01-01', 'era5', 'open_meteo_archive') "
        "ON CONFLICT (grid_bucket, tz, date) DO UPDATE SET tmax = EXCLUDED.tmax")
    _as(ingest, "DELETE FROM cell_daily_conditions WHERE grid_bucket = 1")
    _as(ingest, "INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES (1, 'UTC') "
        "ON CONFLICT (grid_bucket, tz) DO UPDATE SET first_era5_date = NULL")
    _as(ingest, "INSERT INTO cell_normals_status (grid_bucket, status) VALUES (1, 'pending') "
        "ON CONFLICT (grid_bucket) DO UPDATE SET status = EXCLUDED.status")
    _as(ingest, "UPDATE cell_climate_normals SET n_years = 1 WHERE false; "
        "UPDATE cell_climatology SET n_years = 1 WHERE false")
    for table in ("grid_bucket_series", "cell_normals_status", "cell_climate_normals", "cell_climatology"):
        _denied(ingest, f"DELETE FROM {table} WHERE false")
    _denied(ingest, "TRUNCATE cell_daily_conditions")
    _as(app, "SELECT count(*) FROM cell_daily_conditions; SELECT count(*) FROM accident_conditions")
    _denied(app, "DELETE FROM cell_daily_conditions WHERE false")

    # Plan 3 (R6 verify): ingest reads the legacy weather table, never writes it.
    _as(ingest, "SELECT count(*) FROM weather")
    _denied(ingest, "UPDATE weather SET temperature_max = 0 WHERE false")
    _denied(ingest, "DELETE FROM weather WHERE false")
    _denied(ingest, "INSERT INTO weather (date, latitude, longitude) VALUES ('2020-01-01', 0, 0)")

    # Plan 3 (R7): ingest reads area_weekly_weather and can flip longitude only.
    _as(ingest, "SELECT count(*) FROM area_weekly_weather")
    _as(ingest, "UPDATE area_weekly_weather SET longitude = -longitude WHERE false")
    _denied(ingest, "UPDATE area_weekly_weather SET temp_avg = 0 WHERE false")
    _denied(ingest, "INSERT INTO area_weekly_weather (latitude, longitude, week_start, week_end) "
        "VALUES (0, 0, '2020-01-06', '2020-01-12')")
    _denied(ingest, "DELETE FROM area_weekly_weather WHERE false")
    _denied(analyst, "BEGIN READ WRITE; DELETE FROM grid_bucket_series WHERE false; COMMIT")
    _denied(app, "UPDATE accidents_clean SET source_url = 'x' WHERE false")
    _denied(analyst, "BEGIN READ WRITE; DELETE FROM accidents_clean_daily WHERE false; COMMIT")

    _denied(app, "SELECT count(*) FROM internal.mp_tick_aggregates")
    _denied(app, "SELECT count(*) FROM internal.accidents_raw")
    _as(app, "SELECT count(*) FROM source_ingest_log")

    _as(analyst, "SELECT count(*) FROM internal.accidents_raw; SELECT count(*) FROM internal.mp_tick_aggregates")
    # READ WRITE overrides the analyst's read-only default, so only the grants stop it.
    _denied(analyst, "BEGIN READ WRITE; DELETE FROM internal.mp_tick_aggregates; COMMIT")

    # The schema belongs to migrator, so the stray grant is made as migrator.
    _as(owner_url, "SET ROLE migrator; GRANT USAGE ON SCHEMA internal TO app; RESET ROLE;")
    stray = _psql(owner_url, VERIFY_PHASE2, {})
    assert stray.returncode != 0
    assert "app has no USAGE on schema internal" in stray.stderr


def test_after_relaunch_revoke_owner_regrants_set_and_migrator_runs_grants(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner_url = _owner_db(fresh_db)
    command.upgrade(_alembic_cfg_as(fresh_db, owner_url), "0003_hist_insufficient_data")
    _as(owner_url, ANALYST_FIXTURE_SQL)
    assert _psql(owner_url, ROLES_DIR / "create_roles.sql", ROLE_PASSWORD_ENV).returncode == 0

    # Phase 1 relaunch step 3; the owner keeps only its creator ADMIN grant, which has no SET.
    _as(owner_url, "REVOKE migrator FROM CURRENT_USER")
    refused = _psql(owner_url, CREATE_PHASE2, PHASE2_PASSWORD_ENV)
    assert refused.returncode != 0
    assert "owner needs SET on migrator: GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT FALSE" in refused.stderr
    assert PHASE2_PASSWORDS["ingest"] not in refused.stdout + refused.stderr
    assert _fetch_row(fresh_db, "SELECT count(*) FROM pg_roles WHERE rolname IN ('ingest', 'trainer')") == [0]

    _as(owner_url, "GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT FALSE")
    created = _psql(owner_url, CREATE_PHASE2, PHASE2_PASSWORD_ENV)
    assert created.returncode == 0, created.stderr

    migrator_url = _role_url(fresh_db, "migrator", PASSWORDS["migrator"])
    command.upgrade(_alembic_cfg_as(fresh_db, migrator_url), "head")
    granted = _psql(migrator_url, GRANTS_PHASE2, {})
    assert granted.returncode == 0, granted.stderr
    for script in ("verify_roles.sql", "verify_roles_phase2.sql"):
        verified = _psql(owner_url, ROLES_DIR / script, {})
        assert verified.returncode == 0, verified.stdout + verified.stderr

    _as(owner_url, "REVOKE migrator FROM CURRENT_USER")
    no_set = _psql(owner_url, GRANTS_PHASE2, {})
    assert no_set.returncode != 0
    assert "run as migrator, or as an owner with SET on migrator" in no_set.stderr


def test_grants_phase2_narrows_a_preexisting_table_level_grant(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner_url = _owner_db(fresh_db)
    command.upgrade(_alembic_cfg_as(fresh_db, owner_url), "0003_hist_insufficient_data")
    _as(owner_url, ANALYST_FIXTURE_SQL)
    assert _psql(owner_url, ROLES_DIR / "create_roles.sql", ROLE_PASSWORD_ENV).returncode == 0
    assert _psql(owner_url, CREATE_PHASE2, PHASE2_PASSWORD_ENV).returncode == 0
    migrator_url = _role_url(fresh_db, "migrator", PASSWORDS["migrator"])
    command.upgrade(_alembic_cfg_as(fresh_db, migrator_url), "head")

    # Simulates an older grants script, or a by-hand grant, that gave ingest the whole table
    # (climber_name included) before this script's REVOKE-then-column-GRANT existed.
    _as(migrator_url, "GRANT SELECT ON public.mp_ticks TO ingest; GRANT INSERT ON public.accidents TO ingest")
    ingest = _role_url(fresh_db, "ingest", PHASE2_PASSWORDS["ingest"])
    _as(ingest, "SELECT climber_name FROM mp_ticks")

    assert _psql(owner_url, GRANTS_PHASE2, {}).returncode == 0
    _denied(ingest, "SELECT climber_name FROM mp_ticks")
    _as(ingest, "SELECT tick_id FROM mp_ticks")
    _denied(ingest, "BEGIN; INSERT INTO accidents (source, tags) VALUES ('AAC', 'x'); ROLLBACK")
    _as(ingest, "BEGIN; INSERT INTO accidents (source, source_id) VALUES ('AAC', 'probe'); ROLLBACK")
    verified = _psql(owner_url, VERIFY_PHASE2, {})
    assert verified.returncode == 0, verified.stdout + verified.stderr


def test_verify_phase2_catches_stray_grants(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner_url = _owner_db(fresh_db)
    command.upgrade(_alembic_cfg_as(fresh_db, owner_url), "0003_hist_insufficient_data")
    _as(owner_url, ANALYST_FIXTURE_SQL)
    assert _psql(owner_url, ROLES_DIR / "create_roles.sql", ROLE_PASSWORD_ENV).returncode == 0
    assert _psql(owner_url, CREATE_PHASE2, PHASE2_PASSWORD_ENV).returncode == 0
    migrator_url = _role_url(fresh_db, "migrator", PASSWORDS["migrator"])
    command.upgrade(_alembic_cfg_as(fresh_db, migrator_url), "head")
    assert _psql(owner_url, GRANTS_PHASE2, {}).returncode == 0

    _as(
        migrator_url,
        "GRANT DELETE ON public.source_ingest_log TO ingest;"
        "GRANT UPDATE ON internal.mp_tick_aggregates TO ingest;"
        "GRANT DELETE ON internal.duplicate_decisions TO ingest;"
        "REVOKE DELETE ON public.gnis_summits FROM ingest;"
        "GRANT SELECT ON internal.accidents_raw TO ingest;"
        "GRANT SELECT (name) ON public.mp_locations TO ingest;"
        "GRANT SELECT (climber_name) ON public.mp_ticks TO ingest;"
        "GRANT USAGE ON SEQUENCE public.mp_ticks_tick_id_seq TO ingest;"
        "ALTER DEFAULT PRIVILEGES IN SCHEMA internal GRANT SELECT ON TABLES TO ingest;"
        "GRANT SELECT ON public.accidents TO trainer;"
        "GRANT SELECT ON public.accidents_clean TO trainer;"
        "REVOKE SELECT ON public.accidents_clean_daily FROM ingest;"
        "GRANT DELETE ON public.grid_bucket_series TO ingest;"
        "GRANT TRUNCATE ON public.cell_daily_conditions TO ingest;"
        "GRANT INSERT ON public.cell_daily_conditions TO app;"
        "GRANT SELECT ON public.accident_conditions TO trainer;"
        "GRANT UPDATE ON public.accidents_clean TO app;"
        "REVOKE SELECT ON public.accidents_clean_daily FROM analyst;"
        "GRANT CREATE ON SCHEMA internal TO app;"
        "REVOKE SELECT ON internal.accident_revisions FROM analyst;"
        "REVOKE UPDATE (tz) ON public.accidents FROM ingest;"
        "REVOKE SELECT ON public.weather FROM ingest;"
        "GRANT INSERT (location) ON public.accidents TO ingest;"
        "REVOKE INSERT (description) ON public.accidents FROM ingest;"
        "GRANT SELECT ON SEQUENCE public.accidents_accident_id_seq TO ingest;",
    )
    _as(owner_url, "GRANT ingest TO analyst")
    # CREATE without USAGE is enough to create a table, which is why verify checks both.
    _as(_role_url(fresh_db, "app", PASSWORDS["app"]), "CREATE TABLE internal.app_stray (x int)")

    stray = _psql(owner_url, VERIFY_PHASE2, {})
    assert stray.returncode != 0
    for check in (
        "ingest DELETE on public.source_ingest_log matches the expected set",
        "ingest UPDATE on internal.mp_tick_aggregates matches the expected set",
        "ingest DELETE on internal.duplicate_decisions matches the expected set",
        "ingest DELETE on public.gnis_summits matches the expected set",
        "ingest SELECT on internal.accidents_raw matches the expected set",
        "ingest accidents_raw column privileges match the expected set: description",
        "ingest mp_locations column privileges match the expected set: name",
        "ingest accidents column privileges match the expected set: tz",
        "ingest mp_ticks column privileges match the expected set: climber_name",
        "ingest has nothing on sequence public.mp_ticks_tick_id_seq",
        "no default privileges grant ingest or trainer anything",
        "trainer holds no privilege on public.accidents",
        "trainer holds no privilege on public.accidents_clean",
        "ingest SELECT on public.accidents_clean_daily matches the expected set",
        "ingest DELETE on public.grid_bucket_series matches the expected set",
        "ingest TRUNCATE on public.cell_daily_conditions matches the expected set",
        "app can SELECT public.cell_daily_conditions and nothing else",
        "trainer holds no privilege on public.accident_conditions",
        "app can SELECT public.accidents_clean and nothing else",
        "analyst can SELECT public.accidents_clean_daily and nothing else",
        "app has no CREATE on schema internal",
        "analyst can SELECT internal.accident_revisions",
        "nobody can SET or INHERIT ingest",
        "nobody but CURRENT_USER holds ADMIN on ingest",
        "ingest SELECT on public.weather matches the expected set",
        "ingest accidents column privileges match the expected set: location",
        "ingest accidents column privileges match the expected set: description",
        "ingest has nothing on sequence public.accidents_accident_id_seq",
    ):
        assert check in stray.stderr, check
