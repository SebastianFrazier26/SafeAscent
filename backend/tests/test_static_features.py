import math
import uuid

from sqlalchemy import text

from app.pipelines.dem import Elevation
from app.pipelines.epqs import EpqsClient
from app.pipelines.grid import grid_bucket, point_key
from app.pipelines.mp_catalog import refresh
from app.pipelines.static_features import build_route_features, epqs_check, fill_elevation, run_step, upsert_points
from app.pipelines.validate import ValidationReport
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, run_on_engine

PIN = "0009_static_features"
DENVER = "America/Denver"
SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL),
  (900000110, 'Fixture Region', 900000100, 40.6, -105.8),
  (900000111, 'Fixture Crag', 900000110, 40.123456, -105.654321),
  (900000112, 'Fixture Copy', 900000110, 40.6, -105.8),
  (900000113, 'Fixture Nowhere', 900000110, NULL, NULL),
  (900000114, 'Fixture Boulders', 900000110, 40.7, -105.9);
INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade, pitches, length_ft) VALUES
  (900000001, 'Fixture One', 900000111, 'Trad', '5.9', 2, 300),
  (900000002, 'Fixture Two', 900000112, 'Sport', '5.10a', 1.5, NULL),
  (900000003, 'Fixture Three', 900000113, 'Trad', '5.7', NULL, NULL),
  (900000004, 'Fixture Four', 900000114, 'Boulder', 'V3', NULL, NULL),
  (900000005, 'Fixture Five', 900000111, 'unknown', '5.8', NULL, NULL);
