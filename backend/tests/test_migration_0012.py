import asyncpg
import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
PIN = "0012_model_registry"
SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000110, 'Fixture Crag', 900000100, 40.2, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000001, 'Fixture Route', 900000110, 'Trad');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  (900000110, 900000100, '900000100.900000110', 2, 'us', 'CO', 40.2, -105.3, 'own', 900000110,
   '40.20000:-105.30000', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000001, 900000110, 'trad', false, '{trad}', 'rt-mp-v3', true, NULL, 'x', 'v');
INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status)
  VALUES ('k0', 'kernel', 'h', '1990-01-01', '2026-10-01', 'candidate');
"""
OK_ROW = ("INSERT INTO model.route_scores (model_version, route_id, month, type_group, area_id, insufficient, "
          "insufficient_reason, rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95, conf_level, n_eff_geo, "
          "n_eff_feature, per10k_mean, per10k_q05, per10k_q95, per10k_reason) VALUES "
          "('k0', 900000001, {m}, 'trad', 900000110, {ins}, {reason}, {rr}, {pct}, '{conf}', {neff}, "
          "{p10}, {p10reason})")


def _row(m=1, ins="false", reason="NULL", rr="1.0, 0.5, 2.0", pct="40, 20, 60", conf="medium",
         p10reason="'ticks_incomplete'", neff="1, 2", p10="NULL, NULL, NULL"):
    return OK_ROW.format(m=m, ins=ins, reason=reason, rr=rr, pct=pct, conf=conf, p10reason=p10reason, neff=neff,
                         p10=p10)


def test_0012_route_scores_checks():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, _row())
        run_sql(name, _row(m=2, ins="true", reason="'no_evidence'", rr="NULL, NULL, NULL", pct="NULL, NULL, NULL",
                           conf="insufficient", p10reason="NULL"))
        run_sql(name, _row(m=4, p10="1.5, 0.5, 3.0", p10reason="NULL", neff="0, 0"))
        bad = [
            {"rr": "1.0, 1.5, 2.0"},                       # q05 above mean
            {"rr": "0, 0, 0"},                              # a zero rate
            {"pct": "40, 50, 60"},                          # pct_q05 above percentile
            {"ins": "true", "reason": "'no_evidence'", "conf": "insufficient", "p10reason": "NULL"},  # numbers only (F12)
            {"conf": "insufficient"},                       # level without the flag
            {"p10reason": "NULL"},                          # per10k neither present nor explained
            {"rr": "1.0, 0.5, 'Infinity'"},
            {"rr": "1.0, 0.5, 'NaN'"},
            {"p10": "'NaN', 0.5, 3.0", "p10reason": "NULL"},
            {"p10": "1.5, '-Infinity', 3.0", "p10reason": "NULL"},
            {"p10": "1.5, 0.5, 'Infinity'", "p10reason": "NULL"},
            {"neff": "-1, 2"},
            {"neff": "1, 'NaN'"},
        ]
        for over in bad:
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, _row(m=3, **over))


def test_0012_one_active_one_previous():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, "UPDATE model.registry SET status = 'active', promoted_at = now() WHERE version = 'k0'")
        run_sql(name, "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, "
                      "promoted_at) VALUES ('k1', 'kernel', 'h', '1990-01-01', '2026-10-01', 'retired', now())")
        with pytest.raises(asyncpg.UniqueViolationError):
            run_sql(name, "UPDATE model.registry SET status = 'active' WHERE version = 'k1'")


def test_0012_eb_active_needs_bundle_and_scores():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, "
                          "promoted_at) VALUES ('e1', 'eb_v1', 'h', '1990-01-01', '2026-10-01', 'active', now())")


EB_ACTIVE = ("INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, promoted_at, "
             "bundle, bundle_sha256, bundle_bytes, scores_rows) VALUES ('{v}', 'eb_v1', 'h', '1990-01-01', "
             "'2026-10-01', '{status}', now(), '\\x00'::bytea, 's', {nbytes}, {rows})")


def test_0012_eb_active_with_bundle_needs_scores_rows():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, EB_ACTIVE.format(v="e1", status="active", nbytes=1, rows="NULL"))
        run_sql(name, EB_ACTIVE.format(v="e1", status="active", nbytes=1, rows=5))


def test_0012_registry_counts_non_negative():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        for nbytes, rows in ((-1, 5), (1, -1)):
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, EB_ACTIVE.format(v="e2", status="retired", nbytes=nbytes, rows=rows))


def test_0012_models_match_at_head():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))


def test_0012_downgrade_refuses_while_rows_exist():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        with pytest.raises(RuntimeError, match="refusing to downgrade 0012"):
            command.downgrade(_alembic_cfg(name), "0011_exposure_index")
        run_sql(name, "DELETE FROM model.registry")
        command.downgrade(_alembic_cfg(name), "0011_exposure_index")
        assert fetch(name, "SELECT to_regclass('model.registry')") == [(None,)]
