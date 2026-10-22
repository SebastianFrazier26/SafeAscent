import asyncio
import inspect
import re
from pathlib import Path

import asyncpg
from pydantic import BaseModel

import app.api.v1 as api_pkg
from app.schemas import mp_location, mp_route, prediction
from tests.pgtest import migrated_db, pg_url, requires_pg

FORBIDDEN = {"description", "beta", "protection", "comment", "comments", "photos", "photo", "stars", "user",
             "username", "climber_name", "location_description", "notes", "text"}
CATALOG_TABLES = ("mp_routes", "mp_locations", "canonical_routes", "canonical_areas", "scorable_routes")


def _names(field_name: str, info: object) -> set[str]:
    names = {field_name}
    for attr in ("alias", "validation_alias", "serialization_alias"):
        value = getattr(info, attr, None)
        if isinstance(value, str):
            names.add(value)
    return {n.lower() for n in names}


def test_no_api_schema_exposes_an_mp_prose_field():
    offenders = []
    for module in (mp_location, mp_route, prediction):
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == module.__name__:
                offenders += [f"{name}.{f}" for f, info in cls.model_fields.items() if _names(f, info) & FORBIDDEN]
    assert offenders == []


def test_api_sql_never_selects_prose_from_mp_or_catalog_tables():
    select = re.compile(r"SELECT\s(.*?)\sFROM\s+(\w+)", re.IGNORECASE | re.DOTALL)
    offenders = []
    for path in Path(api_pkg.__file__).parent.glob("*.py"):
        for columns, table in select.findall(path.read_text()):
            if table.lower() in CATALOG_TABLES:
                words = {w.lower() for w in re.findall(r"[A-Za-z_]+", columns)}
                offenders += [f"{path.name}: {table}.{w}" for w in sorted(words & FORBIDDEN)]
    assert offenders == []


@requires_pg
def test_mp_and_catalog_tables_have_no_prose_columns():
    async def columns(url: str) -> list[tuple[str, str]]:
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY($1::text[])", list(CATALOG_TABLES))
            return [(r["table_name"], r["column_name"]) for r in rows]
        finally:
            await conn.close()

    with migrated_db() as name:
        found = asyncio.run(columns(pg_url(name)))
    assert {t for t, _ in found} == set(CATALOG_TABLES)
    assert [f"{t}.{c}" for t, c in found if c.lower() in FORBIDDEN] == []
