from datetime import date

import numpy as np
import pytest

from app.ml import registry
from app.ml.bundle import ModelBundle
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, run_sql
from tests.test_migration_0012 import SEED

pytestmark = requires_pg
ROW = ("INSERT INTO model.route_scores (model_version, route_id, month, type_group, area_id, insufficient, "
       "insufficient_reason, rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95, conf_level, n_eff_geo, "
       "n_eff_feature, per10k_mean, per10k_q05, per10k_q95, per10k_reason) VALUES "
       "('{v}', 900000001, 1, 'trad', 900000110, false, NULL, 1, 0.5, 2, 40, 20, 60, 'medium', 1, 1, "
       "NULL, NULL, NULL, 'ticks_incomplete')")
KERNEL_ACTIVE = "UPDATE model.registry SET status = 'active', promoted_at = now() WHERE version = 'k0'"


def _insert(name: str, version: str, bundle: bytes | None = b"zip") -> None:
    run_on_conn(name, lambda c: registry.insert_candidate(
        c, version=version, family="eb_v1", data_hash="h", train_start=date(1990, 1, 1),
        data_cutoff=date(2026, 10, 1), exposure_as_of=None, metrics={}, thresholds={}, model_card={},
        bundle=bundle, parent_version=None))


def _candidate(name: str, version: str, bundle: bytes = b"zip") -> None:
    _insert(name, version, bundle)
    run_sql(name, ROW.format(v=version))
    run_on_conn(name, lambda c: registry.set_gate(c, version, {"passed": True}, {}, 1))


def _promote(name: str, version: str) -> str | None:
    return run_on_conn(name, lambda c: registry.promote(c, version, by="auto"))


def test_promote_then_rollback_flips_atomically():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, KERNEL_ACTIVE)
        _candidate(name, "e1")
        assert _promote(name, "e1") is None  # DP15: the kernel row retires, it never becomes previous
        assert fetch(name, "SELECT version, status FROM model.registry ORDER BY version") == [
            ("e1", "active"), ("k0", "retired")]
        _candidate(name, "e2")
        assert _promote(name, "e2") == "e1"
        assert run_on_conn(name, registry.rollback) == ("e2", "e1")
        assert dict(fetch(name, "SELECT version, status FROM model.registry")) == {
            "e1": "active", "e2": "retired", "k0": "retired"}
        act = run_on_conn(name, registry.active)
        assert (act.version, act.family) == ("e1", "eb_v1") and len(act.bundle_sha256) == 64


def test_third_promotion_retires_the_old_previous():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, KERNEL_ACTIVE)
        for v in ("e1", "e2", "e3"):
            _candidate(name, v)
            _promote(name, v)
        assert dict(fetch(name, "SELECT version, status FROM model.registry")) == {
            "e1": "retired", "e2": "previous", "e3": "active", "k0": "retired"}


def test_rollback_refuses_without_servable_previous():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, KERNEL_ACTIVE)
        _candidate(name, "e1")
        _promote(name, "e1")
        with pytest.raises(registry.RollbackRefused, match="no previous version"):
            run_on_conn(name, registry.rollback)
        assert fetch(name, "SELECT status FROM model.registry WHERE version = 'e1'") == [("active",)]


def test_rollback_refuses_without_active():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, KERNEL_ACTIVE)
        for v in ("e1", "e2"):
            _candidate(name, v)
            _promote(name, v)
        run_sql(name, "UPDATE model.registry SET status = 'retired' WHERE version = 'e2'")
        with pytest.raises(registry.RollbackRefused, match="no active version"):
            run_on_conn(name, registry.rollback)
        assert fetch(name, "SELECT status FROM model.registry WHERE version = 'e1'") == [("previous",)]


