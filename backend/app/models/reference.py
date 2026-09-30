"""Reference and review tables for the Phase 2a repairs (migration 0005)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, Float, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GnisSummit(Base):
    __tablename__ = "gnis_summits"
    __table_args__ = (Index("ix_gnis_summits_name_key", "name_key"),)

    gnis_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text)
    state_code: Mapped[str] = mapped_column(CHAR(2))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    loaded_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class DuplicateDecision(Base):
    __tablename__ = "duplicate_decisions"
    __table_args__ = {"schema": "internal"}

    low_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    high_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    score: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
