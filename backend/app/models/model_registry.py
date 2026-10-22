"""Phase 3 model registry and stored route scores (migration 0012, schema model)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

_STATUSES = "('candidate', 'active', 'previous', 'rejected', 'retired')"
_TYPE_GROUPS = "('sport', 'trad', 'rock', 'alpine', 'ice', 'mixed')"
# NaN sorts above every number in Postgres, so "< 'Infinity'" refuses NaN as well as +inf.
_FINITE = " AND ".join(
    f"({c} IS NULL OR ({c} > '-Infinity'::float8 AND {c} < 'Infinity'::float8))"
    for c in ("rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05", "pct_q95", "per10k_mean", "per10k_q05",
              "per10k_q95"))
_N_EFF = ("(n_eff_geo IS NULL OR (n_eff_geo >= 0 AND n_eff_geo < 'Infinity'::float4)) AND "
          "(n_eff_feature IS NULL OR (n_eff_feature >= 0 AND n_eff_feature < 'Infinity'::float4))")


class ModelRegistry(Base):
    __tablename__ = "registry"
    __table_args__ = (
        CheckConstraint("family IN ('kernel', 'eb_v1')", name="registry_family_check"),
        CheckConstraint(f"status IN {_STATUSES}", name="registry_status_check"),
        CheckConstraint("promoted_by IS NULL OR promoted_by IN ('auto', 'owner')", name="registry_promoted_by_check"),
        CheckConstraint("num_nulls(bundle, bundle_sha256, bundle_bytes) IN (0, 3)", name="registry_bundle_check"),
        CheckConstraint(
            "family = 'kernel' OR status NOT IN ('active', 'previous') "
            "OR (bundle IS NOT NULL AND scores_rows IS NOT NULL AND scores_rows > 0)",
            name="registry_servable_check"),
        CheckConstraint(
            "family = 'kernel' OR (status IN ('active', 'previous', 'retired')) = (promoted_at IS NOT NULL)",
            name="registry_promoted_at_check"),
        CheckConstraint("scores_rows IS NULL OR scores_rows >= 0", name="registry_scores_rows_check"),
        CheckConstraint("bundle_bytes IS NULL OR bundle_bytes >= 0", name="registry_bundle_bytes_check"),
        Index("ux_registry_one_active", text("(true)"), unique=True, postgresql_where=text("status = 'active'")),
        Index("ux_registry_one_previous", text("(true)"), unique=True, postgresql_where=text("status = 'previous'")),
        {"schema": "model"},
    )

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data_hash: Mapped[str] = mapped_column(Text)
    train_start: Mapped[date] = mapped_column(Date)
    data_cutoff: Mapped[date] = mapped_column(Date)
    exposure_as_of: Mapped[date | None] = mapped_column(Date)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    thresholds: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model_card: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    bundle: Mapped[bytes | None] = mapped_column(LargeBinary)
    bundle_sha256: Mapped[str | None] = mapped_column(Text)
    bundle_bytes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promoted_by: Mapped[str | None] = mapped_column(Text)
    parent_version: Mapped[str | None] = mapped_column(Text, ForeignKey("model.registry.version"))
    gate_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    diff_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scores_rows: Mapped[int | None] = mapped_column(Integer)


class RouteScore(Base):
    __tablename__ = "route_scores"
    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="route_scores_month_check"),
        CheckConstraint(f"type_group IN {_TYPE_GROUPS}", name="route_scores_type_group_check"),
        CheckConstraint("insufficient_reason IS NULL OR insufficient_reason IN ('no_evidence')",
                        name="route_scores_reason_check"),
        CheckConstraint("conf_level IN ('high', 'medium', 'low', 'insufficient')", name="route_scores_conf_check"),
        CheckConstraint("insufficient = (conf_level = 'insufficient')", name="route_scores_conf_flag_check"),
        CheckConstraint("insufficient = (insufficient_reason IS NOT NULL)", name="route_scores_reason_flag_check"),
        CheckConstraint(
            "CASE WHEN insufficient THEN num_nonnulls(rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95) = 0 "
            "ELSE num_nulls(rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95) = 0 END",
            name="route_scores_numbers_check"),
        CheckConstraint("rr_q05 IS NULL OR (0 < rr_q05 AND rr_q05 <= rr_mean AND rr_mean <= rr_q95)",
                        name="route_scores_rr_order_check"),
        CheckConstraint(
            "percentile IS NULL OR (0 <= pct_q05 AND pct_q05 <= percentile AND percentile <= pct_q95 AND pct_q95 <= 100)",
            name="route_scores_pct_order_check"),
        CheckConstraint("num_nulls(per10k_mean, per10k_q05, per10k_q95) IN (0, 3)", name="route_scores_per10k_check"),
        CheckConstraint("(per10k_mean IS NULL) = (per10k_reason IS NOT NULL OR insufficient)",
                        name="route_scores_per10k_reason_check"),
        CheckConstraint(_FINITE, name="route_scores_finite_check"),
        CheckConstraint(_N_EFF, name="route_scores_n_eff_check"),
        {"schema": "model"},
    )

    model_version: Mapped[str] = mapped_column(Text, ForeignKey("model.registry.version", ondelete="RESTRICT"),
                                               primary_key=True)
    route_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("canonical_routes.route_id", ondelete="RESTRICT"),
                                          primary_key=True, autoincrement=False)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    type_group: Mapped[str] = mapped_column(Text)
    area_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"))
    insufficient: Mapped[bool] = mapped_column(Boolean)
    insufficient_reason: Mapped[str | None] = mapped_column(Text)
    # Double, not REAL (pre-flight F31): float32 underflows rr_q05 to 0 at the shape floor.
    rr_mean: Mapped[float | None] = mapped_column(Double)
    rr_q05: Mapped[float | None] = mapped_column(Double)
    rr_q95: Mapped[float | None] = mapped_column(Double)
    percentile: Mapped[float | None] = mapped_column(Double)
    pct_q05: Mapped[float | None] = mapped_column(Double)
    pct_q95: Mapped[float | None] = mapped_column(Double)
    conf_level: Mapped[str] = mapped_column(Text)
    n_eff_geo: Mapped[float | None] = mapped_column(REAL)
    n_eff_feature: Mapped[float | None] = mapped_column(REAL)
    per10k_mean: Mapped[float | None] = mapped_column(Double)
    per10k_q05: Mapped[float | None] = mapped_column(Double)
    per10k_q95: Mapped[float | None] = mapped_column(Double)
    per10k_reason: Mapped[str | None] = mapped_column(Text)
