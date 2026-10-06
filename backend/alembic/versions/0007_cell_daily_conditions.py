"""cell_daily_conditions per (0.1° cell, IANA timezone, local day), the series registry,
normals tables, accidents.tz, and accident_conditions.

Every Phase 2 weather-row column is created now (AQI, alerts, SWE), so later plans never
ALTER a table of tens of millions of rows. Lightning lives in plan 7's own tables. accident_conditions joins on
(cell, crag timezone, local date) — never on accident_id — through the grid_bucket_key
function from 0004, the single SQL definition of the grid key.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_cell_daily_conditions"
down_revision = "0006_accidents_clean"
branch_labels = None
depends_on = None


# Task 6 (OQ6 ruling) writes 'derived_cell_daily_conditions' for normals computed from rows already
# in cell_daily_conditions, and 'open_meteo_era5_seamless' for normals bought from the archive.
CONDITIONS_SOURCES = ("open_meteo_archive", "open_meteo_forecast")
NORMALS_SOURCES = ("open_meteo_era5_seamless", "derived_cell_daily_conditions")
PAID_TABLES = ("cell_daily_conditions", "cell_climate_normals", "cell_climatology")
# Owner OQ2 (rule 4): incidents from 1990. Must equal app.pipelines.localday.INCIDENT_START
# (test_localday pins it); a migration never imports app code. Incident series start six days
# earlier, so without this floor a late-1989 incident would get a partial window, not missing.
INCIDENT_START_SQL = "1990-01-01"


def upgrade() -> None:
    # As 0005/0006 (G12): in one alembic run this repeats an earlier revision's 5s; set here so a
    # standalone 0007 gets the same fail-fast bound on its ALTER of accidents.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("accidents", sa.Column("tz", sa.Text(), nullable=True))
    # accidents holds a few thousand rows, so validating this CHECK's scan under the 5s bound is cheap.
    op.create_check_constraint("accidents_tz_check", "accidents", "tz <> ''")
    op.create_table(
        "grid_bucket_series",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("first_era5_date", sa.Date(), nullable=True),
        sa.Column("last_era5_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "tz"),
        sa.CheckConstraint("tz <> ''", name="grid_bucket_series_tz_check"),
        sa.CheckConstraint("(first_era5_date IS NULL) = (last_era5_date IS NULL)", name="grid_bucket_series_span_pair_check"),
        sa.CheckConstraint("first_era5_date IS NULL OR first_era5_date <= last_era5_date", name="grid_bucket_series_span_order_check"),
    )
    op.create_table(
        "cell_daily_conditions",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("tmax", sa.REAL(), nullable=True),
        sa.Column("tmin", sa.REAL(), nullable=True),
        sa.Column("precip_mm", sa.REAL(), nullable=True),
        sa.Column("snowfall_cm", sa.REAL(), nullable=True),
        sa.Column("snow_depth_cm", sa.REAL(), nullable=True),
        sa.Column("wind_max_ms", sa.REAL(), nullable=True),
        sa.Column("gust_max_ms", sa.REAL(), nullable=True),
        sa.Column("freeze_thaw", sa.Boolean(), nullable=True),
        sa.Column("swe_delta_mm", sa.REAL(), nullable=True),
        sa.Column("nws_alert_codes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("aqi", sa.SmallInteger(), nullable=True),
        sa.Column("record_kind", sa.Text(), nullable=False),
        sa.Column("is_forecast", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "tz", "date"),
        sa.CheckConstraint("record_kind IN ('era5', 'stopgap', 'forecast')", name="cell_daily_conditions_record_kind_check"),
        sa.CheckConstraint("is_forecast = (record_kind = 'forecast')", name="cell_daily_conditions_is_forecast_check"),
        sa.CheckConstraint(
            f"source IN ({', '.join(repr(v) for v in CONDITIONS_SOURCES)})", name="cell_daily_conditions_source_check"
        ),
        sa.CheckConstraint("tz <> ''", name="cell_daily_conditions_tz_check"),
    )
    op.create_table(
        "cell_normals_status",
        sa.Column("grid_bucket", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n_years", sa.SmallInteger(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'complete', 'insufficient')", name="cell_normals_status_status_check"),
    )
    op.create_table(
        "cell_climate_normals",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("tmax_mean", sa.REAL(), nullable=True),
        sa.Column("tmin_mean", sa.REAL(), nullable=True),
        sa.Column("precip_mm", sa.REAL(), nullable=True),
        sa.Column("snowfall_cm", sa.REAL(), nullable=True),
        sa.Column("freeze_thaw_days", sa.REAL(), nullable=True),
        sa.Column("n_years", sa.SmallInteger(), nullable=False),
        sa.Column("period_start_year", sa.SmallInteger(), nullable=False),
        sa.Column("period_end_year", sa.SmallInteger(), nullable=False),
        sa.Column("ref_elevation_m", sa.REAL(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("normals_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "month"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="cell_climate_normals_month_check"),
        sa.CheckConstraint(
            f"source IN ({', '.join(repr(v) for v in NORMALS_SOURCES)})", name="cell_climate_normals_source_check"
        ),
    )
    clim_cols = [
        sa.Column(name, sa.REAL(), nullable=True)
        for name in ("tmax_mean", "tmax_p10", "tmax_p90", "tmin_mean", "tmin_p10", "tmin_p90", "precip_mean",
                     "precip_p90", "snowfall_mean", "gust_p90", "freeze_thaw_freq", "lightning_day_freq")
    ]
    op.create_table(
        "cell_climatology",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("doy", sa.SmallInteger(), nullable=False),
        *clim_cols,
        sa.Column("n_years", sa.SmallInteger(), nullable=False),
        sa.Column("period_start_year", sa.SmallInteger(), nullable=False),
        sa.Column("period_end_year", sa.SmallInteger(), nullable=False),
        sa.Column("climatology_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "doy"),
        sa.CheckConstraint("doy BETWEEN 1 AND 366", name="cell_climatology_doy_check"),
    )
    # tz comes from accidents itself: changing 0006's views would change the Phase 3 column
    # interface test_migration_0006 pins. Only trusted points (point_trusted) get a point-level
    # join; region_fallback/unknown points keep coordinates for pooling only. A missing day is an
    # absent row, never a zero. D13: a year range (year_lo < year_hi, from any rule) is not a known day.
    op.execute(
        "CREATE VIEW accident_conditions AS "
        "SELECT a.accident_id, o.day_offset, c.grid_bucket, c.tz, c.date, c.tmax, c.tmin, c.precip_mm, c.snowfall_cm, "
        "c.wind_max_ms, c.gust_max_ms, c.freeze_thaw, c.record_kind, c.is_forecast "
        "FROM accidents_clean_daily a JOIN accidents t ON t.accident_id = a.accident_id "
        "CROSS JOIN generate_series(-6, 0) AS o(day_offset) "
        "JOIN cell_daily_conditions c ON c.grid_bucket = grid_bucket_key(a.latitude, a.longitude) "
        "AND c.tz = t.tz AND c.date = a.date + o.day_offset "
        "WHERE a.point_trusted AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL "
        "AND a.year_lo IS NOT DISTINCT FROM a.year_hi "
        f"AND a.date >= DATE '{INCIDENT_START_SQL}'"
    )


def downgrade() -> None:
    # Same fail-fast bound as upgrade: dropping accidents.tz takes an exclusive lock on accidents.
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()
    for table in PAID_TABLES:
        rows = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if rows:
            raise RuntimeError(f"refusing to downgrade 0007: {table} has {rows} rows (paid ERA5 data)")
    op.execute("DROP VIEW accident_conditions")
    op.drop_table("cell_climatology")
    op.drop_table("cell_climate_normals")
    op.drop_table("cell_normals_status")
    op.drop_table("cell_daily_conditions")
    op.drop_table("grid_bucket_series")
    op.drop_column("accidents", "tz")
