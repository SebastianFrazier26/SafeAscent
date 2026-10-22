"""Exposure (P2-9) on the MP catalog: one row per area x type group x month per snapshot, the raw
proxy components with missing flags, tick coverage, and an unfitted point with x/÷3 bounds. In
schema internal: it carries MP tick aggregates, and the Phase 1 role check requires app to read
every public table, so app stays out by schema rather than by a REVOKE."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_exposure_index"
down_revision = "0010_accident_route_links"
branch_labels = None
depends_on = None

# Scorable type groups only (0008's set minus 'unknown', which is never scorable).
TYPE_GROUPS = "('sport', 'trad', 'rock', 'alpine', 'ice', 'mixed')"


def upgrade() -> None:
    op.create_table(
        "exposure_index",
        sa.Column("area_id", sa.BigInteger(), sa.ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("n_routes", sa.Integer(), nullable=False),
        sa.Column("proxy", postgresql.JSONB(), nullable=False),
        sa.Column("proxy_version", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("data_cutoff", sa.Date(), nullable=False),
        sa.Column("tick_coverage", sa.REAL(), nullable=True),
        sa.Column("exposure_point", sa.REAL(), nullable=True),
        sa.Column("exposure_lo", sa.REAL(), nullable=True),
        sa.Column("exposure_hi", sa.REAL(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("area_id", "type_group", "month", "as_of", "data_cutoff"),
        sa.CheckConstraint(f"type_group IN {TYPE_GROUPS}", name="exposure_index_type_group_check"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="exposure_index_month_check"),
        sa.CheckConstraint("n_routes > 0", name="exposure_index_n_routes_check"),
        sa.CheckConstraint("jsonb_typeof(proxy) = 'object'", name="exposure_index_proxy_check"),
        sa.CheckConstraint("data_cutoff <= as_of", name="exposure_index_cutoff_check"),
        sa.CheckConstraint("tick_coverage IS NULL OR tick_coverage BETWEEN 0 AND 1",
                           name="exposure_index_tick_coverage_check"),
        sa.CheckConstraint(
            "(exposure_point IS NULL) = (exposure_lo IS NULL) AND (exposure_lo IS NULL) = (exposure_hi IS NULL)",
            name="exposure_index_bounds_check"),
        schema="internal",
    )
    op.create_index("ix_exposure_index_snapshot", "exposure_index", ["as_of", "data_cutoff"], schema="internal")


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM internal.exposure_index").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0011: internal.exposure_index has {n} rows")
    op.drop_index("ix_exposure_index_snapshot", "exposure_index", schema="internal")
    op.drop_table("exposure_index", schema="internal")
