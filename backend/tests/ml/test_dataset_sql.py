from datetime import date

import pytest

from app.ml.dataset import (
    StaleExposure,
    pick_snapshot,
    read_area_paths,
    read_areas,
    read_exposure,
    read_incidents,
    read_static,
)
from app.ml.incidents import AreaLookup, interval, place
from tests.pgtest import migrated_db, requires_pg, run_on_conn

pytestmark = requires_pg
KEY = "40.20000:-105.30000"
PRESENT = '{"value": 0.4, "missing": false, "n_days": 300}'
TICKS_ZERO = '{"value": 0.0, "missing": false, "per_route_mean": 0.0, "coverage": 1.0, "n_complete": 1}'
ABSENT = '{"value": null, "missing": true, "reason": "no_era5"}'
SEED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000110, 'Fixture Crag', 900000100, 40.2, -105.3),
  (900000200, 'Utah', NULL, NULL, NULL), (900000210, 'Fixture Gully', 900000200, 38.5, -109.5);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (900000001, 'Fixture Route', 900000110, 'Trad'), (900000002, 'Fixture Ice', 900000210, 'Ice');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  (900000110, 900000100, '900000100.900000110', 2, 'us', 'CO', 40.2, -105.3, 'own', 900000110, '{KEY}', 'v'),
  (900000200, NULL, '900000200', 1, 'us', 'UT', NULL, NULL, 'none', NULL, NULL, 'v'),
  (900000210, 900000200, '900000200.900000210', 2, 'us', 'UT', 38.5, -109.5, 'own', 900000210,
   '38.50000:-109.50000', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000001, 900000110, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'x', 'v'),
  (900000002, 900000210, 'ice', false, '{{ice}}', 'rt-mp-v3', true, NULL, 'x', 'v');
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, elevation_m, dem_source, dem_res_m,
  feature_version) VALUES ('{KEY}', 40.2, -105.3, 4024952, 'a', 'b', 'America/Denver', 2400, '3dep_13', 10, 'f-v1');
INSERT INTO route_static_features (route_id, source, point_key, lat, elevation_m, grid_bucket, coord_precision,
  feature_version) VALUES
  (900000001, 'mp', '{KEY}', 40.2, 2400, 4024952, 'own', 'f-v1'),
  (900000002, 'mp', NULL, 38.5, NULL, 3854595, 'own', 'f-v1');
INSERT INTO cell_normals_status (grid_bucket, status) VALUES (4024952, 'complete');
INSERT INTO cell_climate_normals (grid_bucket, month, tmax_mean, tmin_mean, precip_mm, snowfall_cm, freeze_thaw_days,
  n_years, period_start_year, period_end_year, source, normals_version)
  SELECT 4024952, m, 10, 0, 0, 0, 0, 10, 2016, 2025, 'derived_cell_daily_conditions', 'n-v2'
  FROM generate_series(1, 12) m;
INSERT INTO accidents (accident_id, source, date, year, date_precision, year_lo, year_hi, state, is_canonical,
  activity_class, country, excluded_reason, geocode_precision, mp_route_id) VALUES
  (900000901, 'AAC', '2010-07-14', 2010, 'day', NULL, NULL, 'Colorado', true, 'climbing', 'US', NULL, 'area', 900000001),
  (900000902, 'AAC', NULL, 2003, 'year', 2003, 2005, 'Utah', true, 'climbing', 'US', NULL, 'unknown', NULL),
  (900000903, 'AAC', '2023-07-02', 2023, 'unknown', NULL, NULL, 'Utah', true, 'climbing', 'US', 'year_unverified',
   'area', NULL),
  (900000904, 'NPS', NULL, 2001, 'year', NULL, NULL, 'Colorado', true, 'climbing', 'US', NULL, 'unknown', NULL);
