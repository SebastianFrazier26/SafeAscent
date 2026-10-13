"""MP route catalog (owner decision 2026-10-12: MP is the primary route source). Canonical
areas and routes keyed by the MP ids, derived columns only (no names, grades, URLs or prose);
the scorable view; and the internal tick-coverage view, where absent ticks read 'missing'."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision = "0008_mp_catalog"
down_revision = "0007_cell_daily_conditions"
branch_labels = None
depends_on = None

TYPE_GROUPS = "('sport', 'trad', 'alpine', 'ice', 'mixed', 'unknown')"
UNSCORABLE = "('boulder', 'no_area', 'not_us', 'unknown_type', 'no_coordinates')"
TREE_STATUS = "('us', 'foreign_root', 'cycle', 'orphan_parent')"
SCORABLE_ROUTES = """
CREATE VIEW scorable_routes AS
SELECT r.route_id, r.area_id, r.type_group, r.type_rule_version, a.path AS area_path, a.state_code, a.point_key,
       a.coord_precision, r.updated_at
FROM canonical_routes r JOIN canonical_areas a ON a.area_id = r.area_id
WHERE r.scorable
"""
TICK_COVERAGE = """
CREATE VIEW internal.route_tick_coverage AS
SELECT r.route_id,
       CASE WHEN s.mp_route_id IS NULL THEN 'missing' WHEN s.complete THEN 'complete' ELSE 'scraped' END AS tick_status,
       s.complete_through, s.scraped_at, CASE WHEN s.complete THEN s.total_ticks END AS tick_total
FROM canonical_routes r
LEFT JOIN internal.route_tick_scrapes s ON s.mp_route_id = r.route_id
"""
TICK_MONTHS = """
CREATE VIEW internal.route_tick_months AS
SELECT t.mp_route_id AS route_id, t.period, t.style, t.tick_count
FROM internal.mp_tick_aggregates t
JOIN internal.route_tick_scrapes s ON s.mp_route_id = t.mp_route_id AND s.complete
WHERE t.period <> 'total' AND to_date(t.period || '-01', 'YYYY-MM-DD') <= s.complete_through
"""
# Only totals the loader accepted exist (it accepts complete routes only); the last fully observed
# month is the one before the scrape's UTC month, the same cut-off the loader's partial_month rule uses.
TICK_BACKFILL = """
INSERT INTO internal.route_tick_scrapes (mp_route_id, source, scraped_at, complete, complete_through, total_ticks,
  loaded_run_id)
SELECT t.mp_route_id, 'mp_scrape', t.scraped_at, true,
       (date_trunc('month', t.scraped_at AT TIME ZONE 'UTC') - interval '1 day')::date, t.tick_count, t.loaded_run_id
