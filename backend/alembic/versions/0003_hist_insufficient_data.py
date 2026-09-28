"""historical_predictions stores insufficient_data days as risk_score NULL + color_code 'gray'.

Owner decision 2026-09-28: a route with no contributing evidence is stored explicitly as
insufficient, never skipped and never 0. The CHECK pairs the two columns so a NULL score
can only mean gray and gray can only mean a NULL score. NOT VALID: it guards every new
write without scanning (or failing on) rows written before this change.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_hist_insufficient_data"
down_revision = "0002_drop_ascents_climbers"
branch_labels = None
depends_on = None

CONSTRAINT = "historical_predictions_score_status_check"


def upgrade() -> None:
    op.alter_column("historical_predictions", "risk_score", nullable=True)
    op.execute(
        f"ALTER TABLE historical_predictions ADD CONSTRAINT {CONSTRAINT} "
        "CHECK ((risk_score IS NULL) = (color_code = 'gray')) NOT VALID"
    )


def downgrade() -> None:
    # Same convention as 0002: refuse rather than delete data. Insufficient days have no score
    # to restore, so an operator must decide what happens to them before NOT NULL returns.
    bind = op.get_bind()
    nulls = bind.execute(
        sa.text("SELECT count(*) FROM historical_predictions WHERE risk_score IS NULL")
    ).scalar_one()
    if nulls != 0:
        raise RuntimeError(
            f"refusing to downgrade 0003: {nulls} historical_predictions rows have a NULL risk_score"
        )
    op.drop_constraint(CONSTRAINT, "historical_predictions", type_="check")
    op.alter_column("historical_predictions", "risk_score", nullable=False)
