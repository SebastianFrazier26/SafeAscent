"""R10 links on the MP catalog (migration 0010): legacy links, the unresolved residue, and the
LLM pass's per-call audit and per-(model, prompt version) gate."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AccidentLegacyLink(Base):
    __tablename__ = "accident_legacy_links"
    __table_args__ = (
        CheckConstraint("method IN ('legacy_mp_id', 'owner', 'llm')", name="accident_legacy_links_method_check"),
        CheckConstraint("num_nonnulls(route_id, area_id) = 1", name="accident_legacy_links_target_check"),
        CheckConstraint("(method = 'llm') = (llm_call_id IS NOT NULL)", name="accident_legacy_links_llm_check"),
    )

    accident_id: Mapped[int] = mapped_column(Integer, ForeignKey("accidents.accident_id"), primary_key=True,
                                             autoincrement=False)
    route_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("canonical_routes.route_id"))
    area_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("canonical_areas.area_id"))
    method: Mapped[str] = mapped_column(Text)
    r10_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class R10Unresolved(Base):
    __tablename__ = "r10_unresolved"
    __table_args__ = (
        CheckConstraint("reason IN ('legacy_route_no_mp_id', 'legacy_mp_id_not_in_catalog', 'legacy_mountain_only')",
                        name="r10_unresolved_reason_check"),
        CheckConstraint("owner_decision IS NULL OR owner_decision IN ('link', 'no_link')",
                        name="r10_unresolved_owner_decision_check"),
        CheckConstraint("owner_decision IS DISTINCT FROM 'link' OR num_nonnulls(decided_route_id, decided_area_id) = 1",
                        name="r10_unresolved_link_target_check"),
        {"schema": "internal"},
    )

    accident_id: Mapped[int] = mapped_column(Integer, ForeignKey("accidents.accident_id"), primary_key=True,
                                             autoincrement=False)
    legacy_route_id: Mapped[int | None] = mapped_column(Integer)
    legacy_mountain_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    candidate_route_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    candidate_area_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    owner_decision: Mapped[str | None] = mapped_column(Text)
    decided_route_id: Mapped[int | None] = mapped_column(BigInteger)
    decided_area_id: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class R10LlmCall(Base):
    __tablename__ = "r10_llm_calls"
    __table_args__ = (
        CheckConstraint("candidate_kind IN ('route', 'area')", name="r10_llm_calls_kind_check"),
        CheckConstraint("status IN ('ok', 'invalid', 'refusal', 'max_tokens', 'error')",
                        name="r10_llm_calls_status_check"),
        CheckConstraint("pick_id IS NULL OR pick_id = ANY(candidate_ids)", name="r10_llm_calls_pick_offered_check"),
        CheckConstraint("status <> 'ok' OR (confidence BETWEEN 0 AND 1 AND (pick_none OR pick_id IS NOT NULL))",
                        name="r10_llm_calls_ok_check"),
        CheckConstraint("status = 'ok' OR (pick_id IS NULL AND pick_none IS NULL AND confidence IS NULL)",
                        name="r10_llm_calls_failed_check"),
        Index("ix_r10_llm_calls_accident", "accident_id"),
        {"schema": "internal"},
    )

    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    accident_id: Mapped[int] = mapped_column(Integer, ForeignKey("accidents.accident_id"))
    model: Mapped[str] = mapped_column(Text)
    model_reported: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    payload_fields: Mapped[list[str]] = mapped_column(ARRAY(Text))
    request_sha256: Mapped[str] = mapped_column(Text)
    candidate_kind: Mapped[str] = mapped_column(Text)
    candidate_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    status: Mapped[str] = mapped_column(Text)
    pick_id: Mapped[int | None] = mapped_column(BigInteger)
    pick_none: Mapped[bool | None] = mapped_column(Boolean)
    confidence: Mapped[float | None] = mapped_column(REAL)
    reason_code: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    is_sample: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class R10LlmGate(Base):
    __tablename__ = "r10_llm_gates"
    __table_args__ = ({"schema": "internal"},)

    gate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    sample_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    n_checked: Mapped[int] = mapped_column(Integer)
    n_agree: Mapped[int] = mapped_column(Integer)
    n_auto_band: Mapped[int] = mapped_column(Integer)
    n_auto_band_agree: Mapped[int] = mapped_column(Integer)
    auto_threshold: Mapped[float] = mapped_column(REAL)
    passed: Mapped[bool] = mapped_column(Boolean)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