FROM internal.mp_tick_aggregates t JOIN mp_routes r ON r.mp_route_id = t.mp_route_id
WHERE t.period = 'total' AND t.style = 'all' AND t.scraped_at IS NOT NULL
"""


class Ltree(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "LTREE"


def _ensure_ltree() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT 1 FROM pg_extension WHERE extname = 'ltree'").first():
        return
    if not bind.exec_driver_sql("SELECT has_database_privilege(current_user, current_database(), 'CREATE')").scalar():
        raise RuntimeError("extension ltree is missing: run backend/db/owner/extensions_mp_catalog.sql as the owner first")
    op.execute("CREATE EXTENSION ltree")


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    ]


def upgrade() -> None:
    _ensure_ltree()
    op.create_table(
        "canonical_areas",
        sa.Column("area_id", sa.BigInteger(), sa.ForeignKey("mp_locations.mp_id", ondelete="RESTRICT"),
                  primary_key=True, autoincrement=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("path", Ltree(), nullable=True),
        sa.Column("depth", sa.SmallInteger(), nullable=True),
        sa.Column("tree_status", sa.Text(), nullable=False),
        sa.Column("state_code", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("coord_precision", sa.Text(), nullable=False),
        sa.Column("coord_source_id", sa.BigInteger(), nullable=True),
        sa.Column("point_key", sa.Text(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(f"tree_status IN {TREE_STATUS}", name="canonical_areas_tree_status_check"),
        sa.CheckConstraint("(path IS NOT NULL) = (tree_status IN ('us', 'foreign_root'))", name="canonical_areas_path_check"),
        sa.CheckConstraint("(state_code IS NOT NULL) = (tree_status = 'us')", name="canonical_areas_state_check"),
        sa.CheckConstraint("state_code IS NULL OR state_code ~ '^[A-Z]{2}$'", name="canonical_areas_state_format_check"),
        sa.CheckConstraint("coord_precision IN ('own', 'inherited', 'none')", name="canonical_areas_coord_precision_check"),
        sa.CheckConstraint("(lat IS NULL) = (lon IS NULL)", name="canonical_areas_pair_check"),
        sa.CheckConstraint("(coord_precision = 'none') = (lat IS NULL)", name="canonical_areas_coords_check"),
        sa.CheckConstraint("(coord_precision = 'none') = (coord_source_id IS NULL)", name="canonical_areas_source_check"),
        sa.CheckConstraint("(point_key IS NULL) = (lat IS NULL)", name="canonical_areas_point_key_check"),
    )
    op.create_index("ix_canonical_areas_path", "canonical_areas", ["path"], postgresql_using="gist")
    op.create_index("ix_canonical_areas_point_key", "canonical_areas", ["point_key"])
    op.create_table(
        "canonical_routes",
        sa.Column("route_id", sa.BigInteger(), sa.ForeignKey("mp_routes.mp_route_id", ondelete="RESTRICT"),
                  primary_key=True, autoincrement=False),
        sa.Column("area_id", sa.BigInteger(), sa.ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("type_group", sa.Text(), nullable=True),
        sa.Column("is_boulder", sa.Boolean(), nullable=False),
        sa.Column("disciplines", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("type_rule_version", sa.Text(), nullable=False),
        sa.Column("scorable", sa.Boolean(), nullable=False),
        sa.Column("unscorable_reason", sa.Text(), nullable=True),
        sa.Column("facts_sha256", sa.Text(), nullable=False),
        *_stamps(),
        sa.CheckConstraint(f"type_group IS NULL OR type_group IN {TYPE_GROUPS}", name="canonical_routes_type_group_check"),
        sa.CheckConstraint(f"unscorable_reason IS NULL OR unscorable_reason IN {UNSCORABLE}",
                           name="canonical_routes_reason_check"),
        sa.CheckConstraint("scorable = (unscorable_reason IS NULL)", name="canonical_routes_scorable_check"),
        sa.CheckConstraint(
            "(is_boulder AND type_group IS NULL AND NOT scorable) OR (NOT is_boulder AND type_group IS NOT NULL)",
            name="canonical_routes_boulder_check",
        ),
    )
    op.create_index("ix_canonical_routes_area", "canonical_routes", ["area_id"])
    op.execute(SCORABLE_ROUTES)
    op.create_table(
        "route_tick_scrapes",
        sa.Column("mp_route_id", sa.BigInteger(), sa.ForeignKey("mp_routes.mp_route_id", ondelete="RESTRICT"),
                  primary_key=True, autoincrement=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("complete_through", sa.Date(), nullable=True),
        sa.Column("total_ticks", sa.Integer(), nullable=True),
        sa.Column("loaded_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('mp_scrape')", name="route_tick_scrapes_source_check"),
        sa.CheckConstraint("complete = (complete_through IS NOT NULL)", name="route_tick_scrapes_complete_check"),
        sa.CheckConstraint("complete OR total_ticks IS NULL", name="route_tick_scrapes_total_check"),
        sa.CheckConstraint("total_ticks IS NULL OR total_ticks >= 0", name="route_tick_scrapes_count_check"),
        schema="internal",
    )
    op.execute(TICK_BACKFILL)
    op.execute(TICK_COVERAGE)
    op.execute(TICK_MONTHS)


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM canonical_routes").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0008: canonical_routes has {n} rows")
    op.execute("DROP VIEW internal.route_tick_months")
    op.execute("DROP VIEW internal.route_tick_coverage")
    op.drop_table("route_tick_scrapes", schema="internal")
    op.execute("DROP VIEW scorable_routes")
    op.drop_table("canonical_routes")
    op.drop_table("canonical_areas")
