"""Daily conditions per (0.1° cell, timezone, local day), the series registry and the
per-bucket normals (migration 0007)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import REAL, Boolean, CheckConstraint, Date, DateTime, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GridBucketSeries(Base):
    __tablename__ = "grid_bucket_series"
    __table_args__ = (
        CheckConstraint("(first_era5_date IS NULL) = (last_era5_date IS NULL)", name="grid_bucket_series_span_pair_check"),
        CheckConstraint(
            "first_era5_date IS NULL OR first_era5_date <= last_era5_date", name="grid_bucket_series_span_order_check"
        ),
    )

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    tz: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_era5_date: Mapped[dt.date | None] = mapped_column(Date)
    last_era5_date: Mapped[dt.date | None] = mapped_column(Date)


class CellDailyConditions(Base):
    __tablename__ = "cell_daily_conditions"
    __table_args__ = (
        CheckConstraint("record_kind IN ('era5', 'stopgap', 'forecast')", name="cell_daily_conditions_record_kind_check"),
        CheckConstraint("is_forecast = (record_kind = 'forecast')", name="cell_daily_conditions_is_forecast_check"),
    )

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    tz: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    tmax: Mapped[float | None] = mapped_column(REAL)
    tmin: Mapped[float | None] = mapped_column(REAL)
    precip_mm: Mapped[float | None] = mapped_column(REAL)
    snowfall_cm: Mapped[float | None] = mapped_column(REAL)
    snow_depth_cm: Mapped[float | None] = mapped_column(REAL)
    wind_max_ms: Mapped[float | None] = mapped_column(REAL)
    gust_max_ms: Mapped[float | None] = mapped_column(REAL)
    freeze_thaw: Mapped[bool | None] = mapped_column(Boolean)
    swe_delta_mm: Mapped[float | None] = mapped_column(REAL)
    nws_alert_codes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    aqi: Mapped[int | None] = mapped_column(SmallInteger)
    record_kind: Mapped[str] = mapped_column(Text)
    is_forecast: Mapped[bool] = mapped_column(Boolean, server_default="false")
    model: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CellNormalsStatus(Base):
    __tablename__ = "cell_normals_status"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'complete', 'insufficient')", name="cell_normals_status_status_check"),
    )

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    computed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    n_years: Mapped[int | None] = mapped_column(SmallInteger)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CellClimateNormals(Base):
    __tablename__ = "cell_climate_normals"
    __table_args__ = (CheckConstraint("month BETWEEN 1 AND 12", name="cell_climate_normals_month_check"),)

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    tmax_mean: Mapped[float | None] = mapped_column(REAL)
    tmin_mean: Mapped[float | None] = mapped_column(REAL)
    precip_mm: Mapped[float | None] = mapped_column(REAL)
    snowfall_cm: Mapped[float | None] = mapped_column(REAL)
    freeze_thaw_days: Mapped[float | None] = mapped_column(REAL)
    n_years: Mapped[int] = mapped_column(SmallInteger)
    period_start_year: Mapped[int] = mapped_column(SmallInteger)
    period_end_year: Mapped[int] = mapped_column(SmallInteger)
    ref_elevation_m: Mapped[float | None] = mapped_column(REAL)
    source: Mapped[str] = mapped_column(Text)
    normals_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CellClimatology(Base):
    __tablename__ = "cell_climatology"
    __table_args__ = (CheckConstraint("doy BETWEEN 1 AND 366", name="cell_climatology_doy_check"),)

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    doy: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    tmax_mean: Mapped[float | None] = mapped_column(REAL)
    tmax_p10: Mapped[float | None] = mapped_column(REAL)
    tmax_p90: Mapped[float | None] = mapped_column(REAL)
    tmin_mean: Mapped[float | None] = mapped_column(REAL)
    tmin_p10: Mapped[float | None] = mapped_column(REAL)
    tmin_p90: Mapped[float | None] = mapped_column(REAL)
    precip_mean: Mapped[float | None] = mapped_column(REAL)
    precip_p90: Mapped[float | None] = mapped_column(REAL)
    snowfall_mean: Mapped[float | None] = mapped_column(REAL)
    gust_p90: Mapped[float | None] = mapped_column(REAL)
    freeze_thaw_freq: Mapped[float | None] = mapped_column(REAL)
    lightning_day_freq: Mapped[float | None] = mapped_column(REAL)
    n_years: Mapped[int] = mapped_column(SmallInteger)
    period_start_year: Mapped[int] = mapped_column(SmallInteger)
    period_end_year: Mapped[int] = mapped_column(SmallInteger)
    climatology_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
