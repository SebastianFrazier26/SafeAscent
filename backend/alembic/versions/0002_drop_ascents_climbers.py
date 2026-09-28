"""Drop ascents and climbers (D8 stage 1); both held 0 rows in the 2026-09-27 audit."""

import sqlalchemy as sa
from alembic import op

revision = "0002_drop_ascents_climbers"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# ascents references climbers, so it is dropped first.
TABLES_TO_DROP = ("ascents", "climbers")


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES_TO_DROP:
        rows = bind.execute(sa.select(sa.func.count()).select_from(sa.table(name))).scalar_one()
        if rows != 0:
            raise RuntimeError(f"refusing to drop {name}: {rows} rows present (audit expected 0)")
    for name in TABLES_TO_DROP:
        op.drop_table(name)


def downgrade() -> None:
    raise NotImplementedError("0002 is one-way: the dropped tables were empty and their models are gone")
