"""Drop the legacy public.weather table (Phase 3 MVP-1 hard gate).

Contract migration: the Phase 2 nightly and /predict read this table, so it runs on prod only after
the deploy that stopped reading it (runbook gate, Task 26). A non-empty table drops only behind an
r6_verify run in source_ingest_log with status 'ok' whose logged metrics meet the R6 agreement
thresholds (n > 100, r_tmax > 0.95, p99_gust < 60); a missing or unparseable metric does not
qualify. An empty table (a fresh database) drops freely. Rollback is the pre-p3-4 snapshot branch,
not a downgrade."""

import sqlalchemy as sa
from alembic import op

revision = "0013_drop_legacy_weather"
down_revision = "0012_model_registry"
branch_labels = None
depends_on = None

# The metrics are jsonb numbers beside the counts in validation_report (ingest_log._validated_metrics);
# jsonb_typeof guards the casts so a string or null metric fails closed instead of erroring.
QUALIFYING_R6 = """
SELECT count(*) FROM source_ingest_log
WHERE source = 'r6_verify' AND status = 'ok'
  AND jsonb_typeof(validation_report->'n') = 'number' AND (validation_report->>'n')::numeric > 100
  AND jsonb_typeof(validation_report->'r_tmax') = 'number' AND (validation_report->>'r_tmax')::numeric > 0.95
  AND jsonb_typeof(validation_report->'p99_gust') = 'number' AND (validation_report->>'p99_gust')::numeric < 60
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM public.weather)")).scalar_one():
        if bind.execute(sa.text(QUALIFYING_R6)).scalar_one() == 0:
            raise RuntimeError("refusing to drop weather: no ok r6_verify run meeting the agreement thresholds")
    op.execute("DROP TABLE public.weather")


def downgrade() -> None:
    raise NotImplementedError("0013 is one-way: restore weather from the pre-p3-4 snapshot branch")
