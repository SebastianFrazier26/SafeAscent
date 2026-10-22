"""Phase 3 roles (DP17): trainer gains LOGIN and its training allowlist; schema model is read by app
and analyst, written only by trainer, and invisible to ingest. Prod order: Phase 2 roles and grants →
upgrade head → trainer_login_phase3.sql → grants_phase3.sql → verify scripts."""

import os
import subprocess

import pytest
from alembic import command

from tests.pgtest import requires_pg
from tests.test_migrations import (
    PASSWORDS,
    PHASE2_PASSWORDS,
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
from tests.test_roles_phase2 import run_phase2_roles

pytestmark = requires_pg
TRAINER_LOGIN = ROLES_DIR / "trainer_login_phase3.sql"
GRANTS_PHASE3 = ROLES_DIR / "grants_phase3.sql"
VERIFY_PHASE3 = ROLES_DIR / "verify_roles_phase3.sql"
TRAINER_PASSWORD = "t" * 40
ALLOW_SELECT = ("public.accidents_clean", "public.accident_route_links", "public.canonical_areas",
                "public.canonical_routes", "public.scorable_routes", "public.scoring_unit_features",
                "public.historical_predictions", "internal.exposure_index", "model.registry", "model.route_scores")


def _phase3(fresh_db: str) -> tuple[str, str]:  # noqa: F811
    owner = _owner_db(fresh_db)
    run_phase2_roles(fresh_db)
    command.upgrade(_alembic_cfg_as(fresh_db, owner), "head")
    login = _psql(owner, TRAINER_LOGIN, {"TRAINER_PASSWORD": TRAINER_PASSWORD})
    assert login.returncode == 0, login.stderr
    assert TRAINER_PASSWORD not in login.stdout + login.stderr
    grants = _psql(owner, GRANTS_PHASE3, {})
    assert grants.returncode == 0, grants.stderr
    return owner, _role_url(fresh_db, "trainer", TRAINER_PASSWORD)


def test_trainer_reads_allowlist_and_writes_only_model(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner, trainer = _phase3(fresh_db)
    assert _psql(owner, GRANTS_PHASE3, {}).returncode == 0  # idempotent
    stored = _fetch_row(fresh_db, "SELECT rolpassword, rolconnlimit FROM pg_authid WHERE rolname = 'trainer'")
    assert str(stored[0]).startswith("SCRAM-SHA-256$") and stored[1] == 4
    for rel in ALLOW_SELECT:
        _as(trainer, f"SELECT count(*) FROM {rel}")
    for rel in ("public.accidents", "public.accidents_clean_daily", "public.accident_conditions",
                "internal.mp_tick_aggregates", "internal.route_tick_scrapes", "public.mp_ticks", "public.weather"):
        _denied(trainer, f"SELECT count(*) FROM {rel}")
    _as(trainer, "UPDATE model.registry SET metrics = metrics WHERE false")
    _as(trainer, "DELETE FROM model.route_scores WHERE false")
    # The real writes registry.py and the scoring job make, committed nothing.
    _as(trainer, "BEGIN; INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status) "
        "VALUES ('probe', 'eb_v1', 'h', '2020-01-01', '2026-01-01', 'candidate'); "
        "SELECT status FROM model.registry WHERE version = 'probe' FOR UPDATE; ROLLBACK")
    _denied(trainer, "DELETE FROM model.registry WHERE false")
    _denied(trainer, "UPDATE model.route_scores SET rr_mean = 1 WHERE false")
    _denied(trainer, "TRUNCATE model.route_scores")
    _denied(trainer, "CREATE TABLE model.nope (x int)")
    _denied(trainer, "CREATE TABLE public.nope (x int)")
    _denied(trainer, "UPDATE historical_predictions SET risk_score = 1 WHERE false")
    _denied(trainer, "INSERT INTO internal.exposure_index SELECT * FROM internal.exposure_index WHERE false")
    app = _role_url(fresh_db, "app", PASSWORDS["app"])
    _as(app, "SELECT count(*) FROM model.registry; SELECT count(*) FROM model.route_scores")
    _denied(app, "DELETE FROM model.route_scores WHERE false")
    _denied(app, "UPDATE model.registry SET metrics = metrics WHERE false")
    analyst = _role_url(fresh_db, "analyst", "test-analyst-pw")
    _as(analyst, "SELECT count(*) FROM model.registry; SELECT count(*) FROM model.route_scores")
    _denied(analyst, "BEGIN READ WRITE; DELETE FROM model.route_scores WHERE false; COMMIT")
    ingest = _role_url(fresh_db, "ingest", PHASE2_PASSWORDS["ingest"])
    _denied(ingest, "SELECT count(*) FROM model.registry")
    verify = _psql(owner, VERIFY_PHASE3, {})
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "ALL PHASE 3 ROLE CHECKS PASSED" in verify.stdout
    for script in ("verify_roles.sql", "verify_roles_phase2.sql"):
        earlier = _psql(owner, ROLES_DIR / script, {})
        assert earlier.returncode == 0, earlier.stdout + earlier.stderr


def test_trainer_login_refuses_rerun(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner, _ = _phase3(fresh_db)
    # -e echoes every statement psql sends: the rerun stops before the password leaves the client.
    again = subprocess.run(
        [_require_psql(), owner, "-X", "-q", "-e", "-f", str(TRAINER_LOGIN)],
        env={**os.environ, "TRAINER_PASSWORD": TRAINER_PASSWORD},
        capture_output=True,
        text=True,
        check=False,
    )
    assert again.returncode != 0 and "trainer can already log in" in again.stderr
    assert TRAINER_PASSWORD not in again.stdout + again.stderr


@pytest.mark.parametrize("bad", ["short", "SCRAM-SHA-256$4096:" + "a" * 40, "md5" + "0" * 32])
def test_trainer_login_refuses_bad_passwords(role_cleanup, fresh_db, bad):  # noqa: F811
    _require_psql()
    owner = _owner_db(fresh_db)
    run_phase2_roles(fresh_db)
    out = _psql(owner, TRAINER_LOGIN, {"TRAINER_PASSWORD": bad})
    assert out.returncode != 0
    assert "TRAINER_PASSWORD must be a plaintext password" in out.stderr
    assert bad not in out.stdout + out.stderr
    assert _fetch_row(fresh_db, "SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer'") == [False]


def test_trainer_login_requires_password_and_phase2(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner = _owner_db(fresh_db)
    early = _psql(owner, TRAINER_LOGIN, {"TRAINER_PASSWORD": TRAINER_PASSWORD})
    assert early.returncode != 0 and "run create_roles_phase2.sql first" in early.stderr
    run_phase2_roles(fresh_db)
    unset = subprocess.run(
        [_require_psql(), owner, "-X", "-q", "-f", str(TRAINER_LOGIN)],
        env={k: v for k, v in os.environ.items() if k != "TRAINER_PASSWORD"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert unset.returncode != 0 and "TRAINER_PASSWORD is not set" in unset.stderr
    assert _fetch_row(fresh_db, "SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer'") == [False]


def test_grants_phase3_narrows_stray_model_grants_on_rerun(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner, trainer = _phase3(fresh_db)
    _as(owner, "SET ROLE migrator; GRANT UPDATE, TRUNCATE ON model.route_scores TO trainer; "
               "GRANT SELECT ON model.registry TO ingest; GRANT INSERT ON model.route_scores TO app; RESET ROLE;")
    assert _psql(owner, VERIFY_PHASE3, {}).returncode != 0
    assert _psql(owner, GRANTS_PHASE3, {}).returncode == 0
    _denied(trainer, "TRUNCATE model.route_scores")
    verify = _psql(owner, VERIFY_PHASE3, {})
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_verify_phase3_catches_stray_grants(role_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    owner, _ = _phase3(fresh_db)
    _as(owner, "SET ROLE migrator; "
               "GRANT SELECT ON public.accidents TO trainer; GRANT UPDATE ON model.route_scores TO trainer; "
               "GRANT INSERT ON model.route_scores TO app; GRANT SELECT ON model.registry TO ingest; "
               "GRANT SELECT ON internal.mp_tick_aggregates TO trainer; "
               "GRANT UPDATE (metrics) ON model.registry TO analyst; "
               "GRANT SELECT ON model.registry TO PUBLIC; "
               "GRANT USAGE ON SCHEMA model TO ingest; "
               "ALTER DEFAULT PRIVILEGES IN SCHEMA model GRANT SELECT ON TABLES TO ingest; "
               "RESET ROLE; "
               "ALTER ROLE trainer CONNECTION LIMIT -1;")
    out = _psql(owner, VERIFY_PHASE3, {})
    assert out.returncode != 0
    for check in ("trainer privileges match the allowlist on public.accidents",
                  "trainer privileges match the allowlist on model.route_scores",
                  "trainer privileges match the allowlist on internal.mp_tick_aggregates",
                  "app holds only SELECT on model.route_scores",
                  "ingest holds nothing on model.registry",
                  "analyst holds only SELECT on model.registry",
                  "model.registry grants only the expected privileges",
                  "ingest has no USAGE on schema model",
                  "PUBLIC holds nothing on schema model or its relations",
                  "no default privileges apply to new model relations",
                  "trainer connection limit is 4"):
        assert check in out.stderr, check
