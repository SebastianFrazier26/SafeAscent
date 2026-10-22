"""P3 Task 25 (G6, A:140): one stored row, one answer. /safety, map-with-safety and /risk-breakdown
return the same risk_score, color, confidence, range and relative_rate for every route, and the
breakdown's terms rebuild the stored rr_mean.

Stored rows are per cell (area x type group x month), so the breakdown reads the CELL's static
features: the minimum over the area's scorable routes, as training did. The three routes of area A
therefore carry different elevations and latitudes, and only the minimum reproduces the toy cell the
stored rr_mean was fitted on. A per-route read would put a gap into the evidence residual."""

import math
from types import SimpleNamespace

import pytest

pytest.importorskip("scipy")  # the toy bundle comes from the train group's posterior code

from app.api.v1 import mp_routes  # noqa: E402
from app.ml.bundle import ModelBundle, sha256  # noqa: E402
from app.ml.explain import EVIDENCE, explain  # noqa: E402
from app.ml.fixtures import bundle_reference_terms, toy_bundle_and_row  # noqa: E402
from tests.pgtest import migrated_db, sa_url  # noqa: E402
from tests.test_migration_0012 import SEED  # noqa: E402
from tests.test_migrations import ADMIN_URL  # noqa: E402

VERSION = "eb_v1-t25-parity"
JULY = {"target_date": "2026-07-04"}
SAFETY = "/api/v1/mp-routes/{}/safety"
BREAKDOWN = "/api/v1/mp-routes/{}/risk-breakdown"
MAP = "/api/v1/mp-routes/map-with-safety"

AREA_A, AREA_B, AREA_C = 900000110, 900000120, 900000130
A_ROUTES = (900000001, 900000002, 900000003)  # elevation 2600/2500/2700, lat 44.0/44.5/45.0: the cell is the min
B_ROUTES = (900000004, 900000005)             # no static features at all
C_ROUTES = (900000006,)                       # insufficient: no evidence
ALL_ROUTES = A_ROUTES + B_ROUTES + C_ROUTES
BUCKET = 4242

EXTRA = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  ({AREA_B}, 'Fixture Crag B', 900000100, 40.3, -105.4), ({AREA_C}, 'Fixture Crag C', 900000100, 40.4, -105.5);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (900000002, 'Fixture Route 2', {AREA_A}, 'Trad'), (900000003, 'Fixture Route 3', {AREA_A}, 'Trad'),
  (900000004, 'Fixture Route 4', {AREA_B}, 'Trad'), (900000005, 'Fixture Route 5', {AREA_B}, 'Trad'),
  (900000006, 'Fixture Route 6', {AREA_C}, 'Trad');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  ({AREA_B}, 900000100, '900000100.{AREA_B}', 2, 'us', 'CO', 40.3, -105.4, 'own', {AREA_B}, '40.30000:-105.40000', 'v'),
  ({AREA_C}, 900000100, '900000100.{AREA_C}', 2, 'us', 'CO', 40.4, -105.5, 'own', {AREA_C}, '40.40000:-105.50000', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000002, {AREA_A}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'x', 'v'),
  (900000003, {AREA_A}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'x', 'v'),
  (900000004, {AREA_B}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'x', 'v'),
  (900000005, {AREA_B}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'x', 'v'),
  (900000006, {AREA_C}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'x', 'v');
INSERT INTO route_static_features (route_id, source, type_group, lat, elevation_m, grid_bucket, coord_precision,
  feature_version) VALUES
  (900000001, 'mp', 'trad', 44.0, 2600, {BUCKET}, 'own', 'v'),
  (900000002, 'mp', 'trad', 44.5, 2500, {BUCKET}, 'own', 'v'),
  (900000003, 'mp', 'trad', 45.0, 2700, {BUCKET}, 'own', 'v');
