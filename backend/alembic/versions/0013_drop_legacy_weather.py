"""Drop the legacy public.weather table (Phase 3 MVP-1 hard gate).

Contract migration: the Phase 2 nightly and /predict read this table, so it runs on prod only after
the deploy that stopped reading it (runbook gate, Task 26). Rollback is the pre-p3-4 snapshot
branch, not a downgrade."""

import sqlalchemy as sa
from alembic import op

revision = "0013_drop_legacy_weather"
down_revision = "0012_model_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ok_runs = op.get_bind().execute(
        sa.text("SELECT count(*) FROM source_ingest_log WHERE source = 'r6_verify' AND status = 'ok'")
    ).scalar_one()
    if ok_runs == 0:
        raise RuntimeError("refusing to drop weather: no ok r6_verify run in source_ingest_log")
    op.execute("DROP TABLE public.weather")


def downgrade() -> None:
    raise NotImplementedError("0013 is one-way: restore weather from the pre-p3-4 snapshot branch")
