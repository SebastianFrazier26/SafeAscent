"""The train CLI end to end against a throwaway migrated database seeded with a synthetic world. The
old kernel's per-cell math is replaced by a cheap stand-in (pre-flight F41); test_kernel_adapter covers
the real one. Each step depends on the one before, so this is one ordered test."""

import json
import logging
import re

import pytest

import app.ml.kernel_ref.safety_algorithm_vectorized as kernel_mod
from app.config import settings
from app.ml import train
from app.ml.fixtures import TRAINING_WORLD, seed_training_db, synthetic_raw
from app.ml.kernel_ref.safety_algorithm import SafetyPrediction
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, run_sql, sa_url

pytestmark = requires_pg
TODAY = "2026-10-20"
KERNEL_ROW = ("INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, promoted_at, "
              "promoted_by) VALUES ('kernel-2026-10-01', 'kernel', 'h', '1990-01-01', '2026-10-01', 'active', now(), "
              "'owner')")
SYNTHETIC_ID = re.compile(r"9\d{8}")
SCATTER = ("UPDATE {table} t SET {column} = r.route_id FROM (SELECT route_id, row_number() OVER (ORDER BY route_id) - 1 "
           "AS i, count(*) OVER () AS n FROM canonical_routes) r {where} "
           "{and_} r.i = (t.accident_id::bigint * 7919) % r.n")


def _count_kernel(**kw):
    return SafetyPrediction(risk_score=float(len(kw["accidents"])), num_contributing_accidents=0,
                            top_contributing_accidents=[], metadata={})


@pytest.fixture(scope="module")
def db():
    raw = synthetic_raw(**TRAINING_WORLD)
    with migrated_db() as name:
        run_on_conn(name, lambda c: seed_training_db(c, raw))
        run_sql(name, KERNEL_ROW)
        yield name


