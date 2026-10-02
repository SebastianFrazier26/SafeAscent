import asyncio
import importlib.util
from pathlib import Path

import asyncpg
import pytest
from alembic import command

from app.data.repair import geocode
from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg
from tests.test_repair_dedupe import CLEAN_CASES, _raw

REV_0005 = "0005_gnis_duplicate_decisions"
REV_0006 = "0006_accidents_clean"
# The Phase 3 interface (plan 3 and the trainer read exactly these, in this order).
CLEAN_COLUMNS = [
    "accident_id", "source", "source_id", "date", "year", "date_precision", "year_lo", "year_hi", "state", "country",
    "mountain", "route", "latitude", "longitude", "coordinates", "elevation_meters", "geocode_precision",
    "accident_type", "activity", "activity_class", "inclusion_flag", "injury_severity", "severity_scale",
    "incident_group_id", "mp_route_id", "exp_years_climbing", "exp_stated_level", "exp_first_season", "guided",
    "source_url", "point_trusted",
]

# Row 7 has no country: O3 (owner) keeps it, since only a known non-US country drops a row.
SEED = """
INSERT INTO accidents (accident_id, source, date, is_canonical, activity_class, country, excluded_reason, date_precision, geocode_precision) VALUES
  (1, 'AAC', '2010-07-02', true,  'climbing',          'US', NULL,              'day',     'area'),
  (2, 'AAC', '2010-07-15', true,  'climbing_approach', 'US', NULL,              'month',   'region_fallback'),
  (3, 'AAC', '2010-07-02', false, 'climbing',          'US', NULL,              'day',     'area'),
  (4, 'AAC', '2010-07-02', true,  'non_climbing',      'US', NULL,              'day',     'area'),
  (5, 'AAC', '2010-07-02', true,  'climbing',          'CA', NULL,              'day',     'area'),
  (6, 'AAC', '2023-07-02', true,  'climbing',          'US', 'year_unverified', 'unknown', 'area'),
  (7, 'AAC', '2010-07-03', true,  'climbing',          NULL, NULL,              'day',     NULL);
"""


def _rows(name: str, sql: str) -> list[object]:
    async def go() -> list[object]:
        conn = await asyncpg.connect(pg_url(name))
        try:
            return [r[0] for r in await conn.fetch(sql)]
        finally:
            await conn.close()

    return asyncio.run(go())


def _load_0006():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0006_accidents_clean.py"
    spec = importlib.util.spec_from_file_location("m0006", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_view_point_trusted_matches_the_python_set():
    # The migration is frozen and never imports app code, so the pair is pinned here instead.
    assert set(_load_0006().TRUSTED_POINT_PRECISIONS) == set(geocode.TRUSTED_POINT_PRECISIONS)


def test_0006_revision_id_fits_alembic_version():
    assert len(REV_0006) <= 32


@requires_pg
def test_0006_clean_views_apply_every_rule_and_hide_narratives():
    with migrated_db("head", SEED) as name:
        assert _rows(name, "SELECT version_num FROM alembic_version") == [REV_0006]
        assert _rows(name, "SELECT accident_id FROM accidents_clean ORDER BY 1") == [1, 2, 7]
        assert _rows(name, "SELECT accident_id FROM accidents_clean_daily ORDER BY 1") == [1, 7]
        for view in ("accidents_clean", "accidents_clean_daily"):
            cols = _rows(
                name,
                "SELECT column_name::text FROM information_schema.columns "
                f"WHERE table_schema = 'public' AND table_name = '{view}' ORDER BY ordinal_position",
            )
            assert cols == CLEAN_COLUMNS
            assert not {"description", "tags", "age_range", "location"} & set(cols)


@requires_pg
def test_0006_point_trusted_labels_fallback_points_and_ignores_experience():
    with migrated_db("head", SEED) as name:
        # Row 7 has no geocode label yet: untrusted, never NULL.
        assert _rows(name, "SELECT accident_id FROM accidents_clean WHERE point_trusted ORDER BY 1") == [1]
        assert _rows(name, "SELECT count(*) FROM accidents_clean WHERE point_trusted IS NULL") == [0]
        # Every row has its experience columns at the NULL/'unknown' default and is still clean:
        # the view never filters on R12 facts (spec R12, scoring never requires them).
        assert _rows(name, "SELECT accident_id FROM accidents_clean WHERE exp_years_climbing IS NULL "
                           "AND exp_stated_level = 'unknown' AND exp_first_season IS NULL AND guided = 'unknown' "
                           "ORDER BY 1") == [1, 2, 7]


@requires_pg
def test_0006_view_keeps_exactly_the_rows_dedupe_counts_as_clean():
    # I3: R5 picks a group head the clean set keeps, so the view and DupRow.clean must agree.
    values = []
    for i, (override, _) in enumerate(CLEAN_CASES, start=1):
        row = {"activity_class": "climbing", "country": "US", "excluded_reason": None} | override
        values.append("({}, 'AAC', '2010-07-02', true, {}, {}, {}, 'day')".format(
            i, *(f"'{row[c]}'" if row[c] is not None else "NULL" for c in ("activity_class", "country", "excluded_reason"))
        ))
    seed = ("INSERT INTO accidents (accident_id, source, date, is_canonical, activity_class, country, excluded_reason, "
            "date_precision) VALUES " + ", ".join(values))
    with migrated_db("head", seed) as name:
        kept = set(_rows(name, "SELECT accident_id FROM accidents_clean"))
    assert kept == {i for i, (override, _) in enumerate(CLEAN_CASES, start=1) if _raw(**override).clean}
    assert kept == {i for i, (_, clean) in enumerate(CLEAN_CASES, start=1) if clean}


@requires_pg
def test_0006_downgrade_drops_the_views_and_refuses_while_something_depends_on_them():
    with migrated_db("head", SEED) as name:
        run_sql(name, "CREATE VIEW later_plan AS SELECT accident_id FROM accidents_clean_daily")
        # No CASCADE: a later object built on the views (plan 3's) is never dropped silently.
        with pytest.raises(Exception, match="depend"):
            command.downgrade(_alembic_cfg(name), REV_0005)
        run_sql(name, "DROP VIEW later_plan")
        command.downgrade(_alembic_cfg(name), REV_0005)
        with pytest.raises(asyncpg.UndefinedTableError):
            run_sql(name, "SELECT 1 FROM accidents_clean")
        assert _rows(name, "SELECT count(*) FROM accidents") == [7]
        command.upgrade(_alembic_cfg(name), "head")
        assert _rows(name, "SELECT accident_id FROM accidents_clean_daily ORDER BY 1") == [1, 7]