def test_promote_refuses_unscored_or_ungated():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        _insert(name, "e9", b"z")
        with pytest.raises(registry.PromotionRefused, match="gate"):
            _promote(name, "e9")
        run_on_conn(name, lambda c: registry.set_gate(c, "e9", {"passed": False}, {}, 1))
        with pytest.raises(registry.PromotionRefused, match="gate"):
            _promote(name, "e9")
        run_on_conn(name, lambda c: registry.set_gate(c, "e9", {"passed": True}, {}, 0))
        with pytest.raises(registry.PromotionRefused, match="no materialized scores"):
            _promote(name, "e9")
        with pytest.raises(registry.PromotionRefused, match="no materialized scores"):
            run_on_conn(name, lambda c: registry.promote(c, "e9", by="owner"))
        with pytest.raises(registry.PromotionRefused, match="kernel"):
            run_on_conn(name, lambda c: registry.promote(c, "k0", by="owner"))
        with pytest.raises(registry.PromotionRefused, match="no such version"):
            _promote(name, "nope")
        assert fetch(name, "SELECT status FROM model.registry WHERE version = 'e9'") == [("candidate",)]


def test_promote_refuses_the_active_version():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, KERNEL_ACTIVE)
        for v in ("e1", "e2"):
            _candidate(name, v)
            _promote(name, v)
        with pytest.raises(registry.PromotionRefused, match="already active"):
            run_on_conn(name, lambda c: registry.promote(c, "e2", by="owner"))
        assert fetch(name, "SELECT status FROM model.registry WHERE version = 'e1'") == [("previous",)]


def test_set_gate_and_reject_refuse_a_non_candidate():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, KERNEL_ACTIVE)
        _candidate(name, "e1")
        run_on_conn(name, lambda c: registry.reject(c, "e1"))
        assert fetch(name, "SELECT status FROM model.registry WHERE version = 'e1'") == [("rejected",)]
        with pytest.raises(registry.PromotionRefused, match="not a candidate"):
            run_on_conn(name, lambda c: registry.reject(c, "e1"))
        with pytest.raises(registry.PromotionRefused, match="not a candidate"):
            run_on_conn(name, lambda c: registry.set_gate(c, "e1", {"passed": True}, {}, 1))
        with pytest.raises(registry.PromotionRefused, match="not a candidate"):
            run_on_conn(name, lambda c: registry.reject(c, "k0"))


def _real_bundle(version: str) -> bytes:
    return ModelBundle(version=version, family="eb_v1", arrays={"coef": np.array([0.25, 2.0])},
                       manifest={"phi": 1.5}).to_bytes()


def test_load_bundle_verifies_sha256():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        _insert(name, "e1", _real_bundle("e1"))
        back = run_on_conn(name, lambda c: registry.load_bundle(c, "e1"))
        np.testing.assert_array_equal(back.arrays["coef"], [0.25, 2.0])
        run_sql(name, "UPDATE model.registry SET bundle = bundle || '\\x00'::bytea WHERE version = 'e1'")
        with pytest.raises(registry.BundleCorrupt, match="sha256"):
            run_on_conn(name, lambda c: registry.load_bundle(c, "e1"))


def test_load_bundle_refuses_a_bundle_of_another_version():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        _insert(name, "e1", _real_bundle("e0"))
        with pytest.raises(registry.BundleCorrupt, match="declares version e0"):
            run_on_conn(name, lambda c: registry.load_bundle(c, "e1"))


def test_prune():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, KERNEL_ACTIVE)
        for v in ("e1", "e2", "e3"):
            _candidate(name, v)
            _promote(name, v)
        _candidate(name, "r1")
        run_on_conn(name, lambda c: registry.reject(c, "r1"))
        _candidate(name, "c1")
        run_sql(name, "UPDATE model.registry SET created_at = '2026-01-01'")
        _candidate(name, "fresh")
        run_on_conn(name, lambda c: registry.reject(c, "fresh"))
        run_sql(name, "UPDATE model.registry SET created_at = '2026-09-01' WHERE version = 'fresh'")

        report = run_on_conn(name, lambda c: registry.prune(c, today=date(2026, 10, 22)))

        assert report == registry.PruneReport(scores_deleted=3, bundles_nulled=3)
        assert sorted(fetch(name, "SELECT model_version FROM model.route_scores")) == [
            ("e2",), ("e3",), ("fresh",)]
        assert sorted(fetch(name, "SELECT version FROM model.registry WHERE bundle IS NOT NULL")) == [
            ("e2",), ("e3",), ("fresh",)]
        assert len(fetch(name, "SELECT 1 FROM model.registry")) == 7
        with pytest.raises(registry.BundleMissing, match="pruned"):
            run_on_conn(name, lambda c: registry.load_bundle(c, "e1"))
