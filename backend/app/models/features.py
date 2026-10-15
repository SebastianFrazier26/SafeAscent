"""Static features per point and per MP route (migration 0009)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import REAL, BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.types import Ltree

_SLOPE_COMMENT = "maximum Horn slope (deg) within 200 m of the point (crag steepness), not the slope at the point"


class FeaturePoint(Base):
    __tablename__ = "feature_points"
    __table_args__ = (
        CheckConstraint("dem_source IS NULL OR dem_source IN ('3dep_13', '3dep_2', 'nodata', 'no_tile')",
                        name="feature_points_dem_source_check"),
        CheckConstraint("lithology_source IS NULL OR lithology_source IN ('macrostrat', 'none')",
                        name="feature_points_lithology_source_check"),
        Index("ix_feature_points_grid_bucket", "grid_bucket"),
    )

    point_key: Mapped[str] = mapped_column(Text, primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    grid_bucket: Mapped[int] = mapped_column(Integer)
    h3_r5: Mapped[str] = mapped_column(Text)
    h3_r7: Mapped[str] = mapped_column(Text)
    tz: Mapped[str | None] = mapped_column(Text)
    elevation_m: Mapped[float | None] = mapped_column(REAL)
    dem_source: Mapped[str | None] = mapped_column(Text)
    dem_res_m: Mapped[float | None] = mapped_column(REAL)
    slope_deg: Mapped[float | None] = mapped_column(REAL, comment=_SLOPE_COMMENT)
    aspect_deg: Mapped[float | None] = mapped_column(REAL)
    aspect_confidence: Mapped[float | None] = mapped_column(REAL)
    terrain_version: Mapped[str | None] = mapped_column(Text)
    lithology: Mapped[str | None] = mapped_column(Text)
    lithology_source: Mapped[str | None] = mapped_column(Text)
    feature_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RouteStaticFeatures(Base):
    __tablename__ = "route_static_features"
    __table_args__ = (
        CheckConstraint("source = 'mp'", name="route_static_features_source_check"),
        CheckConstraint("type_group IS NULL OR type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed', 'rock', 'unknown')",
                        name="route_static_features_type_group_check"),
        CheckConstraint("coord_precision IN ('own', 'inherited', 'none')",
                        name="route_static_features_coord_precision_check"),
    )

    route_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("canonical_routes.route_id"), primary_key=True,
                                          autoincrement=False)
    source: Mapped[str] = mapped_column(Text)
    area_path: Mapped[str | None] = mapped_column(Ltree())
    type_group: Mapped[str | None] = mapped_column(Text)
    point_key: Mapped[str | None] = mapped_column(Text, ForeignKey("feature_points.point_key"))
    lat: Mapped[float | None] = mapped_column(Float)
    aspect_deg: Mapped[float | None] = mapped_column(REAL)
    slope_deg: Mapped[float | None] = mapped_column(REAL, comment=_SLOPE_COMMENT)
    elevation_m: Mapped[float | None] = mapped_column(REAL)
    lithology: Mapped[str | None] = mapped_column(Text)
    pitches: Mapped[int | None] = mapped_column(SmallInteger)
    length_m: Mapped[float | None] = mapped_column(REAL)
    h3_r5: Mapped[str | None] = mapped_column(Text)
    grid_bucket: Mapped[int | None] = mapped_column(Integer)
    dem_res_m: Mapped[float | None] = mapped_column(REAL)
    aspect_confidence: Mapped[float | None] = mapped_column(REAL)
    coord_precision: Mapped[str] = mapped_column(Text)
    lithology_source: Mapped[str | None] = mapped_column(Text)
    feature_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