INSERT INTO internal.exposure_index (area_id, type_group, month, n_routes, proxy, proxy_version, as_of, data_cutoff,
  tick_coverage) VALUES
  (900000110, 'trad', 1, 1, '{{"season_share": {PRESENT}, "mp_ticks": {TICKS_ZERO}}}', 'x-mp-v1',
   '2026-10-01', '2026-10-01', 1.0),
  (900000110, 'trad', 2, 1, '{{"season_share": {ABSENT}, "mp_ticks": {ABSENT}}}', 'x-mp-v1',
   '2026-10-01', '2026-10-01', NULL),
  (900000110, 'trad', 1, 1, '{{"season_share": {PRESENT}, "mp_ticks": {ABSENT}}}', 'x-mp-v1',
   '2026-10-01', '2004-01-01', 0.0);
"""


@pytest.fixture(scope="module")
def db():
    with migrated_db(seed_sql=SEED) as name:
        yield name


def test_read_incidents_skips_excluded_and_carries_link_and_year(db):
    rows = {r.accident_id: r for r in run_on_conn(db, read_incidents)}
    assert set(rows) == {900000901, 900000902, 900000904}
    linked = rows[900000901]
    assert (linked.link_method, linked.link_route_id, linked.route_type_group, linked.route_area_id,
            linked.route_scorable) == ("mp_route_id", 900000001, "trad", 900000110, True)
    assert (rows[900000902].year_lo, rows[900000902].year_hi) == (2003, 2005)
    assert interval(rows[900000902]) == (date(2003, 1, 1), date(2006, 1, 1))
    # no date and no year range: the year column alone makes it an interval, never no_date
    assert rows[900000904].year == 2001 and rows[900000904].date is None
    assert interval(rows[900000904]) == (date(2001, 1, 1), date(2002, 1, 1))
    areas = AreaLookup(run_on_conn(db, read_areas))
    assert place(linked, areas).level == "cell"
    assert (place(rows[900000904], areas).level, place(rows[900000904], areas).reason) == ("state", "untrusted_precision")


def test_read_areas_and_paths(db):
    areas = run_on_conn(db, read_areas)
    assert [(a.area_id, a.state_code, dict(a.routes_by_group)) for a in areas] == [
        (900000110, "CO", {"trad": 1}), (900000210, "UT", {"ice": 1})]
    assert run_on_conn(db, read_area_paths) == {900000110: "900000100.900000110",
                                                900000210: "900000200.900000210"}


def test_read_static_keeps_missing_as_none(db):
    static = {(s.area_id, s.month): s.values for s in run_on_conn(db, read_static)}
    assert len(static) == 24
    assert static[(900000110, 7)] == (2400.0, 40.2, 10.0, 0.0, 0.0, 0.0, 0.0)  # measured zeros stay zeros
    assert static[(900000210, 7)] == (None, 38.5, None, None, None, None, None)  # unsampled, normals pending


def test_pick_snapshot(db):
    def pick(**kw):
        return run_on_conn(db, lambda c: pick_snapshot(c, **kw))

    assert pick(data_cutoff=date(2004, 1, 1), production=False, today=date(2026, 12, 1)) == date(2026, 10, 1)
    assert pick(data_cutoff=date(2026, 10, 1), production=True, today=date(2026, 10, 20)) == date(2026, 10, 1)
    with pytest.raises(StaleExposure, match="2026-10-01"):
        pick(data_cutoff=date(2026, 10, 1), production=True, today=date(2026, 12, 1))
    with pytest.raises(StaleExposure, match="1999-01-01"):
        pick(data_cutoff=date(1999, 1, 1), production=False, today=date(2026, 12, 1))


def test_read_exposure_missing_is_none_and_zero_is_zero(db):
    rows = {r.month: r for r in run_on_conn(
        db, lambda c: read_exposure(c, as_of=date(2026, 10, 1), data_cutoff=date(2026, 10, 1)))}
    assert set(rows) == {1, 2}
    assert (rows[1].season_share, rows[1].ticks_value, rows[1].ticks_per_route, rows[1].tick_coverage) == (
        pytest.approx(0.4), 0.0, 0.0, 1.0)
    assert (rows[2].season_share, rows[2].ticks_value, rows[2].ticks_per_route, rows[2].tick_coverage) == (
        None, None, None, None)
    [back] = run_on_conn(db, lambda c: read_exposure(c, as_of=date(2026, 10, 1), data_cutoff=date(2004, 1, 1)))
    assert (back.ticks_value, back.tick_coverage) == (None, 0.0)
