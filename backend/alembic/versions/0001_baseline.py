"""Baseline: the live Neon schema as of 2026-09-27.

Prod is stamped at this revision, never upgraded through it. The SQL beside this file
is a sanitized `pg_dump --schema-only -n public` (scripts/sanitize_schema_dump.py), so
the database, not the models, is the source of truth here.

Offline `--sql` mode is unsupported for this revision (it needs a live connection);
`alembic stamp 0001_baseline --sql` works.
"""

from pathlib import Path

from alembic import context, op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_SQL = Path(__file__).with_name("0001_baseline.sql")
EXTENSIONS = ("postgis",)


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0001_baseline cannot be rendered with --sql; use `alembic stamp 0001_baseline --sql`")
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT to_regclass('public.accidents')").scalar() is not None:
        raise RuntimeError(
            "Database already has the schema (public.accidents exists); run "
            "`alembic stamp 0001_baseline` instead of upgrading through the baseline"
        )
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
    sql = BASELINE_SQL.read_text(encoding="utf-8")
    # asyncpg prepares every statement it is handed; a multi-statement dump only runs
    # through the raw driver's simple-query path.
    bind.connection.dbapi_connection.run_async(lambda conn: conn.execute(sql))


def downgrade() -> None:
    raise NotImplementedError("0001_baseline is the root revision; drop the database instead")
