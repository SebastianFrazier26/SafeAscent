"""Read-only connection for -m db acceptance checks. VERIFY_DATABASE_URL is the analyst URL as
write_role_url wrote it (any driver prefix or ssl query is fine): the DSN is rebuilt without its
query and TLS comes from app.db.ssl.connect_args_for, i.e. verify-full against Neon."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.db.ssl import connect_args_for

URL = os.environ.get("VERIFY_DATABASE_URL")


def fetch(sql: str) -> list[asyncpg.Record]:
    if not URL:
        pytest.skip("VERIFY_DATABASE_URL not set")
    dsn = make_url(URL).set(drivername="postgresql", query={}).render_as_string(hide_password=False)
    connect_args = connect_args_for(URL)

    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(dsn, **connect_args)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    return asyncio.run(go())