"""
CRAG_KEY, REGION_KEY = "40.12346:-105.65432", "40.60000:-105.80000"


def _catalog(name):
    assert run_on_conn(name, lambda c: refresh(c, run_id=uuid.uuid4())).status == "ok"


def _points(conn, run_id, tz=DENVER, report=None):
    return upsert_points(conn, run_id=run_id, report=report or ValidationReport("t"), tz_lookup=lambda la, lo: tz)


@requires_pg
def test_points_come_from_route_bearing_areas_and_register_buckets():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        assert run_on_conn(name, lambda c: _points(c, uuid.uuid4())) == 2
        keys = run_on_conn(name, lambda c: c.execute(text("SELECT point_key FROM feature_points ORDER BY 1")))
        assert keys == [(CRAG_KEY,), (REGION_KEY,)]
        status = run_on_conn(name, lambda c: c.execute(text("SELECT DISTINCT status FROM cell_normals_status")))
        series = run_on_conn(name, lambda c: c.execute(text("SELECT DISTINCT tz FROM grid_bucket_series")))
        assert status == [("pending",)] and series == [(DENVER,)]


@requires_pg
def test_unresolvable_timezone_is_quarantined_and_not_registered():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        report = ValidationReport("t")
        run_on_conn(name, lambda c: _points(c, uuid.uuid4(), tz=None, report=report))
        assert report.quarantined["no_timezone"] == 2
        rows = run_on_conn(name, lambda c: c.execute(text(
            "SELECT (SELECT count(*) FROM feature_points WHERE tz IS NULL), (SELECT count(*) FROM grid_bucket_series)")))
        assert rows == [(2, 0)]


@requires_pg
def test_elevation_fills_and_routes_get_rows_with_inherited_precision():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        run_id = uuid.uuid4()
        run_on_conn(name, lambda c: _points(c, run_id))
        stats = run_on_conn(name, lambda c: fill_elevation(
            c, run_id=run_id, sampler=lambda pts: [Elevation(2400.0, "3dep_13", 10.0)] * len(pts), limit=100))
        assert stats == {"sampled": 2, "null": 0, "retry": 0}
        assert run_on_conn(name, build_route_features) == 4
        rows = run_on_conn(name, lambda c: c.execute(text(
            "SELECT route_id, pitches, round(length_m)::int, elevation_m, coord_precision, area_path::text "
            "FROM route_static_features ORDER BY route_id")))
        assert rows == [
            (900000001, 2, 91, 2400.0, "own", "900000100.900000110.900000111"),
            (900000002, None, None, 2400.0, "inherited", "900000100.900000110.900000112"),
            (900000003, None, None, None, "none", "900000100.900000110.900000113"),
            (900000005, None, None, 2400.0, "own", "900000100.900000110.900000111"),
        ]


@requires_pg
def test_transient_elevation_is_retried_not_recorded():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        run_id = uuid.uuid4()
        run_on_conn(name, lambda c: _points(c, run_id))
        stats = run_on_conn(name, lambda c: fill_elevation(c, run_id=run_id, sampler=lambda pts: [None] * len(pts), limit=100))
        assert stats == {"sampled": 0, "null": 0, "retry": 2}
        assert run_on_conn(name, lambda c: c.execute(text("SELECT count(*) FROM feature_points WHERE dem_source IS NULL"))) == [(2,)]


@requires_pg
def test_nodata_is_recorded_as_null_never_zero():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        run_id = uuid.uuid4()
        run_on_conn(name, lambda c: _points(c, run_id))
        run_on_conn(name, lambda c: fill_elevation(
            c, run_id=run_id, sampler=lambda pts: [Elevation(None, "nodata", None)] * len(pts), limit=100))
        assert run_on_conn(name, lambda c: c.execute(text(
            "SELECT DISTINCT elevation_m, dem_source FROM feature_points"))) == [(None, "nodata")]


@requires_pg
def test_only_scorable_routes_are_scoring_units_and_unlocated_ones_have_no_location():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        run_on_conn(name, lambda c: _points(c, uuid.uuid4()))
        run_on_conn(name, build_route_features)
        units = run_on_conn(name, lambda c: c.execute(text(
            "SELECT unit_id, count(*), min(normals_status) FROM scoring_unit_features GROUP BY 1 ORDER BY 1")))
        assert units == [(900000001, 12, "pending"), (900000002, 12, "pending")]


@requires_pg
def test_a_route_that_becomes_a_boulder_loses_its_feature_row():
    from tests.pgtest import run_sql

    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        run_on_conn(name, lambda c: _points(c, uuid.uuid4()))
        run_on_conn(name, build_route_features)
        run_sql(name, "UPDATE mp_routes SET type = 'Boulder' WHERE mp_route_id = 900000005")
        _catalog(name)
        run_on_conn(name, build_route_features)
        rows = run_on_conn(name, lambda c: c.execute(text("SELECT route_id FROM route_static_features ORDER BY 1")))
        assert rows == [(900000001,), (900000002,), (900000003,)]


@requires_pg
def test_a_transient_point_stays_pending_beside_filled_ones_and_fills_next_run():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _catalog(name)
        run_id = uuid.uuid4()
        run_on_conn(name, lambda c: _points(c, run_id))

        def half(pts):
            return [None if lat > 40.5 else Elevation(2400.0, "3dep_13", 10.0) for lat, _ in pts]

        stats = run_on_conn(name, lambda c: fill_elevation(c, run_id=run_id, sampler=half, limit=100))
        assert stats == {"sampled": 1, "null": 0, "retry": 1}
        rows = fetch(name, "SELECT point_key, elevation_m, dem_source FROM feature_points ORDER BY 1")
        assert rows == [(CRAG_KEY, 2400.0, "3dep_13"), (REGION_KEY, None, None)]
        stats = run_on_conn(name, lambda c: fill_elevation(
            c, run_id=run_id, sampler=lambda pts: [Elevation(2900.0, "3dep_13", 10.0)] * len(pts), limit=100))
        assert stats == {"sampled": 1, "null": 0, "retry": 0}
        assert fetch(name, "SELECT count(*) FROM feature_points WHERE dem_source IS NULL") == [(0,)]


# EPQS check fixtures: three longitude bands, one per slope class, each a north-facing plane.
BANDS = (("flat", -105.0, 2.0), ("moderate", -106.0, 20.0), ("steep", -107.0, 45.0))


def _band(lon):
    return min(BANDS, key=lambda b: abs(b[1] - lon))


def _z(lat, lon):
    return 1000.0 + (lat - 40.0) * 110_574.0 * math.tan(math.radians(_band(lon)[2]))


def _plane(pts):
    return [Elevation(_z(lat, lon), "3dep_13", 10.0) for lat, lon in pts]


def _epqs_seed(n=80):
    values = []
    for _, lon, _ in BANDS:
        for i in range(n):
            lat = round(40.0 + i * 0.001, 5)
            values.append(f"('{point_key(lat, lon)}', {lat}, {lon}, {grid_bucket(lat, lon)}, 'a', 'b', "
                          f"{_z(lat, lon)}, '3dep_13', 10, 'f-v1')")
    return ("INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, elevation_m, dem_source, "
            "dem_res_m, feature_version) VALUES " + ",\n".join(values) + ";")


class FakeEpqs(EpqsClient):
    """Answers 0.5 m above the plane; fails on the first `fail` points (by latitude) of one class."""

    def __init__(self, fail_class=None, fail=0):
        super().__init__(sleep=lambda s: None)
        self.fail_class, self.fail, self.calls = fail_class, fail, []

    def elevation(self, lat, lon):
        self.calls.append((lat, lon))
        if _band(lon)[0] == self.fail_class and round((lat - 40.0) / 0.001) < self.fail:
            self.failed_calls += 1
            return None
        return _z(lat, lon) + 0.5


@requires_pg
def test_epqs_check_fails_closed_when_one_class_loses_25_percent_to_epqs_failures():
    with migrated_db(PIN, seed_sql=_epqs_seed()) as name:
        result = run_on_conn(name, lambda c: epqs_check(c, FakeEpqs("flat", 20), sampler=_plane))
    assert result["epqs_counts"]["flat"] == {"attempted": 80, "failed": 20, "compared": 60}
    assert result["compared"] == 220 and all(c["passed"] == 1 for c in result["classes"].values())
    assert result["passed"] == 0 and len(result["bias_problems"]) == 1


@requires_pg
def test_epqs_check_passes_with_10_percent_epqs_failures():
    with migrated_db(PIN, seed_sql=_epqs_seed()) as name:
        result = run_on_conn(name, lambda c: epqs_check(c, FakeEpqs("flat", 8), sampler=_plane))
    assert result["epqs_counts"]["flat"] == {"attempted": 80, "failed": 8, "compared": 72}
    assert (result["passed"], result["bias_problems"]) == (1, [])


@requires_pg
def test_epqs_sample_is_deterministic_for_a_seed():
    with migrated_db(PIN, seed_sql=_epqs_seed()) as name:
        picks = []
        for seed in (7, 7, 8):
            client = FakeEpqs()
            run_on_conn(name, lambda c, s=seed, cl=client: epqs_check(c, cl, sampler=_plane, seed=s, per_class=10))
            picks.append(client.calls)
    assert len(picks[0]) == 30 and picks[0] == picks[1] and picks[0] != picks[2]


@requires_pg
def test_epqs_run_step_logs_per_class_counts_and_a_rejected_gate():
    with migrated_db(PIN, seed_sql=_epqs_seed()) as name:
        result = run_on_engine(name, lambda e: run_step(e, "epqs-check", epqs_client=FakeEpqs("steep", 20),
                                                        sampler=_plane))
        [(status, problems)] = fetch(name, "SELECT status, validation_report->>'problems' FROM source_ingest_log "
                                           "WHERE source = 'static_features:epqs-check'")
    assert result["passed"] == 0 and status == "rejected"
    assert "epqs class steep: attempted 80, failed 20, compared 60" in problems
    assert "in class steep exceeds 0.20" in problems
