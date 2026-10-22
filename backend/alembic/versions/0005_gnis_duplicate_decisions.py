"""USGS GNIS summits (public domain reference data) and R5 duplicate decisions.

The revision id stays within alembic_version.version_num's VARCHAR(32) (G1).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_gnis_duplicate_decisions"
down_revision = "0004_phase2a_foundation"
branch_labels = None
depends_on = None

NEW_TABLES = ("internal.duplicate_decisions", "public.gnis_summits")


def upgrade() -> None:
    # env.py runs every pending revision in one transaction, so in an upgrade from below 0004
    # this repeats 0004's 5s (G12). It is set here too so applying 0005 alone gets the same
    # fail-fast bound; it creates new objects only, so the bound should never bite.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "gnis_summits",
        sa.Column("gnis_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("state_code", sa.CHAR(2), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("loaded_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("state_code ~ '^[A-Z]{2}$'", name="gnis_summits_state_code_check"),
        # BETWEEN is false for NaN, so this also refuses NaN coordinates.
        sa.CheckConstraint("lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180", name="gnis_summits_coords_check"),
    )
    op.create_index("ix_gnis_summits_name_key", "gnis_summits", ["name_key"])
    op.create_table(
        "duplicate_decisions",
        sa.Column("low_id", sa.Integer(), nullable=False),
        sa.Column("high_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("low_id", "high_id"),
        sa.CheckConstraint("low_id < high_id", name="duplicate_decisions_order_check"),
        sa.CheckConstraint("decision IN ('merge', 'distinct')", name="duplicate_decisions_decision_check"),
        sa.CheckConstraint("decided_by IN ('auto', 'owner')", name="duplicate_decisions_by_check"),
        schema="internal",
    )


def downgrade() -> None:
    bind = op.get_bind()
    owner_rows = bind.exec_driver_sql(
        "SELECT count(*) FROM internal.duplicate_decisions WHERE decided_by = 'owner'"
    ).scalar_one()
    if owner_rows:
        raise RuntimeError(f"refusing to downgrade 0005: {owner_rows} owner duplicate decisions would be lost")
    for table in NEW_TABLES:
        rows = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if rows:
            raise RuntimeError(f"refusing to downgrade 0005: {table} has {rows} rows")
    op.drop_table("duplicate_decisions", schema="internal")
    op.drop_index("ix_gnis_summits_name_key", "gnis_summits")
    op.drop_table("gnis_summits")
