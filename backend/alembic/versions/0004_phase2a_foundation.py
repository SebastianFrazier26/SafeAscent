"""Phase 2a foundation: internal schema, grid_bucket_key(), frozen accidents_raw, accident
repair columns, ingest run log, quarantine, MP tick aggregates, mp_ticks quarantine columns.

Schema `internal` is created by db/roles/create_roles_phase2.sql in prod (CREATE SCHEMA
needs CREATE on the database, which migrator deliberately lacks). Here it is created only
when the current role may, i.e. in local/CI databases.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_phase2a_foundation"
down_revision = "0003_hist_insufficient_data"
branch_labels = None
depends_on = None

# One SQL definition of the D4 grid key. Callers pass float8 (grid.grid_bucket_sql adds the
# casts) so a numeric column buckets exactly as the double Python reads from it.
# Bounds mirror app.pipelines.grid.grid_bucket exactly (0<=lat<=90, -180<=lon<=180: the
# northern-hemisphere-US assumption); SQL returns NULL for out-of-range input rather than
# raising, since a CHECK constraint has no Python-style exception to throw.
GRID_BUCKET_KEY_SQL = """
CREATE FUNCTION public.grid_bucket_key(lat double precision, lon double precision) RETURNS integer
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
  SELECT CASE WHEN lat BETWEEN 0.0 AND 90.0 AND lon BETWEEN -180.0 AND 180.0
    THEN floor(lat * 10 + 0.5)::int * 10000 + floor(lon * 10 + 0.5)::int + 5000
  END
