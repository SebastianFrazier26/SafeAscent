"""Static features keyed by point, the per-route table Phase 3 reads (P3:150 + amendment
§3.1), and the scoring-unit view (the MVP-1 read contract), on the MP catalog. Normals are
0007's (plan 3), filled inside the paid Open-Meteo window; this view only reads them."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision = "0009_static_features"
down_revision = "0008_mp_catalog"
branch_labels = None
depends_on = None

# Same set as canonical_routes_type_group_check (0008); 'rock' is sport-or-trad, discipline unknown.
TYPE_GROUPS = "('sport', 'trad', 'alpine', 'ice', 'mixed', 'rock', 'unknown')"
SLOPE_COMMENT = "maximum Horn slope (deg) within 200 m of the point (crag steepness), not the slope at the point"
SCORING_UNIT_FEATURES = """
CREATE VIEW scoring_unit_features AS
SELECT 'route'::text AS unit_kind, f.route_id AS unit_id, f.type_group, f.area_path, f.point_key, p.tz, f.lat,
       f.elevation_m, f.dem_res_m, f.grid_bucket, f.coord_precision, m.month::smallint AS month,
       n.tmax_mean, n.tmin_mean, n.precip_mm, n.snowfall_cm, n.freeze_thaw_days,
       CASE WHEN f.grid_bucket IS NULL THEN 'no_location' ELSE COALESCE(s.status, 'pending') END AS normals_status,
       f.feature_version
FROM route_static_features f
JOIN canonical_routes c ON c.route_id = f.route_id AND c.scorable
LEFT JOIN feature_points p ON p.point_key = f.point_key
CROSS JOIN generate_series(1, 12) AS m(month)
LEFT JOIN cell_normals_status s ON s.grid_bucket = f.grid_bucket
LEFT JOIN cell_climate_normals n
  ON n.grid_bucket = f.grid_bucket AND n.month = m.month AND s.status IN ('complete', 'insufficient')
"""


class Ltree(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "LTREE"


def upgrade() -> None:
    op.create_table(
        "feature_points",
        sa.Column("point_key", sa.Text(), primary_key=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("h3_r5", sa.Text(), nullable=False),
        sa.Column("h3_r7", sa.Text(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=True),
        sa.Column("elevation_m", sa.REAL(), nullable=True),
        sa.Column("dem_source", sa.Text(), nullable=True),
        sa.Column("dem_res_m", sa.REAL(), nullable=True),
        sa.Column("slope_deg", sa.REAL(), nullable=True, comment=SLOPE_COMMENT),
        sa.Column("aspect_deg", sa.REAL(), nullable=True),
        sa.Column("aspect_confidence", sa.REAL(), nullable=True),
        sa.Column("terrain_version", sa.Text(), nullable=True),
        sa.Column("lithology", sa.Text(), nullable=True),
        sa.Column("lithology_source", sa.Text(), nullable=True),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("dem_source IS NULL OR dem_source IN ('3dep_13', '3dep_2', 'nodata', 'no_tile')",
                           name="feature_points_dem_source_check"),
        sa.CheckConstraint("lithology_source IS NULL OR lithology_source IN ('macrostrat', 'none')",
                           name="feature_points_lithology_source_check"),
    )
    op.create_index("ix_feature_points_grid_bucket", "feature_points", ["grid_bucket"])
    op.create_table(
        "route_static_features",
        sa.Column("route_id", sa.BigInteger(), sa.ForeignKey("canonical_routes.route_id"), primary_key=True,
                  autoincrement=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("area_path", Ltree(), nullable=True),
        sa.Column("type_group", sa.Text(), nullable=True),
        sa.Column("point_key", sa.Text(), sa.ForeignKey("feature_points.point_key"), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("aspect_deg", sa.REAL(), nullable=True),
        sa.Column("slope_deg", sa.REAL(), nullable=True, comment=SLOPE_COMMENT),
        sa.Column("elevation_m", sa.REAL(), nullable=True),
        sa.Column("lithology", sa.Text(), nullable=True),
        sa.Column("pitches", sa.SmallInteger(), nullable=True),
        sa.Column("length_m", sa.REAL(), nullable=True),
        sa.Column("h3_r5", sa.Text(), nullable=True),
        sa.Column("grid_bucket", sa.Integer(), nullable=True),
        sa.Column("dem_res_m", sa.REAL(), nullable=True),
        sa.Column("aspect_confidence", sa.REAL(), nullable=True),
        sa.Column("coord_precision", sa.Text(), nullable=False),
        sa.Column("lithology_source", sa.Text(), nullable=True),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source = 'mp'", name="route_static_features_source_check"),
        sa.CheckConstraint(f"type_group IS NULL OR type_group IN {TYPE_GROUPS}",
                           name="route_static_features_type_group_check"),
        sa.CheckConstraint("coord_precision IN ('own', 'inherited', 'none')",
                           name="route_static_features_coord_precision_check"),
    )
    op.execute(SCORING_UNIT_FEATURES)


def downgrade() -> None:
    sampled = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM feature_points WHERE dem_source IS NOT NULL").scalar_one()
    if sampled:
        raise RuntimeError(f"refusing to downgrade 0009: {sampled} points carry sampled elevation")
    op.execute("DROP VIEW scoring_unit_features")
    op.drop_table("route_static_features")
    op.drop_index("ix_feature_points_grid_bucket", "feature_points")
    op.drop_table("feature_points")