INSERT INTO cell_normals_status (grid_bucket, status) VALUES ({BUCKET}, 'complete');
INSERT INTO cell_climate_normals (grid_bucket, month, tmax_mean, tmin_mean, precip_mm, snowfall_cm, freeze_thaw_days,
  n_years, period_start_year, period_end_year, source, normals_version)
  SELECT {BUCKET}, m, 15, 3, 50, 5, 4, 30, 1991, 2020, 'open_meteo_era5_seamless', 'v' FROM generate_series(1, 12) m;
"""


def _ok(area: int, route: int, rr: float, pct: tuple[float, float, float], conf: str, neff: tuple[float, float]) -> str:
    return (f"('{VERSION}', {route}, 7, 'trad', {area}, false, NULL, {rr!r}, {rr * 0.5!r}, {rr * 2!r}, {pct[0]}, "
            f"{pct[1]}, {pct[2]}, '{conf}', {neff[0]!r}, {neff[1]!r}, NULL, NULL, NULL, 'ticks_missing')")


@pytest.fixture(scope="module")
def toy():
    bundle, row = toy_bundle_and_row()
    return ModelBundle(VERSION, bundle.family, bundle.arrays, bundle.manifest), row


@pytest.fixture
def parity_db(scores_redis, toy):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db.session import get_db
    from app.main import app
    from tests.pgtest import run_sql

    if not ADMIN_URL:
        pytest.skip("MIGRATIONS_TEST_ADMIN_URL not set")
    bundle, row = toy
    raw = bundle.to_bytes()
    rows = [_ok(AREA_A, r, row["rr_mean"], (55.26, 40.0, 70.0), "high", (row["n_eff_geo"], row["n_eff_feature"]))
            for r in A_ROUTES]
    rows += [_ok(AREA_B, r, 0.8, (30.36, 20.0, 45.0), "medium", (1.0, 2.0)) for r in B_ROUTES]
    rows.append(f"('{VERSION}', {C_ROUTES[0]}, 7, 'trad', {AREA_C}, true, 'no_evidence', NULL, NULL, NULL, NULL, NULL, "
                "NULL, 'insufficient', 0, 0, NULL, NULL, NULL, NULL)")
    seed = (SEED + EXTRA
            + "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, promoted_at, "
              "bundle, bundle_sha256, bundle_bytes, scores_rows) VALUES "
              f"('{VERSION}', 'eb_v1', 'h', '1990-01-01', '2026-10-01', 'active', now(), '\\x{raw.hex()}'::bytea, "
              f"'{sha256(raw)}', {len(raw)}, {len(rows)});\n"
            + "INSERT INTO model.route_scores (model_version, route_id, month, type_group, area_id, insufficient, "
              "insufficient_reason, rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95, conf_level, n_eff_geo, "
              "n_eff_feature, per10k_mean, per10k_q05, per10k_q95, per10k_reason) VALUES\n  " + ",\n  ".join(rows) + ";\n")
    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)

        async def override():
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with AsyncSession(engine) as session:
                    yield session
            finally:
                await engine.dispose()

        app.dependency_overrides[get_db] = override
        try:
            yield SimpleNamespace(db=name, run=lambda sql: run_sql(name, sql))
        finally:
            app.dependency_overrides.pop(get_db, None)


COMMON = ("risk_score", "color_code", "data_status", "confidence", "score_range", "relative_rate", "model_version")


def _view(body: dict) -> dict:
    return {k: body[k] for k in COMMON}


def _all_surfaces(client) -> dict[int, dict[str, dict]]:
    mapped = {r["mp_route_id"]: r["safety"] for r in client.get(MAP, params=JULY).json()["routes"]}
    out = {}
    for rid in ALL_ROUTES:
        safety = client.post(SAFETY.format(rid), params=JULY)
        breakdown = client.get(BREAKDOWN.format(rid), params=JULY)
        assert safety.status_code == 200 and breakdown.status_code == 200, rid
        assert mapped[rid] is not None, f"route {rid} missing from the map"
        out[rid] = {"safety": _view(safety.json()), "map": _view(mapped[rid]), "breakdown": _view(breakdown.json())}
    return out


def test_every_endpoint_serves_the_same_score(test_client, parity_db, toy):
    bundle, row = toy
    surfaces = _all_surfaces(test_client)
    assert len(surfaces) == len(ALL_ROUTES) == 6
    for rid, views in surfaces.items():
        assert views["safety"] == views["map"] == views["breakdown"], rid

    a = surfaces[A_ROUTES[0]]["safety"]
    assert (a["risk_score"], a["color_code"], a["confidence"], a["score_range"]) == (55.3, "orange", "high", [40.0, 70.0])
    # relative_rate is RR x r_ref, r_ref = exp(-const) from the same bundle every endpoint reads.
    r_ref = math.exp(-bundle.manifest["const"]["trad"]["7"])
    for key, rr in (("mean", row["rr_mean"]), ("lo", row["rr_mean"] * 0.5), ("hi", row["rr_mean"] * 2)):
        assert a["relative_rate"][key] == pytest.approx(rr * r_ref, rel=1e-3)
    assert a["relative_rate"]["mean"] != pytest.approx(row["rr_mean"], rel=1e-3)
    b = surfaces[B_ROUTES[0]]["safety"]
    assert (b["risk_score"], b["confidence"], b["score_range"]) == (30.4, "medium", [20.0, 45.0])
    c = surfaces[C_ROUTES[0]]["safety"]
    assert (c["risk_score"], c["color_code"], c["data_status"], c["confidence"], c["score_range"],
            c["relative_rate"]) == (None, "gray", "insufficient_data", None, None, None)
    # Routes of one cell are one score.
    assert len({repr(surfaces[r]["safety"]) for r in A_ROUTES}) == 1


def test_a_changed_row_changes_every_endpoint_together(test_client, parity_db):
    parity_db.run("UPDATE model.route_scores SET percentile = 62.46, pct_q05 = 41.0, pct_q95 = 80.0 "
                  f"WHERE route_id = {B_ROUTES[1]}")
    surfaces = _all_surfaces(test_client)
    for views in surfaces[B_ROUTES[1]].values():
        assert (views["risk_score"], views["score_range"]) == (62.5, [41.0, 80.0])


def test_breakdown_terms_rebuild_the_stored_rr_from_the_cell_features(test_client, parity_db, toy, monkeypatch):
    bundle, row = toy
    seen: list[dict] = []

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return explain(*args, **kwargs)

    monkeypatch.setattr(mp_routes, "explain", spy)
    ref = bundle_reference_terms(bundle, row)
    for rid in A_ROUTES:
        seen.clear()
        body = test_client.get(BREAKDOWN.format(rid), params=JULY).json()
        [kwargs] = seen
        # The cell's minimum, not the route's own value.
        assert kwargs["raw_features"]["elevation_m"] == 2500.0 and kwargs["raw_features"]["lat"] == 44.0
        assert kwargs["rr_mean"] == pytest.approx(row["rr_mean"], rel=1e-12)

        full = explain(bundle, **{**kwargs, "top": None})
        assert math.exp(sum(t.log_effect for t in full)) == pytest.approx(row["rr_mean"], rel=1e-6)
        assert sum(t.log_effect for t in full if t.name != EVIDENCE) == pytest.approx(ref.log_hazard_over_rref, abs=1e-6)
        assert full[-1].name == EVIDENCE and full[-1].log_effect == pytest.approx(ref.log_theta_over_mean, abs=1e-6)

        # What the client gets is the same decomposition, rounded to 4 decimals a term.
        assert body["terms"][-1]["name"] == EVIDENCE
        assert math.exp(sum(t["log_effect"] for t in body["terms"])) == pytest.approx(row["rr_mean"], rel=1e-3)


def test_a_cell_without_static_features_still_decomposes_to_its_rr(test_client, parity_db, toy):
    bundle, _ = toy
    body = test_client.get(BREAKDOWN.format(B_ROUTES[0]), params=JULY).json()
    assert body["terms"][-1]["name"] == EVIDENCE
    assert math.exp(sum(t["log_effect"] for t in body["terms"])) == pytest.approx(0.8, rel=1e-3)
    assert body["risk_score"] == 30.4
