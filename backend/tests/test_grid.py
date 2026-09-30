from decimal import Decimal

import pytest

from app.pipelines.grid import bucket_center, bucket_index, grid_bucket, grid_bucket_sql


def test_bucket_index_rounds_half_up_not_to_even():
    assert bucket_index(40.25) == 403
    assert bucket_index(-105.25) == -1052
    assert bucket_index(-105.26) == -1053


def test_grid_bucket_round_trips_to_the_cell_center():
    bucket = grid_bucket(40.01, -105.27)
    assert bucket == 400 * 10000 + (-1053) + 5000
    assert bucket_center(bucket) == (40.0, -105.3)


def test_antimeridian_and_alaska_buckets_decode():
    assert bucket_center(grid_bucket(52.9, 175.0)) == (52.9, 175.0)
    assert bucket_center(grid_bucket(71.3, -156.8)) == (71.3, -156.8)


def test_southern_latitudes_are_rejected():
    with pytest.raises(ValueError):
        grid_bucket(-33.4, -70.6)


def test_sql_expression_calls_the_one_function_with_float8_casts():
    assert grid_bucket_sql("l.latitude", "l.longitude") == (
        "grid_bucket_key((l.latitude)::float8, (l.longitude)::float8)"
    )


@pytest.mark.parametrize(
    "lat,lon,bucket",
    [(40.05, -105.25, 4013948), (40.05, -105.35, 4013947), (64.15, -149.95, 6423501), (19.85, -155.45, 1993446)],
)
def test_half_steps_round_up_at_negative_longitudes(lat, lon, bucket):
    assert grid_bucket(lat, lon) == bucket


def test_a_numeric_just_below_a_half_step_buckets_as_its_double():
    # asyncpg hands Python the float8 of a numeric; the SQL function sees the same double
    # only because grid_bucket_sql casts. test_migration_0004 checks the DB side.
    assert grid_bucket(float(Decimal("40.04999999999999999")), -105.3) == 4013947
