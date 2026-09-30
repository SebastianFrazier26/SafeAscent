"""The 0.1° grid key (P3: "lat/lon rounded to 0.1°"), packed into one integer.

floor(x*10 + 0.5) rather than round(): Python rounds half to even and Postgres's
double-precision round() is platform-dependent. SQL uses the immutable function
grid_bucket_key() from migration 0004, always on float8 arguments: a `numeric` value with
more digits than a double holds (40.04999999999999999) buckets as 400 in numeric math but
as 401 once read into Python as a float. Casting first makes both sides the same IEEE math.
"""

from __future__ import annotations

import math

LON_OFFSET = 5000
LAT_STRIDE = 10000


def bucket_index(x: float) -> int:
    return math.floor(x * 10 + 0.5)


def grid_bucket(lat: float, lon: float) -> int:
    if not (0.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"grid_bucket covers northern-hemisphere US points only, got {lat}, {lon}")
    return bucket_index(lat) * LAT_STRIDE + bucket_index(lon) + LON_OFFSET


def bucket_center(bucket: int) -> tuple[float, float]:
    lat_i, lon_part = divmod(bucket, LAT_STRIDE)
    return lat_i / 10, (lon_part - LON_OFFSET) / 10


def grid_bucket_sql(lat_expr: str, lon_expr: str) -> str:
    return f"grid_bucket_key(({lat_expr})::float8, ({lon_expr})::float8)"
