"""R10 on the MP catalog: accident_route_links is the identity on accidents.mp_route_id plus
the legacy links that carried a usable MP id or an owner decision. R10 never writes accidents."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_accident_route_links"
down_revision = "0009_static_features"
branch_labels = None
depends_on = None

REASONS = "('legacy_route_no_mp_id', 'legacy_mp_id_not_in_catalog', 'legacy_mountain_only')"
VIEW = """
CREATE VIEW accident_route_links AS
SELECT a.accident_id, a.mp_route_id AS route_id, NULL::bigint AS area_id, 'mp_route_id'::text AS method
FROM accidents a JOIN canonical_routes r ON r.route_id = a.mp_route_id
UNION ALL
SELECT l.accident_id, l.route_id, l.area_id, l.method
FROM accident_legacy_links l JOIN accidents a ON a.accident_id = l.accident_id
WHERE a.mp_route_id IS NULL
"""


def upgrade() -> None:
    op.create_table(
        "accident_legacy_links",
        sa.Column("accident_id", sa.Integer(), sa.ForeignKey("accidents.accident_id"), primary_key=True,
                  autoincrement=False),
        sa.Column("route_id", sa.BigInteger(), sa.ForeignKey("canonical_routes.route_id"), nullable=True),
        sa.Column("area_id", sa.BigInteger(), sa.ForeignKey("canonical_areas.area_id"), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("r10_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("method IN ('legacy_mp_id', 'owner', 'llm')", name="accident_legacy_links_method_check"),
        sa.CheckConstraint("num_nonnulls(route_id, area_id) = 1", name="accident_legacy_links_target_check"),
        sa.CheckConstraint("(method = 'llm') = (llm_call_id IS NOT NULL)", name="accident_legacy_links_llm_check"),
    )
    op.create_table(
        "r10_unresolved",
        sa.Column("accident_id", sa.Integer(), sa.ForeignKey("accidents.accident_id"), primary_key=True,
                  autoincrement=False),
        sa.Column("legacy_route_id", sa.Integer(), nullable=True),
        sa.Column("legacy_mountain_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("candidate_route_ids", postgresql.ARRAY(sa.BigInteger()), server_default="{}", nullable=False),
        sa.Column("candidate_area_ids", postgresql.ARRAY(sa.BigInteger()), server_default="{}", nullable=False),
        sa.Column("owner_decision", sa.Text(), nullable=True),
        sa.Column("decided_route_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_area_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(f"reason IN {REASONS}", name="r10_unresolved_reason_check"),
        sa.CheckConstraint("owner_decision IS NULL OR owner_decision IN ('link', 'no_link')",
                           name="r10_unresolved_owner_decision_check"),
        sa.CheckConstraint("owner_decision IS DISTINCT FROM 'link' OR num_nonnulls(decided_route_id, decided_area_id) = 1",
                           name="r10_unresolved_link_target_check"),
        schema="internal",
    )
    op.create_table(
        "r10_llm_calls",
        sa.Column("call_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accident_id", sa.Integer(), sa.ForeignKey("accidents.accident_id"), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_reported", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("payload_fields", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("candidate_kind", sa.Text(), nullable=False),
        sa.Column("candidate_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("pick_id", sa.BigInteger(), nullable=True),
        sa.Column("pick_none", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("is_sample", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("candidate_kind IN ('route', 'area')", name="r10_llm_calls_kind_check"),
        sa.CheckConstraint("status IN ('ok', 'invalid', 'refusal', 'max_tokens', 'error')", name="r10_llm_calls_status_check"),
        # The database, not only the code, refuses an id the model was not offered.
        sa.CheckConstraint("pick_id IS NULL OR pick_id = ANY(candidate_ids)", name="r10_llm_calls_pick_offered_check"),
        sa.CheckConstraint("status <> 'ok' OR (confidence BETWEEN 0 AND 1 AND (pick_none OR pick_id IS NOT NULL))",
                           name="r10_llm_calls_ok_check"),
        sa.CheckConstraint("status = 'ok' OR (pick_id IS NULL AND pick_none IS NULL AND confidence IS NULL)",
                           name="r10_llm_calls_failed_check"),
        schema="internal",
    )
    op.create_index("ix_r10_llm_calls_accident", "r10_llm_calls", ["accident_id"], schema="internal")
    op.create_table(
        "r10_llm_gates",
        sa.Column("gate_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("sample_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("n_checked", sa.Integer(), nullable=False),
        sa.Column("n_agree", sa.Integer(), nullable=False),
        sa.Column("n_auto_band", sa.Integer(), nullable=False),
        sa.Column("n_auto_band_agree", sa.Integer(), nullable=False),
        sa.Column("auto_threshold", sa.REAL(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="internal",
    )
    op.execute(VIEW)


def downgrade() -> None:
    # Review fix 2026-10-14 (same gap as 0009): refuse on ANY row in any 0010 table, direct counts
    # as 0008. The earlier guard (owner decisions + LLM calls only) silently dropped legacy links,
    # undecided residue rows and gate decisions.
    bind = op.get_bind()
    for table in ("accident_legacy_links", "internal.r10_unresolved", "internal.r10_llm_calls",
                  "internal.r10_llm_gates"):
        n = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if n:
            raise RuntimeError(f"refusing to downgrade 0010: {table} has {n} rows")
    op.drop_table("r10_llm_gates", schema="internal")
    op.drop_index("ix_r10_llm_calls_accident", "r10_llm_calls", schema="internal")
    op.drop_table("r10_llm_calls", schema="internal")
    op.execute("DROP VIEW accident_route_links")
    op.drop_table("r10_unresolved", schema="internal")
    op.drop_table("accident_legacy_links")
