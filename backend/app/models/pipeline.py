"""Phase 2 pipeline bookkeeping tables (migration 0004)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SourceIngestLog(Base):
    __tablename__ = "source_ingest_log"
    __table_args__ = (Index("ix_source_ingest_log_source_finished", "source", "finished_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    rows_in: Mapped[int | None] = mapped_column(Integer)
    rows_upserted: Mapped[int | None] = mapped_column(Integer)
    rows_quarantined: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_units: Mapped[Decimal | None] = mapped_column(Numeric)


class AccidentRevision(Base):
    __tablename__ = "accident_revisions"
    __table_args__ = (
        UniqueConstraint("accident_id", "field", "rule_version", name="accident_revisions_key"),
        {"schema": "internal"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    accident_id: Mapped[int] = mapped_column(Integer, ForeignKey("accidents.accident_id"))
    field: Mapped[str] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestQuarantine(Base):
    __tablename__ = "ingest_quarantine"
    __table_args__ = (Index("ix_ingest_quarantine_run", "run_id"), {"schema": "internal"})

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_ingest_log.run_id"))
    source: Mapped[str] = mapped_column(Text)
    row_ref: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MpTickAggregate(Base):
    __tablename__ = "mp_tick_aggregates"
    __table_args__ = {"schema": "internal"}

    mp_route_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period: Mapped[str] = mapped_column(Text, primary_key=True)
    style: Mapped[str] = mapped_column(Text, primary_key=True)
    tick_count: Mapped[int] = mapped_column(Integer)
    scrape_run_id: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loaded_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