def _cli(capsys, *argv):
    rc = train.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def test_run_promotes_then_rejects_corrupted_data_and_rollback_refuses(db, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(kernel_mod, "calculate_safety_score_vectorized", _count_kernel)
    monkeypatch.setattr(settings, "TRAINER_DATABASE_URL", sa_url(db))
    monkeypatch.setattr(settings, "HEALTHCHECKS_TRAIN_URL", None)

    first = tmp_path / "first"
    rc, out, _ = _cli(capsys, "run", "--today", TODAY, "--out-dir", str(first))
    assert (rc, out) == (0, f"promoted eb-{TODAY}\n")
    assert dict(fetch(db, "SELECT version, status FROM model.registry")) == {
        "kernel-2026-10-01": "retired", f"eb-{TODAY}": "active"}
    (scores,) = fetch(db, f"SELECT count(*) FROM model.route_scores WHERE model_version = 'eb-{TODAY}'")[0]
    assert scores > 0 and fetch(db, f"SELECT scores_rows FROM model.registry WHERE version = 'eb-{TODAY}'") == [
        (scores,)]
    md = (first / "issue.md").read_text()
    assert "promoted" in md and not SYNTHETIC_ID.search(md)
    status = json.loads((first / "status.json").read_text())
    assert status == {"version": f"eb-{TODAY}", "promoted": True, "large_shift": True, "rejected_reason": None}

    # Every incident in one area: a spatial fold holding that area's state group out has no training
    # incident, so its GLM cannot fit. The run fails before any write; nothing is left behind.
    route = fetch(db, "SELECT min(route_id) FROM canonical_routes")[0][0]
    run_sql(db, f"UPDATE accidents SET mp_route_id = {route} WHERE mp_route_id IS NOT NULL")
    run_sql(db, f"UPDATE accident_legacy_links SET route_id = {route}")
    rc, out, _ = _cli(capsys, "run", "--today", TODAY, "--out-dir", str(tmp_path / "one_area"))
    assert (rc, out) == (1, "failed: ModelFitError\n")
    assert fetch(db, "SELECT count(*) FROM model.registry") == [(2,)]
    assert not (tmp_path / "one_area" / "status.json").exists()

    # Every incident scattered over routes by a hash of its id: exposure alone explains the counts, so
    # pooling gains nothing over geo-only and the gate rejects.
    run_sql(db, SCATTER.format(table="accidents", column="mp_route_id", where="WHERE t.mp_route_id IS NOT NULL", and_="AND"))
    run_sql(db, SCATTER.format(table="accident_legacy_links", column="route_id", where="", and_="WHERE"))
    second = tmp_path / "second"
    rc, out, _ = _cli(capsys, "run", "--today", TODAY, "--out-dir", str(second))
    assert rc == 0 and out.startswith(f"rejected eb-{TODAY}.2: ") and out.count("\n") == 1, out
    status = json.loads((second / "status.json").read_text())
    assert status["promoted"] is False and status["rejected_reason"]
    assert set(status["rejected_reason"].split(", ")) == set(out.split(": ", 1)[1].strip().split(", "))
    assert fetch(db, f"SELECT status FROM model.registry WHERE version = 'eb-{TODAY}.2'") == [("rejected",)]
    assert fetch(db, "SELECT version FROM model.registry WHERE status = 'active'") == [(f"eb-{TODAY}",)]
    assert not SYNTHETIC_ID.search((second / "issue.md").read_text())

    # The kernel retired at the first promotion (DP15), so there is no previous to restore.
    rc, out, err = _cli(capsys, "rollback")
    assert rc != 0
    assert out == "refused: no previous version to restore; promote a specific version with TRAIN rollback --version\n"
    assert "Traceback" not in err


@pytest.mark.parametrize("url", ["postgresql+asyncpg://trainer:sekret@127.0.0.1:9/t20db",
                                 "postgresql+asyncpg://trainer:sekret@localhost:1/t20db"])
def test_a_connection_failure_prints_the_class_only(url, tmp_path, capsys, caplog, monkeypatch):
    monkeypatch.setattr(settings, "TRAINER_DATABASE_URL", url)
    monkeypatch.setattr(settings, "HEALTHCHECKS_TRAIN_URL", None)
    for name in ("issue.md", "status.json"):
        (tmp_path / name).write_text("from an earlier successful run\n")
    with caplog.at_level(logging.DEBUG):
        rc, out, err = _cli(capsys, "run", "--today", TODAY, "--out-dir", str(tmp_path))
    assert rc == 1 and re.fullmatch(r"failed: \w+\n", out), out
    seen = out + err + caplog.text
    for leak in ("127.0.0.1", "localhost", "t20db", "sekret", "trainer:", "SELECT", "FROM"):
        assert leak not in seen
    assert not (tmp_path / "status.json").exists() and not (tmp_path / "issue.md").exists()


def test_kernel_baseline_seeds_an_empty_registry_once(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(kernel_mod, "calculate_safety_score_vectorized", _count_kernel)
    raw = synthetic_raw(seed=0, n_areas=48)
    with migrated_db() as name:
        run_on_conn(name, lambda c: seed_training_db(c, raw))
        monkeypatch.setattr(settings, "TRAINER_DATABASE_URL", sa_url(name))
        rc, out, _ = _cli(capsys, "kernel-baseline", "--today", TODAY)
        assert (rc, out) == (0, f"kernel baseline kernel-{TODAY}\n")
        row = fetch(name, "SELECT family, status, bundle IS NULL, metrics -> 'main' -> 'by_kind' ? 'spatial', "
                          "metrics -> 'main' -> 'by_kind' ? 'rolling' FROM model.registry")
        assert row == [("kernel", "active", True, True, True)]
        rc, out, _ = _cli(capsys, "kernel-baseline", "--today", TODAY)
        assert rc == 1 and out.startswith("refused: the registry is not empty")
        rc, out, _ = _cli(capsys, "rollback", "--version", f"kernel-{TODAY}")
        assert (rc, out) == (1, "refused: the kernel family is never re-promoted (DP15)\n")
        assert fetch(name, "SELECT count(*) FROM model.registry") == [(1,)]
