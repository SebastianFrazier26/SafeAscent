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

from httpx import ASGITransport, AsyncClient

from app.api.v1.mp_routes import get_ascent_analytics
from app.db.session import get_db
from app.main import app
from tests.test_migrations import ADMIN_URL, _alembic_cfg, _db_url, _execute

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="MIGRATIONS_TEST_ADMIN_URL not set")

ROUTE = 111
OTHER_ROUTE = 222
THIRD_ROUTE = 333
FOURTH_ROUTE = 444

# Accident 1 is the mislink the old join counted: legacy route_id collides with this
# route's MP id while mp_route_id points at another route.
SEED_SQL = f"""
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (10, 'Fixture Crag', 40.0, -105.0);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
    ({ROUTE}, 'Fixture Route', 10, 'Trad'),
    ({OTHER_ROUTE}, 'Other Route', 10, 'Trad'),
    ({THIRD_ROUTE}, 'Third Route', 10, 'Trad'),
    ({FOURTH_ROUTE}, 'Fourth Route', 10, 'Trad');
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
    (5, '3901-01-20', NULL, {ROUTE}),
    (3, NULL, NULL, {ROUTE});
-- Nearby accidents for the route-accidents endpoint (Third Route, ~1 km away).
INSERT INTO accidents (accident_id, date, route, mp_route_id, latitude, longitude) VALUES
    (11, '2015-05-01', 'Unrelated Name', {THIRD_ROUTE}, 40.005, -105.0),
    (12, '2016-05-01', 'Third Route Direct', {FOURTH_ROUTE}, 40.006, -105.0),
    (13, '2017-05-01', 'Third Route', NULL, 40.007, -105.0),
    (14, '3901-05-01', 'Third Route', {THIRD_ROUTE}, 40.008, -105.0),
    (15, '2018-05-01', 'Third Route', {THIRD_ROUTE}, NULL, NULL),
    (16, '2018-06-01', 'Third Route', NULL, NULL, NULL);
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


def test_future_dated_accidents_are_excluded_from_ascent_analytics(seeded_db):
    data = asyncio.run(_analytics(seeded_db, ROUTE))

    # Accident 5 (3901-01-20) would otherwise add to the total, January and the span.
    assert data["total_accidents"] == 3
    assert {m["month"]: m for m in data["monthly_stats"]}["Jan"]["accident_count"] == 0
    assert data["accident_years"]["last"] == 2019


async def _get(dbname: str, path: str, expect: int = 200, **params: Any) -> Any:
    engine = create_async_engine(_db_url(dbname).replace("postgresql://", "postgresql+asyncpg://", 1))

    async def override() -> Any:
        async with AsyncSession(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(path, params=params)
        assert response.status_code == expect, response.text
        return response.json()
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.fixture
def no_weather_calls(monkeypatch):
    import requests

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr(requests, "get", refuse)


def test_same_route_is_fk_equality_not_name_matching(seeded_db, no_weather_calls):
    data = asyncio.run(_get(seeded_db, f"/api/v1/mp-routes/{THIRD_ROUTE}/accidents"))

    same = {a["accident_id"]: a["same_route"] for a in data["accidents"]}
    assert same[11] is True  # FK match, unrelated name
    assert same[12] is False  # similar name, different mp_route_id
    assert same[13] is False  # exact name, NULL mp_route_id


def test_route_accidents_exclude_future_dates(seeded_db, no_weather_calls):
    data = asyncio.run(_get(seeded_db, f"/api/v1/mp-routes/{THIRD_ROUTE}/accidents"))

    assert 14 not in {a["accident_id"] for a in data["accidents"]}


def test_accident_list_filters_by_mp_route_id_not_legacy_route_id(seeded_db):
    data = asyncio.run(_get(seeded_db, "/api/v1/accidents", mp_route_id=ROUTE))

    ids = {a["accident_id"] for a in data["data"]}
    assert 1 not in ids  # legacy route_id == ROUTE, mp_route_id elsewhere
    assert {2, 3, 4} <= ids
    assert all(a["mp_route_id"] == ROUTE for a in data["data"])


def test_fk_linked_accident_without_coordinates_is_listed_first(seeded_db, no_weather_calls):
    data = asyncio.run(_get(seeded_db, f"/api/v1/mp-routes/{THIRD_ROUTE}/accidents"))

    by_id = {a["accident_id"]: a for a in data["accidents"]}
    assert by_id[15]["same_route"] is True
    assert by_id[15]["distance_km"] is None
    assert by_id[15]["impact_score"] is None
    assert by_id[15]["coordinates"] is None
    assert 16 not in by_id  # no FK and no coordinates: not nearby, not this route
    # Same-route rows lead, so the limit never cuts one off in favour of a nearby row.
    assert [a["same_route"] for a in data["accidents"]] == [True, True, False, False]
    limited = asyncio.run(_get(seeded_db, f"/api/v1/mp-routes/{THIRD_ROUTE}/accidents", limit=1))
    assert limited["accidents"][0]["same_route"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"route_id": ROUTE},
        {"lat": 40.0},
        {"lon": -105.0},
        {"radius_km": 10},
        {"lat": 40.0, "radius_km": 10},
        {"lat": 40.0, "lon": -105.0},
    ],
)
def test_accident_list_rejects_ignored_filters(seeded_db, params):
    body = asyncio.run(_get(seeded_db, "/api/v1/accidents", expect=422, **params))
    assert "mp_route_id" in body["detail"] if "route_id" in params else "lat, lon and radius_km" in body["detail"]


def test_accident_list_spatial_search_with_all_three_params(seeded_db):
    data = asyncio.run(_get(seeded_db, "/api/v1/accidents", lat=40.0, lon=-105.0, radius_km=5))
    assert {11, 12, 13} <= {a["accident_id"] for a in data["data"]}
    assert not {1, 2, 3, 15} & {a["accident_id"] for a in data["data"]}
