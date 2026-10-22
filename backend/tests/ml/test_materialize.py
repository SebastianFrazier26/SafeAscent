import math
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.ml import registry
from app.ml.cells import TYPE_GROUPS
from app.ml.fixtures import SYNTHETIC_ID_FLOOR, STATES, fitted_toy
from app.ml.materialize import read_routes, write_scores
from app.ml.posterior import Thresholds, summarize
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn
from tests.test_migration_0012 import SEED

pytestmark = requires_pg
VERSION = "eb-test"
UNKNOWN_ROUTE = SYNTHETIC_ID_FLOOR + 5_000_000
NUMBERS = ("rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05", "pct_q95", "n_eff_geo", "n_eff_feature",
           "per10k_mean", "per10k_q05", "per10k_q95")


def _scored_toy():
    # The target trad area has no incident and no archetype incident; with evidence_far its July cell has
    # no neighbour within d*, so that one cell is insufficient. No ticks there: per10k is missing.
    toy = fitted_toy(local_incidents=0, archetype_incidents=0, tick_coverage=None, ticks_value=None)
    th = Thresholds(d_star=1.0, conf_high=2.0, conf_medium=4.0, phi=1.0)
    return toy, summarize(**toy.inputs(th, evidence_far=True))


def _catalog_sql(toy) -> str:
    cells = toy.cells
    rows = []
    for i, area_id in enumerate(cells.area_id.tolist()):
        st = cells.states[int(cells.state_idx[i])]
        root = SYNTHETIC_ID_FLOOR + 100 * (1 + tuple(STATES).index(st))
        rows.append((int(area_id), st, root))
    out = []
    for root, st in sorted({(r, s) for _, s, r in rows}):
        out.append(f"INSERT INTO mp_locations (mp_id, name) VALUES ({root}, 'Fixture Root'), ({root + 1}, 'Fixture "
                   f"Region') ON CONFLICT DO NOTHING;")
        out.append(f"INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, "
                   f"coord_precision, catalog_version) VALUES ({root}, NULL, '{root}', 1, 'us', '{st}', 'none', 'v'), "
                   f"({root + 1}, {root}, '{root}.{root + 1}', 2, 'us', '{st}', 'none', 'v') ON CONFLICT DO NOTHING;")
    for area_id, st, root in rows:
        out.append(f"INSERT INTO mp_locations (mp_id, name, parent_id) VALUES ({area_id}, 'Fixture Area', {root + 1});")
        out.append(f"INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, "
                   f"coord_precision, coord_source_id, point_key, catalog_version) VALUES ({area_id}, {root + 1}, "
                   f"'{root}.{root + 1}.{area_id}', 3, 'us', '{st}', 40, -105, 'own', {area_id}, 'k{area_id}', 'v');")
        out.append(f"INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES ({area_id + 10_000_000}, "
                   f"'Fixture Route', {area_id});")
        out.append(f"INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, "
                   f"type_rule_version, scorable, facts_sha256, catalog_version) VALUES ({area_id + 10_000_000}, "
                   f"{area_id}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, 'x', 'v');")
    target = rows[0][0]
    out.append(f"INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES ({UNKNOWN_ROUTE}, 'Fixture Route', "
               f"{target});")
    out.append(f"INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, "
               f"type_rule_version, scorable, facts_sha256, catalog_version) VALUES ({UNKNOWN_ROUTE}, {target}, "
               f"'unknown', false, '{{}}', 'rt-mp-v3', true, 'x', 'v');")
    return "\n".join(out)


async def _write(conn, toy, summary, insert: bool = True) -> int:
    if insert:
        await registry.insert_candidate(
            conn, version=VERSION, family="eb_v1", data_hash="h", train_start=date(1990, 1, 1),
            data_cutoff=date(2026, 10, 1), exposure_as_of=None, metrics={}, thresholds={},
            model_card={}, bundle=b"zip", parent_version=None)
    return await write_scores(conn, VERSION, toy.cells, summary, await read_routes(conn))


@pytest.fixture(scope="module")
def scored():
    toy, summary = _scored_toy()
    with migrated_db(seed_sql=SEED + _catalog_sql(toy)) as name:
        n = run_on_conn(name, lambda c: _write(c, toy, summary))
        yield name, toy, summary, n


def test_rows_land_with_null_exactly_where_the_summary_is_nan(scored):
    name, toy, summary, n = scored
    cells = toy.cells
    rows = fetch(name, f"SELECT area_id, month, type_group, {', '.join(NUMBERS)} FROM model.route_scores "
                       f"WHERE model_version = '{VERSION}'")
    assert n == len(rows) == cells.n  # one trad route per toy area, 12 months each
    gi = TYPE_GROUPS.index("trad")
    nulls = 0
    for area_id, month, group, *values in rows:
        c = cells.index[(area_id, gi, month)]
        assert group == "trad"
        for col, v in zip(NUMBERS, values):
            expected = float(getattr(summary, col)[c])
            if math.isnan(expected):
                assert v is None, (col, c)
                nulls += 1
            else:
                assert v == pytest.approx(expected, rel=1e-6), (col, c)
    assert nulls > 0, "the fixture must exercise missing numbers"


def test_an_insufficient_cell_is_stored_insufficient(scored):
    name, toy, summary, _ = scored
    assert summary.insufficient.sum() == 1
    c = int(summary.insufficient.nonzero()[0][0])
    area_id, month = int(toy.cells.area_id[toy.cells.area_idx[c]]), int(toy.cells.month[c])
    assert fetch(name, f"SELECT insufficient, insufficient_reason, conf_level, rr_mean, percentile FROM "
                       f"model.route_scores WHERE area_id = {area_id} AND month = {month}") == [
        (True, "no_evidence", "insufficient", None, None)]


def test_a_second_write_for_the_same_version_fails_on_the_key(scored):
    name, toy, summary, n = scored
    with pytest.raises(IntegrityError):
        run_on_conn(name, lambda c: _write(c, toy, summary, insert=False))
    assert fetch(name, f"SELECT count(*) FROM model.route_scores WHERE model_version = '{VERSION}'") == [(n,)]


def test_nothing_is_written_for_an_unknown_type_route(scored):
    name, *_ = scored
    assert fetch(name, f"SELECT count(*) FROM model.route_scores WHERE route_id = {UNKNOWN_ROUTE}") == [(0,)]
    # The SEED route sits in an area the toy cells do not hold: no cell, no row.
    assert fetch(name, "SELECT count(*) FROM model.route_scores WHERE route_id = 900000001") == [(0,)]
