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
GRANTS_PHASE2 = ROLES_DIR / "grants_phase2.sql"
VERIFY_PHASE2 = ROLES_DIR / "verify_roles_phase2.sql"

# The shape of Task 5's R8 QUARANTINE_SQL, so a column missing from ingest's mp_ticks grant
# fails here rather than on the first prod run.
R8_SHAPED_SQL = """
WITH classified AS (
  SELECT t.tick_id,
         CASE
           WHEN t.tick_date > LEAST(DATE '2026-09-29', t.created_at::date + 1) THEN 'future'
           WHEN CASE WHEN t.route_id ~ '^[0-9]{1,18}$'
                     THEN NOT EXISTS (SELECT 1 FROM mp_routes r WHERE r.mp_route_id = t.route_id::bigint)
                     ELSE true END THEN 'orphan_route'
           WHEN t.tick_date < DATE '1970-01-01' THEN 'pre_1970'
         END AS reason
  FROM mp_ticks t
)
UPDATE mp_ticks m
SET quarantine_reason = c.reason, quarantine_rule_version = 'r8-v1'
FROM classified c
WHERE m.tick_id = c.tick_id
  AND (m.quarantine_reason IS DISTINCT FROM c.reason OR m.quarantine_rule_version IS DISTINCT FROM 'r8-v1');
SELECT coalesce(quarantine_reason, 'clean'), count(*) FROM mp_ticks GROUP BY 1;
"""

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
    _denied(ingest, "SELECT count(*) FROM internal.accidents_raw")
    _as(ingest, "UPDATE mp_ticks SET quarantine_reason = NULL WHERE false")
    _as(ingest, R8_SHAPED_SQL)
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
    _as(migrator_url, "GRANT SELECT ON public.mp_ticks TO ingest")
    ingest = _role_url(fresh_db, "ingest", PHASE2_PASSWORDS["ingest"])
    _as(ingest, "SELECT climber_name FROM mp_ticks")

    assert _psql(owner_url, GRANTS_PHASE2, {}).returncode == 0
    _denied(ingest, "SELECT climber_name FROM mp_ticks")
    _as(ingest, "SELECT tick_id FROM mp_ticks")
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
        "GRANT SELECT ON internal.accidents_raw TO ingest;"
        "GRANT SELECT (climber_name) ON public.mp_ticks TO ingest;"
        "GRANT USAGE ON SEQUENCE public.mp_ticks_tick_id_seq TO ingest;"
        "ALTER DEFAULT PRIVILEGES IN SCHEMA internal GRANT SELECT ON TABLES TO ingest;"
        "GRANT SELECT ON public.accidents TO trainer;"
        "GRANT CREATE ON SCHEMA internal TO app;"
        "REVOKE SELECT ON internal.accident_revisions FROM analyst;",
    )
    _as(owner_url, "GRANT ingest TO analyst")
    # CREATE without USAGE is enough to create a table, which is why verify checks both.
    _as(_role_url(fresh_db, "app", PASSWORDS["app"]), "CREATE TABLE internal.app_stray (x int)")

    stray = _psql(owner_url, VERIFY_PHASE2, {})
    assert stray.returncode != 0
    for check in (
        "ingest DELETE on public.source_ingest_log matches the expected set",
        "ingest UPDATE on internal.mp_tick_aggregates matches the expected set",
        "ingest SELECT on internal.accidents_raw matches the expected set",
        "ingest mp_ticks column privileges match the expected set: climber_name",
        "ingest has nothing on sequence public.mp_ticks_tick_id_seq",
        "no default privileges grant ingest or trainer anything",
        "trainer holds no privilege on public.accidents",
        "app has no CREATE on schema internal",
        "analyst can SELECT internal.accident_revisions",
        "nobody can SET or INHERIT ingest",
        "nobody but CURRENT_USER holds ADMIN on ingest",
    ):
        assert check in stray.stderr, check
