import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
# fileConfig disables every logger that already exists; callers running Alembic inside
# another process (the test suite) opt out so app.* loggers keep working.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("MIGRATOR_DATABASE_URL")
    if not url:
        raise RuntimeError("Set MIGRATOR_DATABASE_URL (the migrator role) to run migrations")
    return url


# app.db.session builds its engine from Settings, which requires DATABASE_URL. Migrations
# must not depend on the app's runtime credentials, so reuse the migrator URL here.
os.environ.setdefault("DATABASE_URL", _database_url())

import app.models  # noqa: E402,F401  (populates Base.metadata)
from app.db.session import Base  # noqa: E402
from app.db.ssl import connect_args_for  # noqa: E402
from app.models.legacy import UNMANAGED_LEGACY_TABLES  # noqa: E402

target_metadata = Base.metadata


def include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    if type_ == "table":
        if name in UNMANAGED_LEGACY_TABLES:
            return False
        if reflected and compare_to is None:
            # Live tables with no model (e.g. historical_predictions) are written by raw
            # SQL; autogenerate must never propose dropping them.
            return False
    return bool(alembic_helpers.include_object(obj, name, type_, reflected, compare_to))


MANAGED_SCHEMAS = frozenset({"public", "internal"})


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    # include_schemas reflects every schema; PostGIS's tiger/topology must never be diffed.
    if type_ == "schema":
        return name is None or name in MANAGED_SCHEMAS
    return True


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        include_schemas=True,
        include_name=include_name,
        render_item=alembic_helpers.render_item,
        process_revision_directives=alembic_helpers.writer,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _database_url()
    connectable = async_engine_from_config(
        {"sqlalchemy.url": url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args_for(url),
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
