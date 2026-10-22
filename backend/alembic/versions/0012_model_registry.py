"""Phase 3 model registry and stored scores, in schema model (DP1): trainer writes, app and analyst
read, and Phase 1's public-schema write check stays untouched. Scores are per route x month per
version (DP2): with CM = 1 in MVP-1 a daily table would repeat each month's row ~30 times."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_model_registry"
down_revision = "0011_exposure_index"
branch_labels = None
depends_on = None

TYPE_GROUPS = "('sport', 'trad', 'rock', 'alpine', 'ice', 'mixed')"
STATUSES = "('candidate', 'active', 'previous', 'rejected', 'retired')"


def _ensure_model_schema() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT to_regnamespace('model')").scalar() is not None:
        return
    if not bind.exec_driver_sql("SELECT has_database_privilege(current_user, current_database(), 'CREATE')").scalar():
        raise RuntimeError("schema model does not exist: run backend/db/owner/schema_model_phase3.sql as the owner first")
    op.execute("CREATE SCHEMA model")


def upgrade() -> None:
    _ensure_model_schema()
    op.create_table(
        "registry",
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("family", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("data_hash", sa.Text(), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("data_cutoff", sa.Date(), nullable=False),
        sa.Column("exposure_as_of", sa.Date(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(), nullable=True),
        sa.Column("model_card", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("bundle", sa.LargeBinary(), nullable=True),
        sa.Column("bundle_sha256", sa.Text(), nullable=True),
        sa.Column("bundle_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_by", sa.Text(), nullable=True),
        sa.Column("parent_version", sa.Text(), sa.ForeignKey("model.registry.version"), nullable=True),
        sa.Column("gate_result", postgresql.JSONB(), nullable=True),
        sa.Column("diff_report", postgresql.JSONB(), nullable=True),
        sa.Column("scores_rows", sa.Integer(), nullable=True),
        sa.CheckConstraint("family IN ('kernel', 'eb_v1')", name="registry_family_check"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="registry_status_check"),
        sa.CheckConstraint("promoted_by IS NULL OR promoted_by IN ('auto', 'owner')", name="registry_promoted_by_check"),
        sa.CheckConstraint("num_nulls(bundle, bundle_sha256, bundle_bytes) IN (0, 3)", name="registry_bundle_check"),
        sa.CheckConstraint(
            "family = 'kernel' OR status NOT IN ('active', 'previous') OR (bundle IS NOT NULL AND scores_rows > 0)",
            name="registry_servable_check"),
        sa.CheckConstraint(
            "family = 'kernel' OR (status IN ('active', 'previous', 'retired')) = (promoted_at IS NOT NULL)",
            name="registry_promoted_at_check"),
        schema="model",
    )
    op.create_index("ux_registry_one_active", "registry", [sa.text("(true)")], unique=True, schema="model",
                    postgresql_where=sa.text("status = 'active'"))
    op.create_index("ux_registry_one_previous", "registry", [sa.text("(true)")], unique=True, schema="model",
                    postgresql_where=sa.text("status = 'previous'"))
    real = sa.REAL()
    dbl = sa.Double()  # pre-flight F31: rr/pct/per10k in double precision; float32 underflows rr_q05 to 0
    op.create_table(
        "route_scores",
        sa.Column("model_version", sa.Text(), sa.ForeignKey("model.registry.version", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("route_id", sa.BigInteger(), sa.ForeignKey("canonical_routes.route_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("area_id", sa.BigInteger(), sa.ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("insufficient", sa.Boolean(), nullable=False),
        sa.Column("insufficient_reason", sa.Text(), nullable=True),
        *(sa.Column(c, dbl, nullable=True) for c in ("rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05",
                                                      "pct_q95")),
        sa.Column("conf_level", sa.Text(), nullable=False),
        *(sa.Column(c, real, nullable=True) for c in ("n_eff_geo", "n_eff_feature")),
        *(sa.Column(c, dbl, nullable=True) for c in ("per10k_mean", "per10k_q05", "per10k_q95")),
        sa.Column("per10k_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("model_version", "route_id", "month"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="route_scores_month_check"),
        sa.CheckConstraint(f"type_group IN {TYPE_GROUPS}", name="route_scores_type_group_check"),
        sa.CheckConstraint("insufficient_reason IS NULL OR insufficient_reason IN ('no_evidence')",
                           name="route_scores_reason_check"),
        sa.CheckConstraint("conf_level IN ('high', 'medium', 'low', 'insufficient')", name="route_scores_conf_check"),
        sa.CheckConstraint("insufficient = (conf_level = 'insufficient')", name="route_scores_conf_flag_check"),
        sa.CheckConstraint("insufficient = (insufficient_reason IS NOT NULL)", name="route_scores_reason_flag_check"),
        sa.CheckConstraint(
            "CASE WHEN insufficient THEN num_nonnulls(rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95) = 0 "
            "ELSE num_nulls(rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95) = 0 END",
            name="route_scores_numbers_check"),
        sa.CheckConstraint("rr_q05 IS NULL OR (0 < rr_q05 AND rr_q05 <= rr_mean AND rr_mean <= rr_q95)",
                           name="route_scores_rr_order_check"),
        sa.CheckConstraint(
            "percentile IS NULL OR (0 <= pct_q05 AND pct_q05 <= percentile AND percentile <= pct_q95 AND pct_q95 <= 100)",
            name="route_scores_pct_order_check"),
        sa.CheckConstraint("num_nulls(per10k_mean, per10k_q05, per10k_q95) IN (0, 3)", name="route_scores_per10k_check"),
        sa.CheckConstraint("(per10k_mean IS NULL) = (per10k_reason IS NOT NULL OR insufficient)",
                           name="route_scores_per10k_reason_check"),
        schema="model",
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("model.route_scores", "model.registry"):
        n = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if n:
            raise RuntimeError(f"refusing to downgrade 0012: {table} has {n} rows")
    op.drop_table("route_scores", schema="model")
    op.drop_index("ux_registry_one_previous", "registry", schema="model")
    op.drop_index("ux_registry_one_active", "registry", schema="model")
    op.drop_table("registry", schema="model")
