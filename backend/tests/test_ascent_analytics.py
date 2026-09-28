"""ascent-analytics against a throwaway, migrated database on a real PostGIS server.

Needs MIGRATIONS_TEST_ADMIN_URL (see test_migrations.py) and skips otherwise.
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.v1.mp_routes import get_ascent_analytics
from tests.test_migrations import ADMIN_URL, _alembic_cfg, _db_url, _execute

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="MIGRATIONS_TEST_ADMIN_URL not set")

ROUTE = 111
OTHER_ROUTE = 222

# Accident 1 is the mislink the old join counted: legacy route_id collides with this
# route's MP id while mp_route_id points at another route.
SEED_SQL = f"""
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (10, 'Fixture Crag', 40.0, -105.0);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
    ({ROUTE}, 'Fixture Route', 10, 'Trad'),
    ({OTHER_ROUTE}, 'Other Route', 10, 'Trad');
INSERT INTO routes (route_id, name) VALUES ({ROUTE}, 'Legacy Route');
INSERT INTO mp_ticks (tick_id, route_id, climber_name, tick_date) VALUES
    (1, '{ROUTE}', 'climber-a', '2025-01-04'),
    (2, '{ROUTE}', 'climber-b', '2025-01-11'),
    (3, '{ROUTE}', 'climber-c', '2025-07-02'),
    (4, '{ROUTE}', 'climber-d', '3901-01-15');
INSERT INTO accidents (accident_id, date, route_id, mp_route_id) VALUES
    (1, '2020-01-05', {ROUTE}, {OTHER_ROUTE}),
    (2, '2019-07-10', NULL, {ROUTE}),
    (4, '2008-03-02', NULL, {ROUTE}),
    (3, NULL, NULL, {ROUTE});
"""


@pytest.fixture
def seeded_db() -> Iterator[str]:
    from alembic import command

    assert ADMIN_URL is not None
    name = f"asc_{uuid.uuid4().hex[:12]}"
    asyncio.run(_execute(ADMIN_URL, f'CREATE DATABASE "{name}"'))
    try:
        command.upgrade(_alembic_cfg(name), "head")
        asyncio.run(_execute(_db_url(name), SEED_SQL))
        yield name
    finally:
        asyncio.run(_execute(ADMIN_URL, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


async def _analytics(dbname: str, route_id: int) -> dict[str, Any]:
    engine = create_async_engine(_db_url(dbname).replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine) as session:
            return await get_ascent_analytics(route_id, db=session)
    finally:
        await engine.dispose()


def test_accidents_count_by_mp_route_id_not_legacy_route_id(seeded_db):
    data = asyncio.run(_analytics(seeded_db, ROUTE))

    assert data["total_ascents"] == 3
    assert data["total_accidents"] == 3
    months = {m["month"]: m for m in data["monthly_stats"]}
    assert months["Jan"]["ascent_count"] == 2
    assert months["Jan"]["accident_count"] == 0
    assert months["Jul"]["accident_count"] == 1
    assert months["Mar"]["accident_count"] == 1


def test_accident_linked_by_mp_route_id_counts_on_that_route(seeded_db):
    data = asyncio.run(_analytics(seeded_db, OTHER_ROUTE))

    assert data["total_accidents"] == 1
    assert {m["month"]: m for m in data["monthly_stats"]}["Jan"]["accident_count"] == 1
    assert data["has_data"] is False


def test_seed_really_has_the_colliding_legacy_link(seeded_db):
    async def count() -> int:
        conn = await asyncpg.connect(_db_url(seeded_db))
        try:
            return int(await conn.fetchval(f"SELECT count(*) FROM accidents WHERE route_id = {ROUTE}"))
        finally:
            await conn.close()

    assert asyncio.run(count()) == 1


def test_response_carries_counts_only_no_rate(seeded_db):
    data = asyncio.run(_analytics(seeded_db, ROUTE))

    rate_keys = {"overall_accident_rate", "best_month", "worst_month"}
    assert rate_keys.isdisjoint(data)
    assert all(set(m) == {"month", "month_num", "ascent_count", "accident_count"} for m in data["monthly_stats"])
    assert data["peak_month"] == "Jan"


def test_future_dated_ticks_are_excluded_from_every_count(seeded_db):
    data = asyncio.run(_analytics(seeded_db, ROUTE))

    assert data["total_ascents"] == 3
    assert {m["month"]: m for m in data["monthly_stats"]}["Jan"]["ascent_count"] == 2
    assert sum(m["ascent_count"] for m in data["monthly_stats"]) == 3


def test_reports_the_year_span_of_each_side(seeded_db):
    data = asyncio.run(_analytics(seeded_db, ROUTE))
    assert data["accident_years"] == {"first": 2008, "last": 2019}
    # The 3901 tick is excluded here too.
    assert data["ascent_years"] == {"first": 2025, "last": 2025}


def test_year_span_is_null_when_a_side_is_empty(seeded_db):
    data = asyncio.run(_analytics(seeded_db, OTHER_ROUTE))
    assert data["accident_years"] == {"first": 2020, "last": 2020}
    assert data["ascent_years"] is None
