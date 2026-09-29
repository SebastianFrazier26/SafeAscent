"""Prod order, rehearsed: 0003 → create_roles.sql → create_roles_phase2.sql → migrator
upgrades to head → grants_phase2.sql → verify scripts."""

import os
import subprocess

from alembic import command

from tests.pgtest import requires_pg
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
CREATE_PHASE2 = ROLES_DIR / "create_roles_phase2.sql"

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


def test_phase2_roles_least_privilege(role_cleanup, fresh_db):  # noqa: F811
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

    granted = _psql(owner_url, ROLES_DIR / "grants_phase2.sql", {})
    assert granted.returncode == 0, granted.stderr
    again = _psql(owner_url, ROLES_DIR / "grants_phase2.sql", {})
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
    _denied(ingest, "SELECT count(*) FROM internal.accidents_raw")
    _as(ingest, "UPDATE mp_ticks SET quarantine_reason = NULL WHERE false")
    _denied(ingest, "UPDATE mp_ticks SET climber_name = 'x' WHERE false")
    _denied(ingest, "UPDATE accidents SET accident_id = accident_id WHERE false")
    _denied(ingest, "DELETE FROM source_ingest_log")
    _denied(ingest, "UPDATE historical_predictions SET risk_score = 1 WHERE false")
    _denied(ingest, "CREATE TABLE internal.nope (x int)")
    _denied(ingest, "CREATE TABLE public.nope (x int)")

    # trainer cannot log in and holds no table privilege anywhere until Phase 3 (D13).
    assert _fetch_row(fresh_db, TRAINER_STATE_SQL) == [False, False]

    _denied(app, "SELECT count(*) FROM internal.mp_tick_aggregates")
    _denied(app, "SELECT count(*) FROM internal.accidents_raw")
    _as(app, "SELECT count(*) FROM source_ingest_log")

    _as(analyst, "SELECT count(*) FROM internal.accidents_raw; SELECT count(*) FROM internal.mp_tick_aggregates")
    # READ WRITE overrides the analyst's read-only default, so only the grants stop it.
    _denied(analyst, "BEGIN READ WRITE; DELETE FROM internal.mp_tick_aggregates; COMMIT")

    # The schema belongs to migrator, so the stray grant is made as migrator.
    _as(owner_url, "SET ROLE migrator; GRANT USAGE ON SCHEMA internal TO app; RESET ROLE;")
    stray = _psql(owner_url, ROLES_DIR / "verify_roles_phase2.sql", {})
    assert stray.returncode != 0
    assert "app has no USAGE on schema internal" in stray.stderr


def test_verify_phase2_catches_stray_ingest_and_trainer_grants(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner_url = _owner_db(fresh_db)
    command.upgrade(_alembic_cfg_as(fresh_db, owner_url), "0003_hist_insufficient_data")
    _as(owner_url, ANALYST_FIXTURE_SQL)
    assert _psql(owner_url, ROLES_DIR / "create_roles.sql", ROLE_PASSWORD_ENV).returncode == 0
    assert _psql(owner_url, CREATE_PHASE2, PHASE2_PASSWORD_ENV).returncode == 0
    migrator_url = _role_url(fresh_db, "migrator", PASSWORDS["migrator"])
    command.upgrade(_alembic_cfg_as(fresh_db, migrator_url), "head")
    assert _psql(owner_url, ROLES_DIR / "grants_phase2.sql", {}).returncode == 0

    _as(
        migrator_url,
        "GRANT DELETE ON public.source_ingest_log TO ingest;"
        "GRANT UPDATE ON internal.mp_tick_aggregates TO ingest;"
        "GRANT SELECT ON public.accidents TO trainer;",
    )
    stray = _psql(owner_url, ROLES_DIR / "verify_roles_phase2.sql", {})
    assert stray.returncode != 0
    assert "ingest DELETE on public.source_ingest_log matches the expected set" in stray.stderr
    assert "ingest UPDATE on internal.mp_tick_aggregates matches the expected set" in stray.stderr
    assert "trainer holds no privilege on public.accidents" in stray.stderr
