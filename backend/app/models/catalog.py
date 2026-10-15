"""MP route catalog (migration 0008): derived columns keyed by the MP ids."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.types import Ltree


class CanonicalArea(Base):
    __tablename__ = "canonical_areas"
    __table_args__ = (
        CheckConstraint("tree_status IN ('us', 'foreign_root', 'cycle', 'orphan_parent')",
                        name="canonical_areas_tree_status_check"),
        CheckConstraint("(path IS NOT NULL) = (tree_status IN ('us', 'foreign_root'))", name="canonical_areas_path_check"),
        CheckConstraint("(state_code IS NOT NULL) = (tree_status = 'us')", name="canonical_areas_state_check"),
        CheckConstraint("state_code IS NULL OR state_code ~ '^[A-Z]{2}$'", name="canonical_areas_state_format_check"),
        CheckConstraint("coord_precision IN ('own', 'inherited', 'none')", name="canonical_areas_coord_precision_check"),
        CheckConstraint("(lat IS NULL) = (lon IS NULL)", name="canonical_areas_pair_check"),
        CheckConstraint("(coord_precision = 'none') = (lat IS NULL)", name="canonical_areas_coords_check"),
        CheckConstraint("(coord_precision = 'none') = (coord_source_id IS NULL)", name="canonical_areas_source_check"),
        CheckConstraint("(point_key IS NULL) = (lat IS NULL)", name="canonical_areas_point_key_check"),
        Index("ix_canonical_areas_path", "path", postgresql_using="gist"),
        Index("ix_canonical_areas_point_key", "point_key"),
    )

    area_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mp_locations.mp_id", ondelete="RESTRICT"),
                                         primary_key=True, autoincrement=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    path: Mapped[str | None] = mapped_column(Ltree())
    depth: Mapped[int | None] = mapped_column(SmallInteger)
    tree_status: Mapped[str] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    coord_precision: Mapped[str] = mapped_column(Text)
    coord_source_id: Mapped[int | None] = mapped_column(BigInteger)
    point_key: Mapped[str | None] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CanonicalRoute(Base):
    __tablename__ = "canonical_routes"
    __table_args__ = (
        CheckConstraint(
            "type_group IS NULL OR type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed', 'rock', 'unknown')",
            name="canonical_routes_type_group_check"),
        CheckConstraint("unscorable_reason IS NULL OR unscorable_reason IN "
                        "('boulder', 'no_area', 'not_us', 'unknown_type', 'no_coordinates')",
                        name="canonical_routes_reason_check"),
        CheckConstraint("scorable = (unscorable_reason IS NULL)", name="canonical_routes_scorable_check"),
        CheckConstraint("(is_boulder AND type_group IS NULL AND NOT scorable) OR (NOT is_boulder AND type_group IS NOT NULL)",
                        name="canonical_routes_boulder_check"),
        Index("ix_canonical_routes_area", "area_id"),
    )

    route_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mp_routes.mp_route_id", ondelete="RESTRICT"),
                                          primary_key=True, autoincrement=False)
    area_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"))
    type_group: Mapped[str | None] = mapped_column(Text)
    is_boulder: Mapped[bool] = mapped_column(Boolean)
    disciplines: Mapped[list[str]] = mapped_column(ARRAY(Text))
    type_rule_version: Mapped[str] = mapped_column(Text)
    scorable: Mapped[bool] = mapped_column(Boolean)
    unscorable_reason: Mapped[str | None] = mapped_column(Text)
    facts_sha256: Mapped[str] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RouteTickScrape(Base):
    """The tick-source seam: one row per route a tick loader has read (DP9)."""

    __tablename__ = "route_tick_scrapes"
    __table_args__ = (
        CheckConstraint("source IN ('mp_scrape')", name="route_tick_scrapes_source_check"),
        CheckConstraint("complete = (complete_through IS NOT NULL)", name="route_tick_scrapes_complete_check"),
        CheckConstraint("complete OR total_ticks IS NULL", name="route_tick_scrapes_total_check"),
        CheckConstraint("total_ticks IS NULL OR total_ticks >= 0", name="route_tick_scrapes_count_check"),
        {"schema": "internal"},
    )

    mp_route_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mp_routes.mp_route_id", ondelete="RESTRICT"),
                                             primary_key=True, autoincrement=False)
    source: Mapped[str] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    complete: Mapped[bool] = mapped_column(Boolean)
    complete_through: Mapped[date | None] = mapped_column(Date)
    total_ticks: Mapped[int | None] = mapped_column(Integer)
    loaded_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
