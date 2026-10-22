"""Exposure index (migration 0011)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import REAL, BigInteger, CheckConstraint, Date, ForeignKey, Index, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ExposureIndex(Base):
    __tablename__ = "exposure_index"
    __table_args__ = (
        CheckConstraint("type_group IN ('sport', 'trad', 'rock', 'alpine', 'ice', 'mixed')",
                        name="exposure_index_type_group_check"),
        CheckConstraint("month BETWEEN 1 AND 12", name="exposure_index_month_check"),
        CheckConstraint("n_routes > 0", name="exposure_index_n_routes_check"),
        CheckConstraint("jsonb_typeof(proxy) = 'object'", name="exposure_index_proxy_check"),
        CheckConstraint("data_cutoff <= as_of", name="exposure_index_cutoff_check"),
        CheckConstraint("tick_coverage IS NULL OR tick_coverage BETWEEN 0 AND 1",
                        name="exposure_index_tick_coverage_check"),
        CheckConstraint(
            "(exposure_point IS NULL) = (exposure_lo IS NULL) AND (exposure_lo IS NULL) = (exposure_hi IS NULL)",
            name="exposure_index_bounds_check"),
        Index("ix_exposure_index_snapshot", "as_of", "data_cutoff"),
        {"schema": "internal"},
    )

    area_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"),
                                         primary_key=True, autoincrement=False)
    type_group: Mapped[str] = mapped_column(Text, primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    data_cutoff: Mapped[date] = mapped_column(Date, primary_key=True)
    n_routes: Mapped[int] = mapped_column(Integer)
    proxy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    proxy_version: Mapped[str] = mapped_column(Text)
    tick_coverage: Mapped[float | None] = mapped_column(REAL)
    exposure_point: Mapped[float | None] = mapped_column(REAL)
    exposure_lo: Mapped[float | None] = mapped_column(REAL)
    exposure_hi: Mapped[float | None] = mapped_column(REAL)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
