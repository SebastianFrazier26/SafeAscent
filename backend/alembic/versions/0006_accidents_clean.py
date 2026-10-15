"""accidents_clean (Phase 3's incident definition) and accidents_clean_daily (the
day-precision set CM trains on). No narrative columns: Phase 3 grants trainer these views,
never the table.

O3 (owner, 2026-09-30): only a known non-US country drops a row; a NULL country stays.
dedupe.DupRow.clean mirrors this WHERE (test_migration_0006 pins the pair).
"""

from alembic import op

revision = "0006_accidents_clean"
down_revision = "0005_gnis_duplicate_decisions"
branch_labels = None
depends_on = None

# Must equal geocode.TRUSTED_POINT_PRECISIONS; test_migration_0006 pins the pair. A literal,
# not an import: a migration must not change when app code does.
TRUSTED_POINT_PRECISIONS = ("exact", "crag", "area", "park_centroid")

COLUMNS = (
    "accident_id, source, source_id, date, year, date_precision, year_source, year_lo, year_hi, state, country, "
    "mountain, route, latitude, longitude, coordinates, elevation_meters, geocode_precision, accident_type, activity, "
    "activity_class, inclusion_flag, injury_severity, severity_scale, incident_group_id, mp_route_id, "
    "exp_years_climbing, exp_stated_level, exp_first_season, guided, source_url, "
    "coalesce(geocode_precision IN ({}), false) AS point_trusted".format(
        ", ".join(f"'{p}'" for p in TRUSTED_POINT_PRECISIONS)
    )
)


def upgrade() -> None:
    # As 0005 (G12): in one alembic run this repeats an earlier revision's 5s; set here so a
    # standalone 0006 gets the same fail-fast bound against a lock held on accidents.
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Default (owner-rights) views on purpose, not security_invoker: a reader needs SELECT on
    # the view only, never on accidents and its narratives.
    op.execute(
        f"CREATE VIEW accidents_clean AS SELECT {COLUMNS} FROM accidents "
        "WHERE is_canonical AND activity_class IN ('climbing', 'climbing_approach') "
        "AND (country = 'US' OR country IS NULL) AND excluded_reason IS NULL"
    )
    op.execute("CREATE VIEW accidents_clean_daily AS SELECT * FROM accidents_clean WHERE date_precision = 'day'")


def downgrade() -> None:
    # No CASCADE: Postgres refuses while a later object (plan 3) still reads the views.
    op.execute("DROP VIEW accidents_clean_daily")
    op.execute("DROP VIEW accidents_clean")