$$
"""

ENUM_CHECKS = {
    "date_precision": ("day", "month", "year", "unknown"),
    "geocode_precision": ("exact", "crag", "area", "park_centroid", "region_fallback", "unknown"),
    "activity_class": ("climbing", "climbing_approach", "non_climbing"),
    "severity_scale": ("full", "fatal_only", "unknown"),
    "exp_stated_level": ("novice", "intermediate", "experienced", "expert", "unknown"),
    "guided": ("guided", "unguided", "unknown"),
}

# Defaults the upgrade() columns are born with (NULL, except is_canonical=true and the two
# NOT NULL 'unknown' text columns). downgrade() refuses if any row has drifted from this, since
# dropping the columns would silently discard real repair work.
DIRTY_ACCIDENTS_WHERE = (
    "date_precision IS NOT NULL OR year_source IS NOT NULL OR year_lo IS NOT NULL OR "
    "year_hi IS NOT NULL OR geocode_precision IS NOT NULL OR geocode_method IS NOT NULL OR "
    "country IS NOT NULL OR activity_class IS NOT NULL OR activity_rule_version IS NOT NULL OR "
    "inclusion_flag IS NOT NULL OR incident_group_id IS NOT NULL OR NOT is_canonical OR "
    "severity_scale IS NOT NULL OR excluded_reason IS NOT NULL OR source_url IS NOT NULL OR "
    "updated_at IS NOT NULL OR exp_years_climbing IS NOT NULL OR exp_stated_level <> 'unknown' OR "
    "exp_first_season IS NOT NULL OR guided <> 'unknown' OR exp_rule_version IS NOT NULL"
)


def _ensure_internal_schema() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT to_regnamespace('internal')").scalar() is not None:
        return
    can_create = bind.exec_driver_sql(
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
    ).scalar()
    if not can_create:
        raise RuntimeError(
            "schema internal does not exist and this role cannot create it: run "
            "backend/db/roles/create_roles_phase2.sql as the owner first"
        )
    op.execute("CREATE SCHEMA internal")


def upgrade() -> None:
    # This migration touches live tables (accidents, mp_ticks); fail fast under lock
    # contention rather than blocking prod traffic for the run's duration.
    op.execute("SET LOCAL lock_timeout = '5s'")
    _ensure_internal_schema()
    op.execute(GRID_BUCKET_KEY_SQL)

    # Snapshot before the new columns exist, so accidents_raw keeps the pre-2a shape.
    op.execute("CREATE TABLE internal.accidents_raw AS TABLE public.accidents")
    bind = op.get_bind()
    live = bind.exec_driver_sql("SELECT count(*) FROM public.accidents").scalar_one()
    raw = bind.exec_driver_sql("SELECT count(*) FROM internal.accidents_raw").scalar_one()
    if live != raw:
        raise RuntimeError(f"accidents_raw copy mismatch: {raw} of {live} rows")
    op.execute("ALTER TABLE internal.accidents_raw ADD PRIMARY KEY (accident_id)")

    add = op.add_column
    add("accidents", sa.Column("date_precision", sa.Text(), nullable=True))
    add("accidents", sa.Column("year_source", sa.Text(), nullable=True))
    add("accidents", sa.Column("year_lo", sa.SmallInteger(), nullable=True))
    add("accidents", sa.Column("year_hi", sa.SmallInteger(), nullable=True))
    add("accidents", sa.Column("geocode_precision", sa.Text(), nullable=True))
    add("accidents", sa.Column("geocode_method", sa.Text(), nullable=True))
    add("accidents", sa.Column("country", sa.Text(), nullable=True))
    add("accidents", sa.Column("activity_class", sa.Text(), nullable=True))
    add("accidents", sa.Column("activity_rule_version", sa.Text(), nullable=True))
    add("accidents", sa.Column("inclusion_flag", sa.Text(), nullable=True))
    add("accidents", sa.Column("incident_group_id", sa.Integer(), nullable=True))
    add("accidents", sa.Column("is_canonical", sa.Boolean(), server_default=sa.true(), nullable=False))
    add("accidents", sa.Column("severity_scale", sa.Text(), nullable=True))
    add("accidents", sa.Column("excluded_reason", sa.Text(), nullable=True))
    add("accidents", sa.Column("source_url", sa.Text(), nullable=True))
    add("accidents", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    add("accidents", sa.Column("exp_years_climbing", sa.SmallInteger(), nullable=True))
    add("accidents", sa.Column("exp_stated_level", sa.Text(), server_default="unknown", nullable=False))
    add("accidents", sa.Column("exp_first_season", sa.Boolean(), nullable=True))
    add("accidents", sa.Column("guided", sa.Text(), server_default="unknown", nullable=False))
    add("accidents", sa.Column("exp_rule_version", sa.Text(), nullable=True))
    for column, values in ENUM_CHECKS.items():
        allowed = ", ".join(f"'{v}'" for v in values)
        op.create_check_constraint(f"accidents_{column}_check", "accidents", f"{column} IS NULL OR {column} IN ({allowed})")
    op.create_check_constraint(
        "accidents_exp_years_climbing_check", "accidents", "exp_years_climbing IS NULL OR exp_years_climbing BETWEEN 0 AND 80"
    )
    op.create_check_constraint(
        "accidents_year_bounds_check", "accidents", "year_lo IS NULL OR year_hi IS NULL OR year_lo <= year_hi"
    )
    op.create_index("idx_accidents_incident_group", "accidents", ["incident_group_id"])

    op.create_table(
        "source_ingest_log",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_in", sa.Integer(), nullable=True),
        sa.Column("rows_upserted", sa.Integer(), nullable=True),
        sa.Column("rows_quarantined", sa.Integer(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("validation_report", postgresql.JSONB(), nullable=True),
        sa.Column("cost_units", sa.Numeric(), nullable=True),
        sa.CheckConstraint("status IN ('running', 'ok', 'rejected', 'failed')", name="source_ingest_log_status_check"),
    )
    op.create_index("ix_source_ingest_log_source_finished", "source_ingest_log", ["source", "finished_at"])

    op.create_table(
        "accident_revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("accident_id", sa.Integer(), sa.ForeignKey("public.accidents.accident_id"), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("accident_id", "field", "rule_version", name="accident_revisions_key"),
        schema="internal",
    )

    op.create_table(
        "ingest_quarantine",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("public.source_ingest_log.run_id"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("row_ref", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="internal",
    )
    op.create_index("ix_ingest_quarantine_run", "ingest_quarantine", ["run_id"], schema="internal")

    op.create_table(
        "mp_tick_aggregates",
        sa.Column("mp_route_id", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("style", sa.Text(), nullable=False),
        sa.Column("tick_count", sa.Integer(), nullable=False),
        sa.Column("scrape_run_id", sa.Text(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loaded_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("mp_route_id", "period", "style"),
        sa.CheckConstraint(
            "period = 'total' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="mp_tick_aggregates_period_check"
        ),
        sa.CheckConstraint("tick_count >= 0", name="mp_tick_aggregates_count_check"),
        schema="internal",
    )

    op.add_column("mp_ticks", sa.Column("quarantine_reason", sa.Text(), nullable=True))
    op.add_column("mp_ticks", sa.Column("quarantine_rule_version", sa.Text(), nullable=True))
    # NOT VALID: the column is brand new and already all-NULL, so there is nothing to
    # validate; skipping the scan avoids holding a lock on live mp_ticks rows to prove it.
    op.create_check_constraint(
        "mp_ticks_quarantine_reason_check",
        "mp_ticks",
        "quarantine_reason IS NULL OR quarantine_reason IN ('future', 'orphan_route', 'pre_1970')",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "internal.accident_revisions",
        "internal.mp_tick_aggregates",
        "internal.ingest_quarantine",
        "public.source_ingest_log",
    ):
        rows = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if rows:
            raise RuntimeError(f"refusing to downgrade 0004: {table} has {rows} rows")
    dirty_accidents = bind.exec_driver_sql(f"SELECT count(*) FROM accidents WHERE {DIRTY_ACCIDENTS_WHERE}").scalar_one()
    if dirty_accidents:
        raise RuntimeError(f"refusing to downgrade 0004: accidents has {dirty_accidents} rows with non-default repair columns")
    marked_ticks = bind.exec_driver_sql("SELECT count(*) FROM mp_ticks WHERE quarantine_reason IS NOT NULL").scalar_one()
    if marked_ticks:
        raise RuntimeError(f"refusing to downgrade 0004: mp_ticks has {marked_ticks} quarantined rows")
    op.drop_constraint("mp_ticks_quarantine_reason_check", "mp_ticks", type_="check")
    op.drop_column("mp_ticks", "quarantine_rule_version")
    op.drop_column("mp_ticks", "quarantine_reason")
    op.drop_table("mp_tick_aggregates", schema="internal")
    op.drop_table("ingest_quarantine", schema="internal")
    op.drop_table("accident_revisions", schema="internal")
    op.drop_table("source_ingest_log")
    op.drop_index("idx_accidents_incident_group", "accidents")
    for name in (*ENUM_CHECKS, "exp_years_climbing", "year_bounds"):
        op.drop_constraint(f"accidents_{name}_check", "accidents", type_="check")
    for column in (
        "exp_rule_version", "guided", "exp_first_season", "exp_stated_level", "exp_years_climbing",
        "updated_at", "source_url", "excluded_reason", "severity_scale", "is_canonical", "incident_group_id",
        "inclusion_flag", "activity_rule_version", "activity_class", "country", "geocode_method",
        "geocode_precision", "year_hi", "year_lo", "year_source", "date_precision",
    ):
        op.drop_column("accidents", column)
    op.drop_table("accidents_raw", schema="internal")
    op.execute("DROP FUNCTION public.grid_bucket_key(double precision, double precision)")
