# Phase 2b Exposure v1 and Prediction Compaction (PRs 2b-4, 2b-5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (2b-4) Build `exposure_index` v1: per area × type group × month, the composite proxy of partial climbing-traffic signals (route density, season share, NPS visits, permits, objective popularity, OpenBeta/in-app ticks as missing, and the optional, removable internal MP tick components), each stored raw with a missing flag, computed **as of a data cutoff** so Phase 3 can build leak-free training snapshots, plus an unfitted exposure with ×/÷3 (×/÷1.5 with permits) bounds and a Spearman validation report. (2b-5) Compact `historical_predictions` from ~3.37 GB to a monthly score-array archive without changing the nightly cadence or any displayed band, verified row-for-row, with one `prediction_history` view that the trends endpoint and the Phase 3 backtest both read.

**Architecture:** `app/pipelines/exposure.py` holds pure component math (season-share rules, Spearman, bounds, proxy assembly) and one set-based job per component, assembled into a new snapshot keyed by `(as_of, data_cutoff)`; every component reads only data dated before `data_cutoff`. Production snapshots (`data_cutoff = as_of`) keep the newest three; backtest snapshots (`data_cutoff < as_of`) are kept until dropped explicitly. MP components run only when `EXPOSURE_ENABLE_MP_TICKS` is true and are missing-flagged otherwise, which is the removal drill. `exposure_index` is revoked from `app` (it holds MP-derived covariates). `app/pipelines/prediction_archive.py` folds rows older than 7 days into `prediction_archive_v1(route_id, month_start, scores smallint[31])` inside one transaction per month, storing `round(score × 10)` (one decimal, exactly what the app displays), `-1` for an insufficient (gray) day and NULL for no prediction, verifying every folded row before deleting it `[assumes D18]`. The archive keeps 400 days; the nightly writer's own 1-year purge is removed so nothing is deleted unfolded.

**Tech Stack:** Python 3.12, numpy, SQLAlchemy async, Alembic, PostGIS/ltree, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Exposure (P2-9), §MP ice/mixed tick aggregates (P2-14), §Storage (P2-8), milestones 2b-4, 2b-5; P3:152 (`exposure_index` shape), P3:397 (train/test split), P3 M5. Decisions D2, D13, D17, D18 in `2026-09-28-phase2a-foundations.md`.

**Prerequisites (merge order is linear; `0014`'s `down_revision` is `0013_live_feeds`):** plans 1–7 merged and applied in order. Content this plan reads: plan 4 (catalog, `internal.mp_route_links`, `scorable_routes`), plan 5 (`feature_points` with `tz`, `canonical_areas.point_key`), plan 6 (`objectives` incl. `sitelinks`/`disciplines`, `objective_permit_counts`), plan 7 (chain head `0013`). The 3-year ERA5 daily history (`cell_daily_conditions.record_kind = 'era5'`) comes from plan 3's first Professional window and each January batch (`era5_window`), not from a weekly append (there is none). Plan 1's tick aggregates loaded.

**Two PRs:** 2b-4 = Tasks 1–6 + runbook Task 10 (`feat/p2b-exposure`); 2b-5 = Tasks 7–9 + runbook Task 11 (`feat/p2b-prediction-compaction`).

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- `exposure_index` is readable only by `ingest` (and, from Phase 3, `trainer`): `app` has no privilege on it (0014 revokes the default-privilege SELECT; `-m db` and migration tests check it). No API endpoint reads it (a test greps `app/api/`).
- Every component is stored raw with `missing: true|false` and, when missing, a `reason`; nothing missing is written as 0, and no floor or default stands in for a missing input. A measured zero (an observed month with no ticks, a season with no climbable days) is stored as 0 and is distinguishable from missing.
- Every component reads only data dated strictly before `data_cutoff` (X4); `route_density` (current catalog) and `objective_popularity` (current Wikidata sitelink count) have no history and are documented as current-state covariates.
- MP components (`mp_tick_count`, `mp_ice_mixed_ticks`) are internal-only covariates, computed only when `EXPOSURE_ENABLE_MP_TICKS=true` (default true); with the flag false, or the tables dropped, they are missing-flagged and everything else is unchanged.
- `ob_ticks` is missing everywhere with reason `no_bulk_source` `[assumes D17]`; plan 4's one-time OpenBeta tick-coverage sample and its revisit trigger decide whether P2-9's OpenBeta-tick component comes back.
- The validation Spearman correlations are reported, never tuned to.
- Compaction never loses a prediction and never changes a displayed band: a month is deleted from `historical_predictions` only after every one of its rows is verified in the archive in the same transaction; the archive stores one decimal (the app's display precision); `app` stays SELECT-only except `historical_predictions`.
- Cadence (one table in the foundations plan, D8): exposure monthly (`data-weekly.yml` monthly job, after the lightning steps; the February run is the first to see the new January ERA5 year); fold daily (`data-daily.yml`, 09:10 UTC); `VACUUM FULL` once, by the owner, outside 01:00–10:00 UTC.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **Scope of rows:** areas with at least one scorable route attached directly (crags) plus every objective's area — including objectives with no scorable routes yet, which get rows for their disciplines (`objectives.disciplines ∩` the five type groups; a peak/glacier/formation with none of them → `alpine`, an `ice_area` → `ice`) with `route_density` missing `no_catalog_routes` rather than being dropped; 12 months.
2. **Season share rules** (from ERA5 rows only — `record_kind = 'era5'`, never stopgap or forecast — of the area's `(grid_bucket, tz)` series in the 3 years before `data_cutoff`, averaged per calendar month): rock (sport/trad) day climbable when tmax 5–32 °C and precip < 2 mm; ice/mixed when tmax < 0 °C on ≥7 of the prior 10 days; alpine when either window holds (the spec says "both snow and dry-day windows"; read as the union of the two).
3. **Unfitted exposure point** for the bounds: `route_density × season_share`, NULL when either is missing (no 0.05 floor: a floor would invent exposure where the season is unknown, which lowers per-exposure risk). Phase 3 fits the real combination (M5); this number only anchors `exposure_lo`/`exposure_hi`.
4. **NPS visits:** a hand-curated list of climbing parks (`data/curated/nps_units.csv`: unit code, centre, radius) and monthly recreation visits entered from NPS IRMA Stats (`data/curated/nps_visits.csv`, public domain), attached to areas within the park radius, month-of-year mean over available years before the cutoff.
5. **Permits:** only month-specific rows (`month` 1–12) feed a month; a whole-year total (`month = 0`) is never spread over months — a month with only annual totals is missing `annual_only`.
6. **MP rock ticks:** counted per canonical type group through route-level links (`internal.mp_route_links` → `canonical_routes.type_group`), so sport and trad never share a count; an area is in the tick sample only if it lies in CA/NV **and** at least one of its linked sport/trad routes has a clean tick before the cutoff — otherwise missing (`outside_ca_nv`, `not_in_tick_sample`), never `present(0)`. Inside the sample a month with no ticks is a measured 0.
7. **MP ice/mixed ticks:** mean per observed year, where the observed years for month *m* are every year from the area's first to last aggregate year whose month *m* is not after the export's last complete month; a year with no row for that month counts as 0 (the export lists only non-zero months).
8. **Snapshots:** exposure is recomputed monthly (and on demand, and with `--cutoff` for Phase 3 backtests); only the three most recent production snapshots are kept (storage).
9. **Archive encoding:** `scores[day]` holds `round(risk_score × 10)` (0–1000, half away from zero — Postgres `numeric` rounding), `-1` an insufficient (gray) day (NULL score, or a score below `MIN_ESTIMABLE_SCORE`), NULL no prediction; archived days decode to `v / 10` and go through the same `displayed_risk` as live rows, so bands are unchanged (24.5 stays green; 0.05–0.49 stays a number).
10. **Retention:** archive months are kept 400 days (spec) and pruned by the daily fold; the nightly writer's own 1-year `DELETE` is removed, so the fold is the only deleter of `historical_predictions`.

## Review Focus

1. **Exposure recomputed with `EXPOSURE_ENABLE_MP_TICKS=false`** — expect both MP keys present with `missing: true, reason: "disabled"` and every other component identical to the enabled run at the same cutoff (Task 5 `test_removal_drill_changes_only_mp_components`).
2. **A route whose MP ticks hit the 16-per-route cap** — expect the component flagged `censored: true`, not treated as an exact count, and the sibling sport type group in the same area not given the trad count (Task 3 `test_capped_routes_are_flagged_censored_and_types_not_shared`).
3. **An area with no climate history, or an objective with no scorable routes** — expect the season or density component missing and `exposure_point`/bounds NULL, never a 0.05-floored number (Task 4 `test_missing_season_gives_null_point_not_a_floor`).
4. **A tick or NPS month after `data_cutoff`** — expect it excluded from the snapshot (Task 3 `test_cutoff_excludes_later_data`).
5. **Folding a month with scores 24.5, 0.3 and 0.04** — expect decoded 24.5 green, 0.3 shown as 0.3 (not insufficient), 0.04 gray — exactly `displayed_risk` of the raw score (Task 8 `test_fold_preserves_displayed_band_and_precision`).
6. **A fold interrupted after insert but before delete** — expect a rerun to finish without duplicating or losing rows (Task 8 `test_rerun_after_partial_fold_is_idempotent`).
7. **The nightly writer with a row older than a year in `historical_predictions`** — expect the row untouched (Task 8 `test_nightly_writer_no_longer_purges`).
8. **The trends endpoint for a window spanning archive and recent rows** — expect one entry per day, recent rows winning on overlap, with `today` injected (Task 9 `test_trends_merge_archive_and_recent_without_duplicates`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0014_exposure.py` | Create | `exposure_index` (with `data_cutoff`), `nps_units`, `nps_monthly_visits`; REVOKE `app` on `exposure_index`. |
| `backend/alembic/versions/0015_prediction_archive.py` | Create | `prediction_archive_v1`, view `prediction_history`. |
| `backend/app/models/exposure.py` | Create | Models. |
| `backend/app/config.py`, `.env.example` | Modify | `EXPOSURE_ENABLE_MP_TICKS`. |
| `backend/app/pipelines/exposure.py` | Create | Components, assembly, snapshot job, validation, CLI. |
| `backend/app/pipelines/curated.py` | Modify | `nps_units` / `nps_visits` loaders. |
| `backend/app/pipelines/prediction_archive.py` | Create | Fold, verify, delete, prune; CLI. |
| `backend/app/tasks/safety_computation_optimized.py` | Modify | Remove the nightly writer's 1-year purge. |
| `backend/app/api/v1/mp_routes.py` | Modify | Trends read `prediction_history` with an injected UTC today. |
| `data/curated/nps_units.csv`, `data/curated/nps_visits.csv` | Create | Headers (owner fills). |
| tests: `test_migration_0014.py`, `test_exposure.py`, `test_exposure_job.py`, `test_no_proxy_in_api.py`, `test_migration_0015.py`, `test_prediction_archive.py`, `test_historical_trends_archive.py` | Create | Tests. |
| `backend/tests/verify/test_phase2b_exposure_storage.py` | Create | `-m db` acceptance incl. the `app` privilege check. |
| `.github/workflows/data-weekly.yml`, `data-daily.yml` | Modify | Monthly exposure; daily fold. |
| `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql`, `backend/pyproject.toml`, docs | Modify | Grants, revoke, mypy, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–6 | `exposure_index` (PK incl. `data_cutoff`), NPS tables (0014) | Task 1 first. |
| 2 | 3–5 | `season_share`, `spearman`, `bounds`, `exposure_point`, `Component`, `PROXY_VERSION` | Pure; frozen in Task 2. |
| 3 | 4, 5 | `scope(conn)`, component functions all take `cutoff: date` | Frozen in Task 3. |
| 4 | 5, 10 | `build(conn, *, today, cutoff, mp_enabled)`, `validate(conn, *, as_of, data_cutoff)` | Frozen in Task 4. |
| 6 | plan 6 | `curated.py` gains NPS loaders | Additive functions + CLI choices. |
| 7 | 8, 9, Phase 3 backtest | `prediction_archive_v1`, view `prediction_history` (0015) | Task 7 first; Phase 3 reads the view, never the raw tables. |
| 8 | 9 | `ARCHIVE_GRAY = -1`, `SCORE_SCALE = 10`, `FOLD_AFTER_DAYS = 7`, `ARCHIVE_RETENTION_DAYS = 400` | Frozen in Task 8. |
| 8 | nightly job | `safety_computation_optimized._save_to_historical` purge block | Removed in Task 8, test pins it. |
| 9 | existing trends tests | `app/api/v1/mp_routes.py` historical query | Task 9 keeps the response shape; new test covers the merge. |
| 1, 7 | each other, plans 1–7 | grants/verify SQL; `verify_roles.sql` (app SELECT-only) unchanged | Append-only; `trainer` gets nothing here (D13). |

---

# PR 2b-4 — `feat/p2b-exposure`

### Task 1: Migration `0014`, models, setting

**Files:**
- Create: `backend/alembic/versions/0014_exposure.py`, `backend/app/models/exposure.py`, `backend/tests/test_migration_0014.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/config.py`, `.env.example`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Consumes: `0013_live_feeds` (down revision).
- Produces (DB): `exposure_index(area_id uuid REFERENCES canonical_areas, type_group text CHECK IN ('sport','trad','alpine','ice','mixed'), month smallint CHECK 1..12, n_routes integer NOT NULL CHECK >= 0, proxy jsonb NOT NULL, proxy_version text NOT NULL, as_of date NOT NULL, data_cutoff date NOT NULL CHECK (data_cutoff <= as_of), exposure_point real, exposure_lo real, exposure_hi real, CHECK ((exposure_point IS NULL) = (exposure_lo IS NULL) AND (exposure_lo IS NULL) = (exposure_hi IS NULL)), PK (area_id, type_group, month, as_of, data_cutoff))`; `app` holds no privilege on it. `nps_units(unit_code text PK, name text NOT NULL, lat double precision NOT NULL, lon double precision NOT NULL, radius_km real NOT NULL CHECK > 0)`; `nps_monthly_visits(unit_code text REFERENCES nps_units, year smallint, month smallint CHECK 1..12, visits integer NOT NULL CHECK >= 0, source_url text NOT NULL, PK (unit_code, year, month))`.
- Produces (Python): `Settings.EXPOSURE_ENABLE_MP_TICKS: bool = True`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0014.py`:

```python
import asyncio

import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

AREA = "00000000-0000-0000-0000-00000000000a"
AREA_SQL = (
    f"INSERT INTO canonical_areas (area_id, name, path, coord_precision, source, redistributable) "
    f"VALUES ('{AREA}', 'Fixture Crag', '0000000000000000000000000000000a', 'none', 'openbeta', true);"
)


def _row(**over: str) -> str:
    cols = {"area_id": f"'{AREA}'", "type_group": "'trad'", "month": "7", "n_routes": "1", "proxy": "'{}'",
            "proxy_version": "'x-v1'", "as_of": "'2026-09-28'", "data_cutoff": "'2026-09-28'",
            "exposure_point": "NULL", "exposure_lo": "NULL", "exposure_hi": "NULL"}
    cols.update(over)
    return f"INSERT INTO exposure_index ({', '.join(cols)}) VALUES ({', '.join(cols.values())})"


def test_0014_checks():
    with migrated_db(seed_sql=AREA_SQL) as name:
        command.check(_alembic_cfg(name))
        run_sql(name, "INSERT INTO nps_units VALUES ('YOSE', 'Yosemite', 37.75, -119.6, 25)")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO nps_monthly_visits VALUES ('YOSE', 2025, 13, 1, 'https://irma.nps.gov/Stats/')")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO nps_monthly_visits VALUES ('YOSE', 2025, 7, -1, 'https://irma.nps.gov/Stats/')")
        run_sql(name, _row())
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, _row(type_group="'boulder'", month="8"))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, _row(data_cutoff="'2026-09-29'", month="9"))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, _row(exposure_point="1.0", month="10"))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, _row(n_routes="-1", month="11"))


def test_0014_revokes_default_privilege_select_from_app():
    """Default privileges give app SELECT on every new public table; 0014 must take it back (SEC5)."""
    with migrated_db(revision="0013_live_feeds") as name:
        run_sql(name, "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app') "
                      "THEN CREATE ROLE app NOLOGIN; END IF; END $$; "
                      "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app;")
        command.upgrade(_alembic_cfg(name), "0014_exposure")

        async def privs() -> tuple[bool, bool]:
            conn = await asyncpg.connect(pg_url(name))
            try:
                row = await conn.fetchrow(
                    "SELECT has_table_privilege('app', 'public.exposure_index', 'SELECT') AS exposure, "
                    "has_table_privilege('app', 'public.nps_units', 'SELECT') AS nps")
                return bool(row["exposure"]), bool(row["nps"])
            finally:
                await conn.close()

        exposure, nps = asyncio.run(privs())
    assert exposure is False
    assert nps is True
```

- [ ] **Step 2: Implement** `backend/alembic/versions/0014_exposure.py`:

```python
"""Exposure v1 (P2-9): exposure_index (P3:152 + bounds + data cutoff) and the curated NPS
visitation tables. exposure_index carries MP-derived covariates, so the app role's
default-privilege SELECT is revoked here (D2/SEC5)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_exposure"
down_revision = "0013_live_feeds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exposure_index",
        sa.Column("area_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_areas.area_id"), nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("n_routes", sa.Integer(), nullable=False),
        sa.Column("proxy", postgresql.JSONB(), nullable=False),
        sa.Column("proxy_version", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("data_cutoff", sa.Date(), nullable=False),
        sa.Column("exposure_point", sa.REAL(), nullable=True),
        sa.Column("exposure_lo", sa.REAL(), nullable=True),
        sa.Column("exposure_hi", sa.REAL(), nullable=True),
        sa.PrimaryKeyConstraint("area_id", "type_group", "month", "as_of", "data_cutoff"),
        sa.CheckConstraint("type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed')", name="exposure_index_type_group_check"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="exposure_index_month_check"),
        sa.CheckConstraint("n_routes >= 0", name="exposure_index_n_routes_check"),
        sa.CheckConstraint("data_cutoff <= as_of", name="exposure_index_cutoff_check"),
        sa.CheckConstraint(
            "(exposure_point IS NULL) = (exposure_lo IS NULL) AND (exposure_lo IS NULL) = (exposure_hi IS NULL)",
            name="exposure_index_bounds_check"),
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app') "
        "THEN REVOKE ALL ON public.exposure_index FROM app; END IF; END $$")
    op.create_table(
        "nps_units",
        sa.Column("unit_code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("radius_km", sa.REAL(), nullable=False),
        sa.CheckConstraint("radius_km > 0", name="nps_units_radius_check"),
    )
    op.create_table(
        "nps_monthly_visits",
        sa.Column("unit_code", sa.Text(), sa.ForeignKey("nps_units.unit_code"), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("visits", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("unit_code", "year", "month"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="nps_monthly_visits_month_check"),
        sa.CheckConstraint("visits >= 0", name="nps_monthly_visits_visits_check"),
    )


def downgrade() -> None:
    op.drop_table("nps_monthly_visits")
    op.drop_table("nps_units")
    op.drop_table("exposure_index")
```

Models in `backend/app/models/exposure.py` (`ExposureIndex` incl. `data_cutoff`, `NpsUnit`, `NpsMonthlyVisits`) mirror it; register; models mypy block. `config.py`: `EXPOSURE_ENABLE_MP_TICKS: bool = True` with comment `# P2-9: false drops the internal MP tick components from exposure (the removal drill).`; `.env.example`: `EXPOSURE_ENABLE_MP_TICKS=true` (`test_env_example_parity.py` passes).

Grants ("Plan 8 (0014)") appended to `grants_phase2.sql`:

```sql
-- Plan 8 (0014)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.exposure_index TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.nps_units, public.nps_monthly_visits TO ingest;
-- exposure_index holds MP-derived covariates: a re-run of the Phase 1 grants (SELECT on all
-- public tables to app) must not re-open it, so this script revokes it every time.
REVOKE ALL ON public.exposure_index FROM app;
```

`ingest`'s SELECT on `internal.mp_tick_aggregates` (plan 1), `internal.mp_ticks`/`internal.mp_route_links` (plan 4), `feature_points` and `cell_daily_conditions` (plans 3, 5) is already granted; keep one copy each. `trainer` gets nothing here (D13: NOLOGIN, no grants until Phase 3). `verify_roles_phase2.sql`: `ingest_writes` rows `('public.exposure_index','INSERT'), ('public.exposure_index','UPDATE'), ('public.exposure_index','DELETE'), ('public.nps_units','INSERT'), ('public.nps_units','UPDATE'), ('public.nps_monthly_visits','INSERT'), ('public.nps_monthly_visits','UPDATE')`, and in its `app_denied` block the row `('public.exposure_index','SELECT')` (the check fails if `has_table_privilege('app', …)` is true).

- [ ] **Step 3: Run** — `cd backend && uv run pytest tests/test_migration_0014.py -q` → PASS. **Step 4: Commit** — `git add backend/alembic/versions/0014_exposure.py backend/app/models/ backend/app/config.py .env.example backend/tests/test_migration_0014.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0014 exposure_index (app revoked) and curated NPS visitation tables"`

---

### Task 2: Pure exposure math

**Files:**
- Create: `backend/app/pipelines/exposure.py` (pure part), `backend/tests/test_exposure.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `PROXY_VERSION = "x-v1"`, `COMPONENTS = ("route_density", "season_share", "ob_ticks", "nps_visits", "permit_attempts", "objective_popularity", "in_app_ticks", "mp_tick_count", "mp_ice_mixed_ticks")`, `MP_COMPONENTS = ("mp_tick_count", "mp_ice_mixed_ticks")`, `@dataclass(frozen=True) Component(value: float | None, missing: bool, reason: str | None = None, censored: bool = False)` with `present(v, *, censored=False)` and `absent(reason)` constructors and `to_json() -> dict[str, object]`, `rock_day(tmax, precip) -> bool | None`, `ice_day(prior10_tmax: Sequence[float | None]) -> bool | None`, `season_share(days: Sequence[tuple[date, float | None, float | None]], type_group: str) -> dict[int, float | None]` (month → share), `spearman(x: Sequence[float], y: Sequence[float]) -> float | None`, `bounds(point: float | None, *, has_permits: bool) -> tuple[float | None, float | None]`, `exposure_point(route_density: float | None, season: float | None) -> float | None`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_exposure.py`:

```python
from datetime import date, timedelta

import pytest

from app.pipelines.exposure import Component, bounds, exposure_point, ice_day, rock_day, season_share, spearman


def test_rock_and_ice_day_rules():
    assert rock_day(20.0, 0.0) is True and rock_day(40.0, 0.0) is False and rock_day(20.0, 5.0) is False
    assert rock_day(None, 0.0) is None
    assert ice_day([-5.0] * 7 + [2.0] * 3) is True and ice_day([-5.0] * 6 + [2.0] * 4) is False
    assert ice_day([None] * 10) is None


def _days(tmax: float, precip: float, n: int = 400):
    start = date(2024, 1, 1)
    return [(start + timedelta(d), tmax, precip) for d in range(n)]


def test_season_share_by_type_group():
    rock = season_share(_days(20.0, 0.0), "trad")
    assert rock[1] == 1.0 and rock[7] == 1.0
    ice = season_share(_days(-5.0, 0.0), "ice")
    assert ice[1] == 1.0
    alpine = season_share(_days(-5.0, 0.0), "alpine")
    assert alpine[1] == 1.0
    assert season_share([], "trad")[1] is None


def test_measured_zero_season_is_zero_not_missing():
    assert season_share(_days(40.0, 0.0), "sport")[7] == 0.0


def test_component_json_and_missing():
    assert Component.present(3.0).to_json() == {"value": 3.0, "missing": False}
    assert Component.present(0.0).to_json() == {"value": 0.0, "missing": False}
    assert Component.present(16.0, censored=True).to_json() == {"value": 16.0, "missing": False, "censored": True}
    assert Component.absent("disabled").to_json() == {"value": None, "missing": True, "reason": "disabled"}


def test_bounds_and_point_never_floor_a_missing_input():
    assert bounds(9.0, has_permits=False) == (3.0, 27.0)
    assert bounds(9.0, has_permits=True) == pytest.approx((6.0, 13.5))
    assert bounds(None, has_permits=False) == (None, None)
    assert exposure_point(10.0, 0.5) == 5.0
    assert exposure_point(10.0, 0.0) == 0.0
    assert exposure_point(10.0, None) is None
    assert exposure_point(None, 0.5) is None


def test_spearman_handles_ties_and_degenerate_input():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 2, 3], [3, 2, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
```

- [ ] **Step 2: Implement** `backend/app/pipelines/exposure.py` (pure part):

```python
"""Exposure v1 (P2-9): partial signals of climbing traffic per area × type group × month,
each stored raw with a missing flag; Phase 3 fits their weights (M5). The unfitted point
estimate here only anchors the ×/÷3 (×/÷1.5 with permits) bounds."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

PROXY_VERSION = "x-v1"
COMPONENTS = ("route_density", "season_share", "ob_ticks", "nps_visits", "permit_attempts",
              "objective_popularity", "in_app_ticks", "mp_tick_count", "mp_ice_mixed_ticks")
MP_COMPONENTS = ("mp_tick_count", "mp_ice_mixed_ticks")
ROCK_TMAX = (5.0, 32.0)
ROCK_MAX_PRECIP = 2.0
ICE_WINDOW, ICE_MIN_FROZEN = 10, 7


@dataclass(frozen=True)
class Component:
    value: float | None
    missing: bool
    reason: str | None = None
    censored: bool = False

    @classmethod
    def present(cls, value: float, *, censored: bool = False) -> Component:
        return cls(float(value), False, None, censored)

    @classmethod
    def absent(cls, reason: str) -> Component:
        return cls(None, True, reason)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"value": self.value, "missing": self.missing}
        if self.reason:
            out["reason"] = self.reason
        if self.censored:
            out["censored"] = True
        return out


def rock_day(tmax: float | None, precip: float | None) -> bool | None:
    if tmax is None or precip is None:
        return None
    return ROCK_TMAX[0] <= tmax <= ROCK_TMAX[1] and precip < ROCK_MAX_PRECIP


def ice_day(prior10_tmax: Sequence[float | None]) -> bool | None:
    known = [t for t in prior10_tmax if t is not None]
    if len(known) < ICE_MIN_FROZEN:
        return None
    return sum(1 for t in known if t < 0) >= ICE_MIN_FROZEN


def season_share(days: Sequence[tuple[date, float | None, float | None]], type_group: str) -> dict[int, float | None]:
    ordered = sorted(days)
    tmax_series = [t for _, t, _ in ordered]
    per_month: dict[int, list[bool]] = defaultdict(list)
    for i, (d, tmax, precip) in enumerate(ordered):
        rock = rock_day(tmax, precip)
        ice = ice_day(tmax_series[max(0, i - ICE_WINDOW + 1) : i + 1]) if i >= ICE_WINDOW - 1 else None
        if type_group in ("sport", "trad"):
            ok = rock
        elif type_group in ("ice", "mixed"):
            ok = ice
        else:
            ok = None if rock is None and ice is None else bool(rock) or bool(ice)
        if ok is not None:
            per_month[d.month].append(ok)
    return {m: (sum(per_month[m]) / len(per_month[m]) if per_month.get(m) else None) for m in range(1, 13)}


def _ranks(values: Sequence[float]) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    order = x.argsort(kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=np.float64)
    for v in np.unique(x):
        tied = x == v
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def exposure_point(route_density: float | None, season: float | None) -> float | None:
    # No floor on a missing or zero season: inventing exposure would lower per-exposure risk.
    if route_density is None or season is None:
        return None
    return route_density * season


def bounds(point: float | None, *, has_permits: bool) -> tuple[float | None, float | None]:
    if point is None or not math.isfinite(point):
        return None, None
    factor = 1.5 if has_permits else 3.0
    return point / factor, point * factor
```

Append `"app.pipelines.exposure"` to the strict mypy block in `backend/pyproject.toml`.

- [ ] **Step 3: Run** — `cd backend && uv run pytest tests/test_exposure.py -q && uv run mypy` → PASS. **Step 4: Commit** — `git add backend/app/pipelines/exposure.py backend/tests/test_exposure.py backend/pyproject.toml && git commit -m "feat(pipelines): exposure v1 component math, bounds, Spearman"`

---

### Task 3: Component queries

**Files:**
- Modify: `backend/app/pipelines/exposure.py`
- Create: `backend/tests/test_exposure_job.py`

**Interfaces:**
- Produces: `Key = tuple[uuid.UUID, str, int]` (area, type group, month), `Scope = dict[tuple[uuid.UUID, str], int]`, `TYPE_GROUPS`, `TICK_CAP = 16`, `async scope(conn) -> Scope` (area × type group → scorable routes in subtree, 0 for route-less objectives; Decision 1), and, all with keyword `cutoff: date` (data strictly before it): `async route_density(conn, scope) -> dict[Key, Component]` (no cutoff: current catalog), `async season(conn, scope, *, cutoff) -> dict[Key, Component]`, `async nps_visits(conn, scope, *, cutoff)`, `async permits(conn, scope, *, cutoff)`, `async popularity(conn, scope) -> dict[Key, Component]` (current sitelinks), `async mp_tick_count(conn, scope, *, enabled: bool, cutoff)`, `async mp_ice_mixed_ticks(conn, scope, *, enabled: bool, cutoff)`; `ob_ticks` → `Component.absent("no_bulk_source")` everywhere `[assumes D17]`; `in_app_ticks` → `Component.absent("phase4")`.
- Consumes: `canonical_areas.point_key` → `feature_points(grid_bucket, tz)` (plan 5), `cell_daily_conditions(grid_bucket, tz, date, record_kind)` (plan 3), `objectives.disciplines/kind/sitelinks`, `objective_permit_counts` (plan 6), `internal.mp_ticks`, `internal.mp_route_links`, `internal.mp_tick_aggregates` (plans 1, 4).

Component definitions:
- `route_density`: scorable routes of the type group in the area's subtree (`path <@`); missing `no_catalog_routes` for an objective area with none (Decision 1).
- `season_share`: `season_share()` over the area's `(grid_bucket, tz)` series (`canonical_areas.point_key` → `feature_points`), ERA5 rows only, `[cutoff − 3×365 d, cutoff)`; missing `no_point` when the area has no feature point or timezone, `no_history` when a month has no value.
- `nps_visits`: mean monthly visits over years before the cutoff month for units whose centre is within `radius_km` of the area point; missing `outside_parks`, or `no_visit_data` for an in-park area whose month has no entered visits.
- `permit_attempts`: for objective areas, mean attempts over years for that calendar month, month-specific rows only, `(year, month)` before the cutoff month; missing `annual_only` (only `month = 0` totals exist) or `no_permits`.
- `objective_popularity`: objective `sitelinks`; missing `no_wikidata` / `not_objective`.
- `mp_tick_count`: clean rock ticks (`quarantine_reason IS NULL`, `tick_date < cutoff`) of MP routes linked to canonical sport/trad routes in the area's subtree, per the canonical route's type group, month of year; present only for areas in CA/NV (the tick sample's coverage: CA 32.5–42.0 N, 124.5–114.1 W; NV 35.0–42.0 N, 120.0–114.0 W) with at least one such tick; `censored` when any contributing route has ≥16 clean ticks; missing `outside_ca_nv`, `not_in_tick_sample`, `disabled`, or `table_dropped`.
- `mp_ice_mixed_ticks`: `internal.mp_tick_aggregates` monthly rows (period before the cutoff month) of MP routes linked to canonical ice/mixed routes in the subtree, mean per observed year (Decision 7); missing `no_ticks`, `disabled`, or `table_dropped` (`to_regclass('internal.mp_tick_aggregates') IS NULL`).

- [ ] **Step 1: Failing test** — `backend/tests/test_exposure_job.py`:

```python
import asyncio
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.exposure import mp_ice_mixed_ticks, mp_tick_count, nps_visits, route_density, scope
from tests.pgtest import migrated_db, requires_pg, sa_url

AREA = "00000000-0000-0000-0000-00000000000a"
ICE = "00000000-0000-0000-0000-00000000000b"
PEAK = "00000000-0000-0000-0000-00000000000d"
OBJ = "00000000-0000-0000-0000-0000000000f1"
CUTOFF = date(2026, 9, 1)
SEED = f"""
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version) VALUES
  ('37.70000:-119.60000', 37.7, -119.6, grid_bucket_key(37.7, -119.6), 'h5', 'h7', 'America/Los_Angeles', 'f-v1');
INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, disciplines, source, license) VALUES
  ('{OBJ}', 'peak', 'Fixture Peak', 46.85, -121.76, ST_SetSRID(ST_MakePoint(-121.76, 46.85), 4326)::geography,
   '{{alpine}}', 'gnis', 'public_domain');
INSERT INTO canonical_areas (area_id, name, path, lat, lon, geom, point_key, objective_id, coord_precision, source, redistributable) VALUES
  ('{AREA}', 'Fixture Crag', '0000000000000000000000000000000a', 37.7, -119.6,
   ST_SetSRID(ST_MakePoint(-119.6, 37.7), 4326)::geography, '37.70000:-119.60000', NULL, 'area_centroid', 'openbeta', true),
  ('{ICE}', 'Fixture Ice', '0000000000000000000000000000000b', 44.1, -71.3,
   ST_SetSRID(ST_MakePoint(-71.3, 44.1), 4326)::geography, NULL, NULL, 'area_centroid', 'openbeta', true),
  ('{PEAK}', 'Fixture Peak', '0000000000000000000000000000000d', 46.85, -121.76,
   ST_SetSRID(ST_MakePoint(-121.76, 46.85), 4326)::geography, NULL, '{OBJ}', 'crag', 'safeascent_curated', true);
INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000c1', '{AREA}', 'R1', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true),
  ('00000000-0000-0000-0000-0000000000c3', '{AREA}', 'R3', '{{sport}}', 'sport', 'rt-v1', false, true, 'openbeta', true),
  ('00000000-0000-0000-0000-0000000000c2', '{ICE}', 'I1', '{{ice}}', 'ice', 'rt-v1', false, true, 'mp_facts', false);
INSERT INTO internal.mp_route_links (route_id, mp_route_id, match_score, match_method) VALUES
  ('00000000-0000-0000-0000-0000000000c1', 900000001, 1.0, 'auto'),
  ('00000000-0000-0000-0000-0000000000c3', 900000003, 1.0, 'auto'),
  ('00000000-0000-0000-0000-0000000000c2', 900000002, 1.0, 'mp_facts');
INSERT INTO internal.mp_ticks (tick_id, route_id, climber_name, tick_date)
  SELECT g, '900000001', 'c', DATE '2025-07-01' + g FROM generate_series(1, 16) g;
INSERT INTO internal.mp_ticks (tick_id, route_id, climber_name, tick_date) VALUES (17, '900000001', 'c', DATE '2026-09-15');
INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES
  (900000002, '2025-01', 'lead', 4), (900000002, '2024-01', 'lead', 2), (900000002, '2023-02', 'lead', 3),
  (900000002, 'total', 'all', 9);
INSERT INTO nps_units VALUES ('YOSE', 'Yosemite', 37.75, -119.6, 25);
INSERT INTO nps_monthly_visits VALUES ('YOSE', 2025, 7, 600000, 'https://irma.nps.gov/Stats/'),
  ('YOSE', 2026, 9, 999999, 'https://irma.nps.gov/Stats/');
"""


def _go(name, fn):
    async def go():
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.connect() as conn:
                return await fn(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_capped_routes_are_flagged_censored_and_types_not_shared():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            s = await scope(conn)
            return await mp_tick_count(conn, s, enabled=True, cutoff=CUTOFF)

        comps = _go(name, run)
    july = comps[(uuid.UUID(AREA), "trad", 7)]
    assert july.value == 16.0 and july.censored is True
    assert comps[(uuid.UUID(AREA), "trad", 1)].to_json() == {"value": 0.0, "missing": False, "censored": True}
    assert comps[(uuid.UUID(AREA), "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "not_in_tick_sample"}


@requires_pg
def test_cutoff_excludes_later_data():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            s = await scope(conn)
            return await mp_tick_count(conn, s, enabled=True, cutoff=CUTOFF), await nps_visits(conn, s, cutoff=CUTOFF)

        ticks, visits = _go(name, run)
    assert ticks[(uuid.UUID(AREA), "trad", 9)].value == 0.0
    assert visits[(uuid.UUID(AREA), "trad", 9)].to_json() == {"value": None, "missing": True, "reason": "no_visit_data"}
    assert visits[(uuid.UUID(ICE), "ice", 7)].to_json() == {"value": None, "missing": True, "reason": "outside_parks"}
    assert visits[(uuid.UUID(AREA), "trad", 7)].value == 600000.0


@requires_pg
def test_ice_aggregates_count_zero_years_and_disabled_flag():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            s = await scope(conn)
            return (await route_density(conn, s), await mp_ice_mixed_ticks(conn, s, enabled=True, cutoff=CUTOFF),
                    await mp_ice_mixed_ticks(conn, s, enabled=False, cutoff=CUTOFF))

        density, on, off = _go(name, run)
    assert density[(uuid.UUID(ICE), "ice", 1)].value == 1.0
    assert on[(uuid.UUID(ICE), "ice", 1)].value == 2.0
    assert on[(uuid.UUID(ICE), "ice", 2)].value == 1.5
    assert on[(uuid.UUID(ICE), "ice", 7)].value == 0.0
    assert off[(uuid.UUID(ICE), "ice", 1)].to_json() == {"value": None, "missing": True, "reason": "disabled"}


@requires_pg
def test_objective_without_routes_is_kept_with_density_missing():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            s = await scope(conn)
            return s, await route_density(conn, s)

        s, density = _go(name, run)
    assert s[(uuid.UUID(PEAK), "alpine")] == 0
    assert density[(uuid.UUID(PEAK), "alpine", 6)].to_json() == {"value": None, "missing": True, "reason": "no_catalog_routes"}
```

(Ice: aggregate years span 2023–2025 and the export's last month is 2025-01. January observes 2023, 2024, 2025 → (0 + 2 + 4) / 3 = 2.0; February observes 2023 and 2024 (2025-02 is after the last month) → (3 + 0) / 2 = 1.5; July observes 2023 and 2024 → 0.0, a measured zero. Rock: 16 clean ticks in July 2025 on the trad route hit the cap, so every trad month is `censored`; the September 2026 tick is after the cutoff; the sport route has no ticks, so sport is `not_in_tick_sample`, not the trad count.)

- [ ] **Step 2: Implement** — append to `exposure.py` (add `import uuid`, `from datetime import timedelta`, `from sqlalchemy import text`, `from sqlalchemy.ext.asyncio import AsyncConnection` at the top):

```python
Key = tuple[uuid.UUID, str, int]
Scope = dict[tuple[uuid.UUID, str], int]
TYPE_GROUPS = ("sport", "trad", "alpine", "ice", "mixed")
TICK_CAP = 16
SEASON_YEARS = 3
CA_NV = ("ST_MakeEnvelope(-124.5, 32.5, -114.1, 42.0, 4326)", "ST_MakeEnvelope(-120.0, 35.0, -114.0, 42.0, 4326)")
KIND_DEFAULT_GROUP = {"peak": "alpine", "glacier": "alpine", "formation": "alpine", "ice_area": "ice"}


def _months(scope_: Scope) -> list[Key]:
    return [(a, tg, m) for (a, tg) in scope_ for m in range(1, 13)]


def _ym(d: date) -> tuple[int, int]:
    return d.year, d.month


async def scope(conn: AsyncConnection) -> Scope:
    rows = (await conn.execute(text(
        "SELECT a.area_id, s.type_group, count(*) FROM canonical_areas a "
        "JOIN canonical_areas d ON d.path <@ a.path JOIN scorable_routes s ON s.area_id = d.area_id "
        "WHERE a.retired_at IS NULL AND (a.objective_id IS NOT NULL OR EXISTS "
        " (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id AND r.retired_at IS NULL)) "
        "GROUP BY 1, 2"))).all()
    out: Scope = {(uuid.UUID(str(a)), str(tg)): int(n) for a, tg, n in rows}
    objectives = (await conn.execute(text(
        "SELECT a.area_id, o.kind, o.disciplines FROM canonical_areas a JOIN objectives o ON o.objective_id = a.objective_id "
        "WHERE a.retired_at IS NULL"))).all()
    for a, kind, disciplines in objectives:
        area = uuid.UUID(str(a))
        if any(key[0] == area for key in out):
            continue
        groups = [g for g in (disciplines or []) if g in TYPE_GROUPS] or [KIND_DEFAULT_GROUP[str(kind)]]
        for g in groups:
            out[(area, g)] = 0
    return out


async def route_density(conn: AsyncConnection, scope_: Scope) -> dict[Key, Component]:
    return {(a, tg, m): (Component.present(scope_[(a, tg)]) if scope_[(a, tg)] > 0 else Component.absent("no_catalog_routes"))
            for a, tg, m in _months(scope_)}


async def season(conn: AsyncConnection, scope_: Scope, *, cutoff: date) -> dict[Key, Component]:
    series = {uuid.UUID(str(a)): (int(b), str(tz)) for a, b, tz in (await conn.execute(text(
        "SELECT a.area_id, f.grid_bucket, f.tz FROM canonical_areas a JOIN feature_points f ON f.point_key = a.point_key "
        "WHERE f.tz IS NOT NULL"))).all()}
    wanted = {series[a] for a, _ in scope_ if a in series}
    since = cutoff - timedelta(days=SEASON_YEARS * 365)
    history: dict[tuple[int, str], list[tuple[date, float | None, float | None]]] = defaultdict(list)
    if wanted:
        for b, tz, d, tmax, precip in (await conn.execute(text(
                "SELECT grid_bucket, tz, date, tmax, precip_mm FROM cell_daily_conditions "
                "WHERE record_kind = 'era5' AND date >= :since AND date < :cutoff AND grid_bucket = ANY(:b)"),
                {"since": since, "cutoff": cutoff, "b": sorted({b for b, _ in wanted})})).all():
            key = (int(b), str(tz))
            if key in wanted:
                history[key].append((d, float(tmax) if tmax is not None else None,
                                     float(precip) if precip is not None else None))
    out: dict[Key, Component] = {}
    for a, tg in scope_:
        if a not in series:
            for m in range(1, 13):
                out[(a, tg, m)] = Component.absent("no_point")
            continue
        shares = season_share(history.get(series[a], []), tg)
        for m in range(1, 13):
            v = shares[m]
            out[(a, tg, m)] = Component.present(v) if v is not None else Component.absent("no_history")
    return out


async def nps_visits(conn: AsyncConnection, scope_: Scope, *, cutoff: date) -> dict[Key, Component]:
    in_park = (
        "FROM canonical_areas a JOIN nps_units u ON a.geom IS NOT NULL "
        "AND ST_DWithin(a.geom, ST_SetSRID(ST_MakePoint(u.lon, u.lat), 4326)::geography, u.radius_km * 1000) "
    )
    parked = {uuid.UUID(str(a)) for (a,) in (await conn.execute(text(f"SELECT DISTINCT a.area_id {in_park}"))).all()}
    rows = (await conn.execute(text(
        f"SELECT a.area_id, v.month, avg(v.visits) {in_park}"
        "JOIN nps_monthly_visits v ON v.unit_code = u.unit_code "
        "WHERE (v.year, v.month) < (:y, :m) GROUP BY 1, 2"), {"y": cutoff.year, "m": cutoff.month})).all()
    visits = {(uuid.UUID(str(a)), int(m)): float(n) for a, m, n in rows}
    out: dict[Key, Component] = {}
    for a, tg, m in _months(scope_):
        if (a, m) in visits:
            out[(a, tg, m)] = Component.present(visits[(a, m)])
        else:
            out[(a, tg, m)] = Component.absent("no_visit_data" if a in parked else "outside_parks")
    return out


async def permits(conn: AsyncConnection, scope_: Scope, *, cutoff: date) -> dict[Key, Component]:
    rows = (await conn.execute(text(
        "SELECT a.area_id, p.month, avg(p.attempts) FROM canonical_areas a JOIN objective_permit_counts p "
        "ON p.objective_id = a.objective_id WHERE p.attempts IS NOT NULL "
        "AND ((p.month = 0 AND p.year < :y) OR (p.month > 0 AND (p.year, p.month) < (:y, :m))) GROUP BY 1, 2"),
        {"y": cutoff.year, "m": cutoff.month})).all()
    monthly: dict[tuple[uuid.UUID, int], float] = {}
    annual_only: set[uuid.UUID] = set()
    for a, m, n in rows:
        area = uuid.UUID(str(a))
        if int(m) == 0:
            annual_only.add(area)
        else:
            monthly[(area, int(m))] = float(n)
    has_monthly = {area for area, _ in monthly}
    out: dict[Key, Component] = {}
    for a, tg, m in _months(scope_):
        if (a, m) in monthly:
            out[(a, tg, m)] = Component.present(monthly[(a, m)])
        elif a in annual_only and a not in has_monthly:
            out[(a, tg, m)] = Component.absent("annual_only")
        else:
            out[(a, tg, m)] = Component.absent("no_permits")
    return out


async def popularity(conn: AsyncConnection, scope_: Scope) -> dict[Key, Component]:
    rows = (await conn.execute(text(
        "SELECT a.area_id, o.sitelinks FROM canonical_areas a JOIN objectives o ON o.objective_id = a.objective_id"))).all()
    links = {uuid.UUID(str(a)): s for a, s in rows}
    out: dict[Key, Component] = {}
    for a, tg, m in _months(scope_):
        if a not in links:
            out[(a, tg, m)] = Component.absent("not_objective")
        elif links[a] is None:
            out[(a, tg, m)] = Component.absent("no_wikidata")
        else:
            out[(a, tg, m)] = Component.present(float(links[a]))
    return out


async def _exists(conn: AsyncConnection, name: str) -> bool:
    return (await conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})).scalar() is True


async def mp_tick_count(conn: AsyncConnection, scope_: Scope, *, enabled: bool, cutoff: date) -> dict[Key, Component]:
    keys = [k for k in _months(scope_) if k[1] in ("sport", "trad")]
    if not enabled:
        return {k: Component.absent("disabled") for k in keys}
    if not await _exists(conn, "internal.mp_ticks"):
        return {k: Component.absent("table_dropped") for k in keys}
    areas = sorted({a for a, _, _ in keys})
    in_ca_nv = {uuid.UUID(str(a)) for (a,) in (await conn.execute(text(
        f"SELECT area_id FROM canonical_areas WHERE geom IS NOT NULL AND (ST_Within(geom::geometry, {CA_NV[0]}) "
        f"OR ST_Within(geom::geometry, {CA_NV[1]}))"))).all()}
    rows = (await conn.execute(text(
        "WITH clean AS (SELECT route_id::bigint AS mp_route_id, tick_date FROM internal.mp_ticks "
        " WHERE quarantine_reason IS NULL AND tick_date IS NOT NULL AND tick_date < :cutoff AND route_id ~ '^[0-9]{1,18}$'), "
        "per_route AS (SELECT mp_route_id, count(*) AS total FROM clean GROUP BY 1), "
        "linked AS (SELECT l.mp_route_id, c.type_group, ca.path FROM internal.mp_route_links l "
        " JOIN canonical_routes c ON c.route_id = l.route_id AND c.retired_at IS NULL AND c.type_group IN ('sport', 'trad') "
        " JOIN canonical_areas ca ON ca.area_id = c.area_id) "
        "SELECT a.area_id, k.type_group, extract(month FROM t.tick_date)::int AS m, count(*) AS n, "
        " bool_or(p.total >= :cap) AS capped "
        "FROM canonical_areas a JOIN linked k ON k.path <@ a.path "
        "JOIN clean t ON t.mp_route_id = k.mp_route_id JOIN per_route p ON p.mp_route_id = k.mp_route_id "
        "WHERE a.area_id = ANY(:areas) GROUP BY 1, 2, 3"),
        {"cutoff": cutoff, "cap": TICK_CAP, "areas": areas})).all()
    counts: dict[Key, float] = {}
    capped: dict[tuple[uuid.UUID, str], bool] = defaultdict(bool)
    for a, tg, m, n, c in rows:
        area = uuid.UUID(str(a))
        counts[(area, str(tg), int(m))] = float(n)
        capped[(area, str(tg))] |= bool(c)
    sampled = {(a, tg) for a, tg, _ in counts}
    out: dict[Key, Component] = {}
    for a, tg, m in keys:
        if a not in in_ca_nv:
            out[(a, tg, m)] = Component.absent("outside_ca_nv")
        elif (a, tg) not in sampled:
            out[(a, tg, m)] = Component.absent("not_in_tick_sample")
        else:
            out[(a, tg, m)] = Component.present(counts.get((a, tg, m), 0.0), censored=capped[(a, tg)])
    return out


async def mp_ice_mixed_ticks(conn: AsyncConnection, scope_: Scope, *, enabled: bool, cutoff: date) -> dict[Key, Component]:
    keys = [k for k in _months(scope_) if k[1] in ("ice", "mixed")]
    if not enabled:
        return {k: Component.absent("disabled") for k in keys}
    if not await _exists(conn, "internal.mp_tick_aggregates"):
        return {k: Component.absent("table_dropped") for k in keys}
    rows = (await conn.execute(text(
        "SELECT a.area_id, c.type_group, g.period, sum(g.tick_count) "
        "FROM internal.mp_tick_aggregates g JOIN internal.mp_route_links l ON l.mp_route_id = g.mp_route_id "
        "JOIN canonical_routes c ON c.route_id = l.route_id AND c.retired_at IS NULL AND c.type_group IN ('ice', 'mixed') "
        "JOIN canonical_areas ca ON ca.area_id = c.area_id JOIN canonical_areas a ON ca.path <@ a.path "
        "WHERE g.period <> 'total' AND g.period < :cut AND a.area_id = ANY(:areas) GROUP BY 1, 2, 3"),
        {"cut": f"{cutoff:%Y-%m}", "areas": sorted({a for a, _, _ in keys})})).all()
    per_group: dict[tuple[uuid.UUID, str], dict[tuple[int, int], float]] = defaultdict(dict)
    for a, tg, period, n in rows:
        per_group[(uuid.UUID(str(a)), str(tg))][(int(period[:4]), int(period[5:7]))] = float(n)
    if not per_group:
        return {k: Component.absent("no_ticks") for k in keys}
    last = max(ym for months in per_group.values() for ym in months)
    out: dict[Key, Component] = {}
    for a, tg, m in keys:
        months = per_group.get((a, tg))
        if not months:
            out[(a, tg, m)] = Component.absent("no_ticks")
            continue
        years = range(min(y for y, _ in months), max(y for y, _ in months) + 1)
        observed = [y for y in years if (y, m) <= last]
        if not observed:
            out[(a, tg, m)] = Component.absent("no_observed_year")
            continue
        out[(a, tg, m)] = Component.present(sum(months.get((y, m), 0.0) for y in observed) / len(observed))
    return out
```

`in_ca_nv` uses the area's point; the 16-tick cap check counts clean ticks per route before the cutoff, which is how the MP sample was censored. The export's last month (`last`) is the latest monthly row across the whole table, since plan 1 quarantines the scrape month as `partial_month` and never loads it.

- [ ] **Step 3: Run** — `cd backend && uv run pytest tests/test_exposure_job.py -q && uv run mypy` → PASS. **Step 4: Commit** — `git add backend/app/pipelines/exposure.py backend/tests/test_exposure_job.py && git commit -m "feat(pipelines): exposure components with data cutoff; no fabricated zeros"`

---

### Task 4: Snapshot job, validation report, CLI

**Files:**
- Modify: `backend/app/pipelines/exposure.py`, `backend/tests/test_exposure_job.py`

**Interfaces:**
- Produces: `KEEP_SNAPSHOTS = 3`, `async build(conn, *, today: date, cutoff: date | None = None, mp_enabled: bool) -> dict[str, object]` (`cutoff` defaults to `today`, must be ≤ `today`; assembles every component per key into `proxy` JSON with all nine component names, writes rows with `as_of = today`, `data_cutoff = cutoff`, `exposure_point`, bounds with `has_permits = not proxy['permit_attempts'].missing`; deletes production snapshots (`data_cutoff = as_of`) beyond the newest three, never backtest snapshots), `async validate(conn, *, as_of: date, data_cutoff: date) -> dict[str, float | None]` (Spearman of `exposure_point` vs permit attempts over objective rows; vs `mp_tick_count` over rows where not missing; counts of NULL points), `async drop_snapshot(conn, *, as_of: date, data_cutoff: date) -> int`, CLI `python -m app.pipelines.exposure [--cutoff YYYY-MM-DD] [--drop-snapshot AS_OF:CUTOFF]` (reads `settings.EXPOSURE_ENABLE_MP_TICKS`, logs `source='exposure'` with the validation numbers in `validation_report`).

- [ ] **Step 1: Failing test** — append to `test_exposure_job.py`:

```python
import pytest
from sqlalchemy import text

from app.pipelines.exposure import build

CLIMATE = """
INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, precip_mm, source, record_kind)
  SELECT grid_bucket_key(37.7, -119.6), 'America/Los_Angeles', d::date, 20, 0, 'open_meteo_archive', 'era5'
  FROM generate_series(DATE '2023-01-01', DATE '2025-12-31', interval '1 day') d;
"""


def _begin(name, fn):
    async def go():
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                return await fn(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_snapshot_has_every_component_and_bounds():
    with migrated_db(seed_sql=SEED + CLIMATE) as name:
        async def run(conn):
            await build(conn, today=date(2026, 9, 28), mp_enabled=True)
            return (await conn.execute(text(
                "SELECT proxy, exposure_point, exposure_lo, exposure_hi, data_cutoff FROM exposure_index "
                "WHERE area_id = :a AND type_group = 'trad' AND month = 7"), {"a": AREA})).one()

        proxy, point, lo, hi, cutoff = _begin(name, run)
    assert set(proxy) == {"route_density", "season_share", "ob_ticks", "nps_visits", "permit_attempts",
                          "objective_popularity", "in_app_ticks", "mp_tick_count", "mp_ice_mixed_ticks"}
    assert proxy["ob_ticks"] == {"value": None, "missing": True, "reason": "no_bulk_source"}
    assert cutoff == date(2026, 9, 28)
    assert point == pytest.approx(1.0, rel=1e-6)
    assert (lo, hi) == pytest.approx((point / 3, point * 3), rel=1e-6)


@requires_pg
def test_missing_season_gives_null_point_not_a_floor():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            await build(conn, today=date(2026, 9, 28), mp_enabled=True)
            return (await conn.execute(text(
                "SELECT area_id::text, type_group, proxy->'season_share', exposure_point, exposure_lo, exposure_hi "
                "FROM exposure_index WHERE month = 1 AND area_id IN (:a, :p)"), {"a": AREA, "p": PEAK})).all()

        rows = {(a, tg): rest for a, tg, *rest in _begin(name, run)}
    season_json, point, lo, hi = rows[(AREA, "trad")]
    assert season_json == {"value": None, "missing": True, "reason": "no_history"}
    assert (point, lo, hi) == (None, None, None)
    assert rows[(PEAK, "alpine")][1:] == [None, None, None]


@requires_pg
def test_backtest_snapshots_survive_production_pruning():
    with migrated_db(seed_sql=SEED + CLIMATE) as name:
        async def run(conn):
            await build(conn, today=date(2026, 5, 1), cutoff=date(2025, 1, 1), mp_enabled=True)
            for day in (date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)):
                await build(conn, today=day, mp_enabled=True)
            return (await conn.execute(text(
                "SELECT DISTINCT as_of, data_cutoff FROM exposure_index ORDER BY 1"))).all()

        snapshots = [tuple(r) for r in _begin(name, run)]
    assert snapshots == [(date(2026, 5, 1), date(2025, 1, 1)), (date(2026, 7, 1), date(2026, 7, 1)),
                         (date(2026, 8, 1), date(2026, 8, 1)), (date(2026, 9, 1), date(2026, 9, 1))]


def test_cutoff_after_today_is_refused():
    async def run():
        await build(None, today=date(2026, 9, 28), cutoff=date(2026, 9, 29), mp_enabled=True)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="after today"):
        asyncio.run(run())
```

(Trad July with the climate fixture: one trad route, every day 20 °C and dry → season 1.0 → point 1.0; REAL columns are compared with a relative tolerance.)

- [ ] **Step 2: Implement** — append to `exposure.py` (add `import argparse`, `import asyncio`, `import json` at the top):

```python
KEEP_SNAPSHOTS = 3


def _not_applicable(name: str) -> Component:
    return Component.absent("not_applicable_type_group") if name in MP_COMPONENTS else Component.absent("missing")


async def build(conn: AsyncConnection, *, today: date, cutoff: date | None = None, mp_enabled: bool) -> dict[str, object]:
    cut = cutoff or today
    if cut > today:
        raise ValueError(f"data cutoff {cut} is after today {today}")
    s = await scope(conn)
    parts: dict[str, dict[Key, Component]] = {
        "route_density": await route_density(conn, s),
        "season_share": await season(conn, s, cutoff=cut),
        "nps_visits": await nps_visits(conn, s, cutoff=cut),
        "permit_attempts": await permits(conn, s, cutoff=cut),
        "objective_popularity": await popularity(conn, s),
        "mp_tick_count": await mp_tick_count(conn, s, enabled=mp_enabled, cutoff=cut),
        "mp_ice_mixed_ticks": await mp_ice_mixed_ticks(conn, s, enabled=mp_enabled, cutoff=cut),
    }
    rows = []
    for key in _months(s):
        area, tg, month = key
        comps = {name: parts[name].get(key) or _not_applicable(name) for name in parts}
        proxy = {name: c.to_json() for name, c in comps.items()}
        proxy["ob_ticks"] = Component.absent("no_bulk_source").to_json()
        proxy["in_app_ticks"] = Component.absent("phase4").to_json()
        point = exposure_point(comps["route_density"].value, comps["season_share"].value)
        lo, hi = bounds(point, has_permits=not comps["permit_attempts"].missing)
        rows.append({"a": area, "tg": tg, "m": month, "n": s[(area, tg)], "p": json.dumps(proxy, sort_keys=True),
                     "v": PROXY_VERSION, "d": today, "c": cut, "pt": point, "lo": lo, "hi": hi})
    if rows:
        await conn.execute(text(
            "INSERT INTO exposure_index (area_id, type_group, month, n_routes, proxy, proxy_version, as_of, data_cutoff, "
            "exposure_point, exposure_lo, exposure_hi) VALUES (:a, :tg, :m, :n, CAST(:p AS jsonb), :v, :d, :c, :pt, :lo, :hi) "
            "ON CONFLICT (area_id, type_group, month, as_of, data_cutoff) DO UPDATE SET n_routes = EXCLUDED.n_routes, "
            "proxy = EXCLUDED.proxy, proxy_version = EXCLUDED.proxy_version, exposure_point = EXCLUDED.exposure_point, "
            "exposure_lo = EXCLUDED.exposure_lo, exposure_hi = EXCLUDED.exposure_hi"), rows)
    await conn.execute(text(
        "DELETE FROM exposure_index WHERE data_cutoff = as_of AND as_of NOT IN "
        "(SELECT DISTINCT as_of FROM exposure_index WHERE data_cutoff = as_of ORDER BY as_of DESC LIMIT :k)"),
        {"k": KEEP_SNAPSHOTS})
    return {"rows": len(rows), "as_of": today.isoformat(), "data_cutoff": cut.isoformat(), "mp_enabled": mp_enabled,
            "null_points": sum(1 for r in rows if r["pt"] is None)}


async def validate(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> dict[str, float | None]:
    rows = (await conn.execute(text(
        "SELECT exposure_point, (proxy->'permit_attempts'->>'value')::float, (proxy->'mp_tick_count'->>'value')::float "
        "FROM exposure_index WHERE as_of = :d AND data_cutoff = :c"), {"d": as_of, "c": data_cutoff})).all()
    permit_pairs = [(float(p), float(a)) for p, a, _ in rows if p is not None and a is not None]
    tick_pairs = [(float(p), float(t)) for p, _, t in rows if p is not None and t is not None]
    return {
        "spearman_vs_permits": spearman([p for p, _ in permit_pairs], [a for _, a in permit_pairs]),
        "spearman_vs_mp_ticks": spearman([p for p, _ in tick_pairs], [t for _, t in tick_pairs]),
        "n_permit_rows": float(len(permit_pairs)),
        "n_tick_rows": float(len(tick_pairs)),
        "n_rows": float(len(rows)),
        "n_null_points": float(sum(1 for p, _, _ in rows if p is None)),
    }


async def drop_snapshot(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> int:
    result = await conn.execute(text("DELETE FROM exposure_index WHERE as_of = :d AND data_cutoff = :c"),
                                {"d": as_of, "c": data_cutoff})
    return int(result.rowcount or 0)


async def _run(*, today: date, cutoff: date | None, mp_enabled: bool, drop: tuple[date, date] | None) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.ingest_log import finish_run, start_run
    from app.pipelines.validate import ValidationReport

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if drop is not None:
                return {"dropped_rows": await drop_snapshot(conn, as_of=drop[0], data_cutoff=drop[1])}
            run_id = await start_run(conn, source="exposure", window_start=cutoff or today, window_end=today,
                                     content_sha256=None)
            summary = await build(conn, today=today, cutoff=cutoff, mp_enabled=mp_enabled)
            checks = await validate(conn, as_of=today, data_cutoff=cutoff or today)
            report = ValidationReport("exposure")
            report.rows_in = report.accepted = int(str(summary["rows"]))
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=report.accepted)
            await conn.execute(text(
                "UPDATE source_ingest_log SET validation_report = validation_report || CAST(:extra AS jsonb) "
                "WHERE run_id = :run_id"),
                {"extra": json.dumps({**summary, **checks}, default=str), "run_id": run_id})
            return {**summary, **checks}
    finally:
        await engine.dispose()


def _main() -> None:
    from app.config import settings
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(description="Build an exposure_index snapshot (P2-9).")
    parser.add_argument("--cutoff", type=date.fromisoformat, default=None,
                        help="Use only data before this date (Phase 3 backtests); default: today (UTC).")
    parser.add_argument("--drop-snapshot", default=None, metavar="AS_OF:CUTOFF",
                        help="Delete one snapshot, e.g. 2026-09-28:2025-01-01, and exit.")
    args = parser.parse_args()
    drop = None
    if args.drop_snapshot:
        as_of, cut = args.drop_snapshot.split(":")
        drop = (date.fromisoformat(as_of), date.fromisoformat(cut))
    result = asyncio.run(_run(today=utc_today(), cutoff=args.cutoff, mp_enabled=settings.EXPOSURE_ENABLE_MP_TICKS,
                              drop=drop))
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    _main()
```

The MP components exist only for their type groups (rock for `mp_tick_count`, ice/mixed for `mp_ice_mixed_ticks`); other type groups carry them as `missing: true, reason: "not_applicable_type_group"` so every row has all nine keys. Plan 1's frozen `finish_run` writes `report.summary()` into `validation_report`; the Spearman numbers and `null_points` are merged into that JSON by one `UPDATE … || jsonb` on the same run row (the log is `ingest`-writable), so plan 1's signature is unchanged.

- [ ] **Step 3: Run** — `cd backend && uv run pytest tests/test_exposure_job.py -q && uv run mypy` → PASS. **Step 4: Commit** — `git add backend/app/pipelines/exposure.py backend/tests/test_exposure_job.py && git commit -m "feat(pipelines): exposure snapshots by data cutoff, bounds, Spearman, CLI"`

---

### Task 5: Guards — removal drill, proxy never served

**Files:**
- Create: `backend/tests/test_no_proxy_in_api.py`
- Modify: `backend/tests/test_exposure_job.py`

- [ ] **Step 1: Tests**

`backend/tests/test_no_proxy_in_api.py`:

```python
import re
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "app" / "api"


def test_no_endpoint_reads_the_exposure_proxy():
    offenders = [str(p) for p in API.rglob("*.py") if re.search(r"exposure_index|\bproxy\b", p.read_text())]
    assert offenders == []
```

(The grep is the code-level guard; the privilege guard is the REVOKE in 0014 plus `test_0014_revokes_default_privilege_select_from_app` and the `-m db` cell in Task 6.)

Append to `test_exposure_job.py`:

```python
@requires_pg
def test_removal_drill_changes_only_mp_components():
    with migrated_db(seed_sql=SEED + CLIMATE) as name:
        async def snapshot(conn, enabled, day):
            await build(conn, today=day, cutoff=date(2026, 8, 31), mp_enabled=enabled)
            return {(str(a), tg, m): p for a, tg, m, p in (await conn.execute(text(
                "SELECT area_id, type_group, month, proxy FROM exposure_index WHERE as_of = :d"), {"d": day})).all()}

        async def run(conn):
            return await snapshot(conn, True, date(2026, 9, 1)), await snapshot(conn, False, date(2026, 9, 2))

        on, off = _begin(name, run)
    assert on.keys() == off.keys()
    for key in on:
        for name in on[key]:
            if name in ("mp_tick_count", "mp_ice_mixed_ticks"):
                assert off[key][name]["missing"] is True
            else:
                assert on[key][name] == off[key][name]
```

(Both runs share `data_cutoff = 2026-08-31`, so only the MP flag differs.)

- [ ] **Step 2: Run** — `cd backend && uv run pytest tests/test_no_proxy_in_api.py tests/test_exposure_job.py -q` → PASS. **Step 3: Commit** — `git add backend/tests/test_no_proxy_in_api.py backend/tests/test_exposure_job.py && git commit -m "test: exposure removal drill and no proxy in the API"`

---

### Task 6: NPS curated loaders, workflow, PR 2b-4 docs

**Files:**
- Modify: `backend/app/pipelines/curated.py`, `backend/tests/test_curated.py`, `data/curated/README.md`, `.github/workflows/data-weekly.yml`, `CHANGELOG.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`
- Create: `data/curated/nps_units.csv`, `data/curated/nps_visits.csv`, `backend/tests/verify/test_phase2b_exposure_storage.py`

**Interfaces:**
- Produces: `NPS_UNIT_COLUMNS = ("unit_code", "name", "lat", "lon", "radius_km")`, `NPS_VISIT_COLUMNS = ("unit_code", "year", "month", "recreation_visits", "source_url")`, `async load_nps_units(conn, path, report) -> int`, `async load_nps_visits(conn, path, report, today) -> int` (the current and future months quarantined `future`), CLI choices `nps-units`, `nps-visits`.

- [ ] **Step 1: Failing test** — append to `test_curated.py`:

```python
from app.pipelines.curated import NPS_UNIT_COLUMNS, NPS_VISIT_COLUMNS, load_nps_units, load_nps_visits


@requires_pg
def test_nps_loaders_validate_and_reject_future_months(tmp_path):
    units = _write(tmp_path, "u.csv", NPS_UNIT_COLUMNS, ["YOSE,Yosemite,37.75,-119.6,25", "BAD,Nowhere,95,-119.6,25"])
    visits = _write(tmp_path, "v.csv", NPS_VISIT_COLUMNS, [
        "YOSE,2025,7,600000,https://irma.nps.gov/Stats/", "YOSE,2026,10,1,https://irma.nps.gov/Stats/",
        "YOSE,2026,9,5,https://irma.nps.gov/Stats/"])
    with migrated_db() as name:
        r1, r2 = ValidationReport("u"), ValidationReport("v")
        assert _go(name, lambda c: load_nps_units(c, units, r1)) == 1
        assert _go(name, lambda c: load_nps_visits(c, visits, r2, date(2026, 9, 28))) == 1
        assert r1.quarantined == {"out_of_range": 1} and r2.quarantined == {"future": 2}
```

- [ ] **Step 2: Implement** — append to `curated.py`:

```python
NPS_UNIT_COLUMNS = ("unit_code", "name", "lat", "lon", "radius_km")
NPS_VISIT_COLUMNS = ("unit_code", "year", "month", "recreation_visits", "source_url")


def _reader(path: Path, header: tuple[str, ...]) -> list[tuple[int, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != header:
            raise ValueError(f"{path.name}: header must be {','.join(header)}")
        return list(enumerate(reader, start=2))


async def load_nps_units(conn: AsyncConnection, path: Path, report: ValidationReport) -> int:
    rows = []
    for number, raw in _reader(path, NPS_UNIT_COLUMNS):
        ref = f"{path.name}:{number}"
        try:
            lat, lon, radius = float(raw["lat"]), float(raw["lon"]), float(raw["radius_km"])
        except ValueError:
            report.quarantine(ref, "invalid")
            continue
        problem = coord_problem(lat, lon)
        if problem or radius <= 0 or not raw["unit_code"].isalpha():
            report.quarantine(ref, problem or "invalid")
            continue
        report.accept()
        rows.append({"u": raw["unit_code"].upper(), "n": raw["name"], "lat": lat, "lon": lon, "r": radius})
    if rows:
        await conn.execute(text(
            "INSERT INTO nps_units (unit_code, name, lat, lon, radius_km) VALUES (:u, :n, :lat, :lon, :r) "
            "ON CONFLICT (unit_code) DO UPDATE SET name = EXCLUDED.name, lat = EXCLUDED.lat, lon = EXCLUDED.lon, radius_km = EXCLUDED.radius_km"),
            rows)
    return len(rows)


async def load_nps_visits(conn: AsyncConnection, path: Path, report: ValidationReport, today: date) -> int:
    units = {str(u) for (u,) in (await conn.execute(text("SELECT unit_code FROM nps_units"))).all()}
    rows = []
    for number, raw in _reader(path, NPS_VISIT_COLUMNS):
        ref = f"{path.name}:{number}"
        try:
            year, month, visits = int(raw["year"]), int(raw["month"]), int(raw["recreation_visits"])
        except ValueError:
            report.quarantine(ref, "invalid")
            continue
        if not 1 <= month <= 12 or visits < 0 or not raw["source_url"].startswith("https://"):
            report.quarantine(ref, "invalid")
            continue
        if (year, month) >= (today.year, today.month):
            report.quarantine(ref, "future")
            continue
        if raw["unit_code"].upper() not in units:
            report.quarantine(ref, "unknown_unit")
            continue
        report.accept()
        rows.append({"u": raw["unit_code"].upper(), "y": year, "m": month, "v": visits, "s": raw["source_url"]})
    if rows:
        await conn.execute(text(
            "INSERT INTO nps_monthly_visits (unit_code, year, month, visits, source_url) VALUES (:u, :y, :m, :v, :s) "
            "ON CONFLICT (unit_code, year, month) DO UPDATE SET visits = EXCLUDED.visits, source_url = EXCLUDED.source_url"), rows)
    return len(rows)
```

The current month is quarantined as `future` because its visitation total is not final. Extend `curated._main` with `nps-units` and `nps-visits` (same pattern as the plan 6 choices: path argument, `ValidationReport`, `today=utc_today()`, run log). `data/curated/nps_units.csv` and `nps_visits.csv`: header lines only; README gains a paragraph on them (NPS IRMA Stats, public domain, monthly recreation visits; the unit list is the owner's hand-picked climbing parks).

`backend/tests/verify/test_phase2b_exposure_storage.py`:

```python
import asyncio
import os

import asyncpg
import pytest

from app.db.ssl import connect_args_for

pytestmark = pytest.mark.db
URL = os.environ.get("VERIFY_DATABASE_URL")


def fetch(sql: str) -> list[asyncpg.Record]:
    if not URL:
        pytest.skip("VERIFY_DATABASE_URL not set")

    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(URL, **connect_args_for(URL))
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_bounds_null_exactly_when_point_null_and_spearman_reported():
    [row] = fetch(
        "SELECT count(*) AS n, count(*) FILTER (WHERE exposure_point IS NULL) AS null_points, "
        "count(*) FILTER (WHERE (exposure_point IS NULL) <> (exposure_lo IS NULL OR exposure_hi IS NULL)) AS mismatched "
        "FROM exposure_index WHERE data_cutoff = as_of AND as_of = (SELECT max(as_of) FROM exposure_index WHERE data_cutoff = as_of)")
    assert row["n"] > 0 and row["mismatched"] == 0
    print({"rows": row["n"], "null_points": row["null_points"]})
    [rep] = fetch("SELECT validation_report FROM source_ingest_log WHERE source = 'exposure' AND status = 'ok' "
                  "ORDER BY finished_at DESC LIMIT 1")
    print(rep["validation_report"])


def test_no_fabricated_floor_or_spread():
    [row] = fetch(
        "SELECT count(*) FILTER (WHERE (proxy->'season_share'->>'missing')::boolean AND exposure_point IS NOT NULL) AS floored, "
        "count(*) FILTER (WHERE proxy->'mp_tick_count'->>'reason' = 'outside_ca_nv' "
        "  AND (proxy->'mp_tick_count'->>'missing')::boolean IS NOT TRUE) AS bad_mp "
        "FROM exposure_index WHERE data_cutoff = as_of")
    assert row["floored"] == 0 and row["bad_mp"] == 0


def test_app_cannot_read_exposure_index():
    [row] = fetch("SELECT has_table_privilege('app', 'public.exposure_index', 'SELECT') AS app_can_read")
    assert row["app_can_read"] is False
```

(`app.db.ssl.connect_args_for` gives the verify-full TLS arguments for a remote URL — foundations plan SEC1.)

Workflow: add to `data-weekly.yml`'s `monthly` job (after the lightning steps), passing nothing from inputs:

```yaml
      - name: Exposure snapshot
        working-directory: backend
        run: uv run python -m app.pipelines.exposure
```

Docs: CHANGELOG "Phase 2b exposure v1 (PR 2b-4)" dated entry; CLAUDE.md: `EXPOSURE_ENABLE_MP_TICKS`, the removal drill, and "`exposure_index` is revoked from `app`" in one line each; DEPLOYMENT.md: none beyond the monthly job; DATABASE_STRUCTURE.md: `exposure_index` (`data_cutoff`, backtest snapshots, "never served by the API, no `app` privilege"), NPS tables, and the `ob_ticks` note: "missing `no_bulk_source` (D17); plan 4's one-time OpenBeta tick-coverage sample and its recorded revisit trigger decide whether P2-9's OpenBeta-tick component returns".

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_no_mp_data.py` → green.
- [ ] **Step 4: Commit** — `git add backend/app/pipelines/curated.py backend/tests/ data/curated/ .github/workflows/data-weekly.yml CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md && git commit -m "feat(pipelines): NPS visitation loaders; monthly exposure; acceptance cells"`

---

# PR 2b-5 — `feat/p2b-prediction-compaction` `[assumes D18]`

### Task 7: Migration `0015` — `prediction_archive_v1` and `prediction_history`

**Files:**
- Create: `backend/alembic/versions/0015_prediction_archive.py`, `backend/tests/test_migration_0015.py`
- Modify: `backend/app/models/exposure.py` (add `PredictionArchive`), `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB): `prediction_archive_v1(route_id bigint, month_start date CHECK (extract(day FROM month_start) = 1), scores smallint[] NOT NULL CHECK (cardinality(scores) = 31) CHECK (-1 <= ALL (scores) AND 1000 >= ALL (scores)), PK (route_id, month_start))`; view `prediction_history(route_id bigint, prediction_date date, risk_score double precision, color_code text, store text)` = recent `historical_predictions` rows plus decoded archive days (`v / 10.0`; `-1` → NULL + `'gray'`; NULL slots and days past the month's end omitted; a day present in both comes from `historical_predictions`). `app` gets SELECT on both through `migrator`'s default privileges (unchanged); `ingest` gets SELECT/INSERT/UPDATE/DELETE on the archive and SELECT/DELETE on `historical_predictions`. The Phase 3 backtest reads `prediction_history`, never the raw tables.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0015.py`:

```python
import asyncio

import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
EMPTY = "array_fill(NULL::smallint, ARRAY[31])"


def test_0015_shape_checks():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        run_sql(name, f"INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-08-01', {EMPTY})")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, f"INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-08-02', {EMPTY})")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO prediction_archive_v1 VALUES (900000002, '2026-08-01', ARRAY[1,2]::smallint[])")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO prediction_archive_v1 VALUES (900000003, '2026-08-01', "
                          "array_fill(1001::smallint, ARRAY[31]))")


def test_prediction_history_decodes_and_prefers_recent_rows():
    seed = (
        "INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-02-01', "
        "(SELECT array_agg(CASE g WHEN 1 THEN 245 WHEN 2 THEN -1 WHEN 3 THEN 300 WHEN 30 THEN 50 ELSE NULL END ORDER BY g)"
        "::smallint[] FROM generate_series(1, 31) g));"
        "INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) "
        "VALUES (900000001, '2026-02-03', 31.0, 'yellow');"
    )
    with migrated_db(seed_sql=seed) as name:
        async def rows():
            conn = await asyncpg.connect(pg_url(name))
            try:
                return [tuple(r) for r in await conn.fetch(
                    "SELECT prediction_date::text, risk_score, color_code, store FROM prediction_history ORDER BY 1")]
            finally:
                await conn.close()

        got = asyncio.run(rows())
    assert got == [("2026-02-01", 24.5, "archived", "archive"), ("2026-02-02", None, "gray", "archive"),
                   ("2026-02-03", 31.0, "yellow", "recent")]
```

(The slot for 30 February is past the month's end and is omitted even though it holds a value; day 3 comes from the recent row.)

- [ ] **Step 2: Implement** `backend/alembic/versions/0015_prediction_archive.py`:

```python
"""P2-8 compaction target: one row per route per month, scores[day] = round(score * 10)
(the one decimal every surface displays), -1 = insufficient (gray), NULL = no prediction.
prediction_history is the one reader for the trends endpoint and the Phase 3 backtest.
Public (MP ids may be displayed, amendment D5); the app role only reads it (D18)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_prediction_archive"
down_revision = "0014_exposure"
branch_labels = None
depends_on = None

HISTORY_VIEW = """
CREATE VIEW prediction_history AS
SELECT h.route_id::bigint AS route_id, h.prediction_date, h.risk_score::double precision AS risk_score,
       h.color_code, 'recent'::text AS store
FROM historical_predictions h
UNION ALL
SELECT a.route_id, (a.month_start + (u.i - 1)::int)::date,
       CASE WHEN u.v = -1 THEN NULL ELSE u.v / 10.0 END::double precision,
       CASE WHEN u.v = -1 THEN 'gray' ELSE 'archived' END, 'archive'
FROM prediction_archive_v1 a CROSS JOIN LATERAL unnest(a.scores) WITH ORDINALITY u(v, i)
WHERE u.v IS NOT NULL
  AND u.i <= extract(day FROM (a.month_start + interval '1 month' - interval '1 day'))
  AND NOT EXISTS (SELECT 1 FROM historical_predictions h2
                  WHERE h2.route_id = a.route_id AND h2.prediction_date = a.month_start + (u.i - 1)::int)
"""


def upgrade() -> None:
    op.create_table(
        "prediction_archive_v1",
        sa.Column("route_id", sa.BigInteger(), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("scores", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.PrimaryKeyConstraint("route_id", "month_start"),
        sa.CheckConstraint("extract(day FROM month_start) = 1", name="prediction_archive_v1_month_start_check"),
        sa.CheckConstraint("cardinality(scores) = 31", name="prediction_archive_v1_scores_check"),
        sa.CheckConstraint("-1 <= ALL (scores) AND 1000 >= ALL (scores)", name="prediction_archive_v1_range_check"),
    )
    op.execute(HISTORY_VIEW)


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM prediction_archive_v1").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0015: {n} archived route-months (their source rows are deleted)")
    op.execute("DROP VIEW prediction_history")
    op.drop_table("prediction_archive_v1")
```

Grants ("Plan 8 (0015)") appended to `grants_phase2.sql`: `GRANT SELECT, INSERT, UPDATE, DELETE ON public.prediction_archive_v1 TO ingest; GRANT SELECT, DELETE ON public.historical_predictions TO ingest; GRANT SELECT ON public.prediction_history TO ingest;`. No `trainer` grant (D13; Phase 3 grants SELECT on `prediction_history`). `verify_roles_phase2.sql` `ingest_writes`: `('public.prediction_archive_v1','INSERT'), ('public.prediction_archive_v1','UPDATE'), ('public.prediction_archive_v1','DELETE'), ('public.historical_predictions','DELETE')`. Phase 1's `verify_roles.sql` still passes: `app` holds only SELECT on the new table and view.

- [ ] **Step 3: Run** — `cd backend && uv run pytest tests/test_migration_0015.py tests/test_migrations.py -q` → PASS (including `test_role_scripts_create_least_privilege_roles`). **Step 4: Commit** — `git add backend/alembic/versions/0015_prediction_archive.py backend/app/models/exposure.py backend/tests/test_migration_0015.py backend/db/roles/ && git commit -m "feat(db): 0015 prediction_archive_v1 and prediction_history (read-only to app)"`

---

### Task 8: Fold, verify, delete, prune; remove the writer's purge

**Files:**
- Create: `backend/app/pipelines/prediction_archive.py`, `backend/tests/test_prediction_archive.py`
- Modify: `backend/app/tasks/safety_computation_optimized.py` (`_save_to_historical`), `backend/pyproject.toml`, `.github/workflows/data-daily.yml`

**Interfaces:**
- Produces: `ARCHIVE_GRAY = -1`, `SCORE_SCALE = 10`, `FOLD_AFTER_DAYS = 7`, `ARCHIVE_RETENTION_DAYS = 400`, `FOLD_SQL`, `VERIFY_SQL`, `INVALID_SQL`, `async fold_month(conn, month_start: date, *, fold_before: date) -> dict[str, int]` (refuse on invalid scores, insert/merge, verify, delete — one transaction; raises and rolls back on any mismatch), `async prune_archive(conn, *, today: date) -> int`, `async fold_all(engine_factory, *, today: date, dry_run: bool = False) -> dict[str, object]` (every month with rows older than `today − 7 d`, oldest first, logged per month as `prediction_archive`, then the prune), CLI `python -m app.pipelines.prediction_archive [--dry-run]`.
- Changes: `_save_to_historical` no longer deletes anything (the fold is the only deleter; retention is the archive's 400 days).

- [ ] **Step 1: Failing tests** — `backend/tests/test_prediction_archive.py`:

```python
import asyncio
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.pipelines.prediction_archive import fold_month, prune_archive
from app.services.risk_bands import displayed_risk
from tests.pgtest import migrated_db, requires_pg, sa_url

pytestmark = requires_pg
RAW = {1: 23.4, 3: 24.5, 4: 24.96, 5: 0.05, 6: 0.3, 7: 0.04, 8: 99.99, 31: 80.6}
SEED = "INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) VALUES " + ", ".join(
    [f"(900000001, '2026-08-{d:02d}', {s}, 'green')" for d, s in RAW.items()]
    + ["(900000001, '2026-08-02', NULL, 'gray')", "(900000001, '2026-09-25', 50.0, 'orange')"]) + ";"


def _go(name, fn):
    async def go():
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                return await fn(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


async def _scores(conn):
    return list((await conn.execute(text("SELECT scores FROM prediction_archive_v1"))).scalar_one())


async def _count(conn):
    return (await conn.execute(text("SELECT count(*) FROM historical_predictions"))).scalar_one()


def test_gray_day_round_trips_as_insufficient():
    with migrated_db(seed_sql=SEED) as name:
        result = _go(name, lambda c: fold_month(c, date(2026, 8, 1), fold_before=date(2026, 9, 21)))
        assert result == {"folded": 9, "deleted": 9}
        scores = _go(name, _scores)
        assert scores[0] == 234 and scores[1] == -1 and scores[30] == 806 and scores[8] is None
        assert _go(name, _count) == 1


def test_fold_preserves_displayed_band_and_precision():
    with migrated_db(seed_sql=SEED) as name:
        _go(name, lambda c: fold_month(c, date(2026, 8, 1), fold_before=date(2026, 9, 21)))
        decoded = {r.prediction_date.day: (r.risk_score, r.color_code) for r in _go(name, lambda c: _history(c))}
    for day, raw in RAW.items():
        score, colour = decoded[day]
        shown = displayed_risk(None if colour == "gray" else score)
        assert shown == displayed_risk(raw), (day, raw, score)
    assert displayed_risk(decoded[3][0]) == (24.5, "green", "ok")
    assert displayed_risk(decoded[6][0]) == (0.3, "green", "ok")
    assert decoded[7] == (None, "gray")


async def _history(conn):
    return (await conn.execute(text(
        "SELECT prediction_date, risk_score, color_code FROM prediction_history ORDER BY prediction_date"))).all()


def test_invalid_scores_refuse_the_month_untouched():
    bad = "INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) VALUES " \
          "(900000002, '2026-07-01', 150.0, 'red'), (900000002, '2026-07-02', 20.0, 'green');"
    with migrated_db(seed_sql=bad) as name:
        with pytest.raises(RuntimeError, match="invalid"):
            _go(name, lambda c: fold_month(c, date(2026, 7, 1), fold_before=date(2026, 9, 21)))
        assert _go(name, _count) == 2


def test_rerun_after_partial_fold_is_idempotent():
    with migrated_db(seed_sql=SEED) as name:
        # Simulate a crash after the insert: archive row present, source rows still there.
        _go(name, lambda c: c.execute(text(
            "INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-08-01', "
            "(SELECT array_agg(CASE WHEN g = 1 THEN 234 ELSE NULL END ORDER BY g)::smallint[] FROM generate_series(1, 31) g))")))
        result = _go(name, lambda c: fold_month(c, date(2026, 8, 1), fold_before=date(2026, 9, 21)))
        assert result == {"folded": 9, "deleted": 9}
        assert _go(name, _scores)[1] == -1


def test_prune_keeps_400_days():
    seed = ("INSERT INTO prediction_archive_v1 VALUES "
            "(900000001, '2025-07-01', array_fill(NULL::smallint, ARRAY[31])), "
            "(900000001, '2025-08-01', array_fill(NULL::smallint, ARRAY[31]));")
    with migrated_db(seed_sql=seed) as name:
        pruned = _go(name, lambda c: prune_archive(c, today=date(2026, 9, 28)))
        left = _go(name, lambda c: _months(c))
    assert pruned == 1 and left == [date(2025, 8, 1)]


async def _months(conn):
    return [m for (m,) in (await conn.execute(text("SELECT month_start FROM prediction_archive_v1 ORDER BY 1"))).all()]


def test_nightly_writer_no_longer_purges():
    from app.tasks.safety_computation_optimized import _save_to_historical

    seed = "INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) " \
           "VALUES (900000001, '2020-01-01', 12.0, 'green');"
    with migrated_db(seed_sql=seed) as name:
        async def go():
            engine = create_async_engine(sa_url(name))
            try:
                async with AsyncSession(engine) as db:
                    await _save_to_historical(db, {900000002: {"risk_score": 30.0, "color_code": "yellow"}}, date(2026, 9, 28))
                async with engine.connect() as conn:
                    return (await conn.execute(text(
                        "SELECT count(*) FROM historical_predictions WHERE prediction_date = '2020-01-01'"))).scalar_one()
            finally:
                await engine.dispose()

        assert asyncio.run(go()) == 1
```

(0.05 → `round(0.5)` = 1 → 0.1, and `displayed_risk(0.05)` is also 0.1; 0.04 is below `MIN_ESTIMABLE_SCORE` → `-1`; 24.96 → 250 → 25.0 yellow, the same as the live display; 24.5 → 245 → 24.5 green, where the old integer encoding gave 25 → yellow.)

- [ ] **Step 2: Implement** `backend/app/pipelines/prediction_archive.py`:

```python
"""P2-8 compaction (D18): fold historical_predictions rows older than 7 days into one
array per route-month, verify every folded row, then delete it — all in one transaction per
month, so a crash leaves either nothing changed or a complete, verified month. The archive
keeps 400 days (spec); this job is the only deleter of historical_predictions."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport
from app.services.risk_bands import MIN_ESTIMABLE_SCORE

ARCHIVE_GRAY = -1
SCORE_SCALE = 10
FOLD_AFTER_DAYS = 7
ARCHIVE_RETENTION_DAYS = 400

MONTH_FILTER = (
    "prediction_date >= :m AND prediction_date < (:m + interval '1 month') AND prediction_date < :before"
)
# numeric rounding (half away from zero) on score*10 reproduces displayed_risk's one decimal;
# a score below MIN_ESTIMABLE_SCORE or NULL is the gray insufficient day, as on every live surface.
SRC = (
    "SELECT route_id, date_trunc('month', prediction_date)::date AS m, extract(day FROM prediction_date)::int AS d, "
    f"CASE WHEN risk_score IS NULL OR risk_score < :min_score THEN {ARCHIVE_GRAY} "
    f"ELSE round((risk_score * {SCORE_SCALE})::numeric)::int END AS v "
    f"FROM historical_predictions WHERE {MONTH_FILTER}"
)
INVALID_SQL = (
    "SELECT count(*) FROM historical_predictions WHERE risk_score IS NOT NULL "
    f"AND (risk_score = 'NaN'::float8 OR risk_score < 0 OR risk_score > 100) AND {MONTH_FILTER}"
)
FOLD_SQL = f"""
WITH src AS ({SRC}),
agg AS (SELECT route_id, m, jsonb_object_agg(d::text, v) AS days FROM src GROUP BY route_id, m)
INSERT INTO prediction_archive_v1 (route_id, month_start, scores)
SELECT route_id, m, ARRAY(SELECT (days ->> g::text)::smallint FROM generate_series(1, 31) g ORDER BY g) FROM agg
ON CONFLICT (route_id, month_start) DO UPDATE SET scores = (
  SELECT array_agg(coalesce(n.v, o.v) ORDER BY n.i)
  FROM unnest(EXCLUDED.scores) WITH ORDINALITY n(v, i)
  JOIN unnest(prediction_archive_v1.scores) WITH ORDINALITY o(v, i) USING (i))
"""
VERIFY_SQL = f"""
WITH src AS ({SRC})
SELECT count(*) AS n, count(*) FILTER (WHERE a.scores[s.d] IS DISTINCT FROM s.v) AS bad
FROM src s LEFT JOIN prediction_archive_v1 a ON a.route_id = s.route_id AND a.month_start = s.m
"""
DELETE_SQL = f"DELETE FROM historical_predictions WHERE {MONTH_FILTER}"


async def fold_month(conn: AsyncConnection, month_start: date, *, fold_before: date) -> dict[str, int]:
    params = {"m": month_start, "before": fold_before, "min_score": MIN_ESTIMABLE_SCORE}
    invalid = int((await conn.execute(text(INVALID_SQL), params)).scalar_one())
    if invalid:
        raise RuntimeError(f"refusing to fold {month_start:%Y-%m}: {invalid} invalid stored scores (NaN or outside 0-100)")
    await conn.execute(text(FOLD_SQL), params)
    check = (await conn.execute(text(VERIFY_SQL), params)).one()
    if int(check.bad):
        raise RuntimeError(f"archive verification failed for {month_start:%Y-%m}: {int(check.bad)} of {int(check.n)} rows differ")
    deleted = (await conn.execute(text(DELETE_SQL), params)).rowcount
    if deleted != int(check.n):
        raise RuntimeError(f"deleted {deleted} rows but verified {int(check.n)} for {month_start:%Y-%m}")
    return {"folded": int(check.n), "deleted": deleted}


async def prune_archive(conn: AsyncConnection, *, today: date) -> int:
    keep_from = (today - timedelta(days=ARCHIVE_RETENTION_DAYS)).replace(day=1)
    result = await conn.execute(text("DELETE FROM prediction_archive_v1 WHERE month_start < :k"), {"k": keep_from})
    return int(result.rowcount or 0)


async def fold_all(engine_factory: Callable[[], AsyncEngine], *, today: date, dry_run: bool = False) -> dict[str, object]:
    before = today - timedelta(days=FOLD_AFTER_DAYS)
    engine = engine_factory()
    done: dict[str, int] = {}
    try:
        async with engine.connect() as conn:
            months = [m for (m,) in (await conn.execute(text(
                "SELECT DISTINCT date_trunc('month', prediction_date)::date FROM historical_predictions "
                "WHERE prediction_date < :b ORDER BY 1"), {"b": before})).all()]
        if dry_run:
            return {"mode": "dry_run", "months": [m.isoformat() for m in months], "fold_before": before.isoformat()}
        for m in months:
            async with engine.begin() as conn:
                result = await fold_month(conn, m, fold_before=before)
                report = ValidationReport("prediction_archive")
                report.rows_in = report.accepted = result["folded"]
                run_id = await start_run(conn, source="prediction_archive", window_start=m, window_end=before, content_sha256=None)
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=result["deleted"])
            done[m.isoformat()] = result["folded"]
        async with engine.begin() as conn:
            pruned = await prune_archive(conn, today=today)
    finally:
        await engine.dispose()
    return {"months": done, "fold_before": before.isoformat(), "pruned_archive_months": pruned}


if __name__ == "__main__":
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(fold_all(ingest_engine, today=utc_today(), dry_run=args.dry_run)), sort_keys=True))
```

Rounding: `risk_score` is `double precision`; the cast to `numeric` keeps 15 significant digits, so `round(score × 10)` agrees with Python's `round(score, 1)` in `displayed_risk` except for a raw score within ~1e-13 of an exact half-step (e.g. a binary value just under 24.95), where the two may differ by 0.1. `VERIFY_SQL` uses the same expression, so verification compares like with like; `test_fold_preserves_displayed_band_and_precision` pins the ordinary cases, including the 24.5 and 0.05–0.49 cases the integer encoding broke.

Writer: in `backend/app/tasks/safety_computation_optimized.py` `_save_to_historical`, delete the `# Purge data older than 1 year` block (the `DELETE FROM historical_predictions WHERE prediction_date < CURRENT_DATE - INTERVAL '1 year'` try/except) and the docstring line "Also purges data older than 1 year for storage efficiency." Replace the docstring line with `Retention is prediction_archive's job (2b-5): the daily fold is the only deleter.` The `failed_batches` raise that followed the purge stays. The writer stores the run's `target_date` (the nightly task passes `date.today()` on the UTC worker); rows newer than `today − 7 d` are never folded.

Append `"app.pipelines.prediction_archive"` to strict mypy. `data-daily.yml` (09:10 UTC): add as its last step:

```yaml
      - name: Fold predictions older than 7 days into the archive
        working-directory: backend
        run: uv run python -m app.pipelines.prediction_archive
```

(The fold deletes only rows older than 7 days, so it never contends with the nightly writer's inserts for today even when the nightly run's tail reaches ~10:00 UTC.)

- [ ] **Step 3: Run** — `cd backend && uv run pytest tests/test_prediction_archive.py -q && uv run mypy` → PASS. **Step 4: Commit** — `git add backend/app/pipelines/prediction_archive.py backend/tests/test_prediction_archive.py backend/app/tasks/safety_computation_optimized.py backend/pyproject.toml .github/workflows/data-daily.yml && git commit -m "feat(pipelines): verified one-decimal compaction of historical_predictions; fold owns retention"`

---

### Task 9: Trends endpoint reads `prediction_history`; PR 2b-5 docs

**Files:**
- Modify: `backend/app/api/v1/mp_routes.py` (historical trends query, around line 1567), `CHANGELOG.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`
- Create: `backend/tests/test_historical_trends_archive.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_historical_trends_archive.py` (same fixture pattern as `test_ascent_analytics.py`: a migrated database, `get_db` overridden, the weather fetchers patched out like its `no_weather_calls` fixture, and `utc_today` pinned so the window never depends on the machine's clock or timezone):

```python
import asyncio
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.v1 import mp_routes
from app.db.session import get_db
from app.main import app
from tests.pgtest import migrated_db, requires_pg, sa_url

pytestmark = requires_pg
TODAY = date(2026, 9, 28)


@pytest.fixture
def no_weather(monkeypatch):
    # Same as test_ascent_analytics.no_weather_calls: the weather-volatility block catches this.
    import requests

    def refuse(*args, **kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr(requests, "get", refuse)


def test_trends_merge_archive_and_recent_without_duplicates(no_weather, monkeypatch):
    monkeypatch.setattr(mp_routes, "utc_today", lambda: TODAY)
    seed = """
    INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000101, 'Fixture Crag', 40.0, -105.0);
    INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000001, 'Fixture', 900000101, 'Trad');
    INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-08-01',
      (SELECT array_agg(CASE WHEN g = 1 THEN 300 WHEN g = 2 THEN -1 WHEN g = 4 THEN 245 ELSE NULL END ORDER BY g)::smallint[]
       FROM generate_series(1, 31) g));
    INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-06-01',
      (SELECT array_agg(CASE WHEN g = 1 THEN 500 ELSE NULL END ORDER BY g)::smallint[] FROM generate_series(1, 31) g));
    INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) VALUES
      (900000001, '2026-08-01', 31.0, 'yellow'), (900000001, '2026-09-28', 40.0, 'yellow');
    """
    with migrated_db(seed_sql=seed) as name:
        engine = create_async_engine(sa_url(name))

        async def db():
            async with AsyncSession(engine) as session:
                yield session

        app.dependency_overrides[get_db] = db
        try:
            async def call():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                    return (await client.get("/api/v1/mp-routes/900000001/historical-trends", params={"days": 60})).json()

            data = asyncio.run(call())
        finally:
            app.dependency_overrides.clear()
            asyncio.run(engine.dispose())
    by_date = {p["date"]: p for p in data["historical_predictions"]}
    assert len(by_date) == len(data["historical_predictions"])
    assert set(by_date) == {"2026-08-01", "2026-08-02", "2026-08-04", "2026-09-28"}
    assert by_date["2026-08-01"]["risk_score"] == 31.0
    assert by_date["2026-08-02"]["data_status"] == "insufficient_data"
    assert by_date["2026-08-04"]["risk_score"] == 24.5 and by_date["2026-08-04"]["color_code"] == "green"
```

(The window is 2026-07-30 onward, so the June archive day is excluded, the NULL slot on 3 August is no prediction and absent, and the recent 1 August row wins over the archived 30.0.)

- [ ] **Step 2: Implement** — in `mp_routes.py` add `from datetime import timedelta` (beside the existing `date` import) and `from app.services.temporal_weighting import utc_today`, then replace `historical_query` and its execute call in the trends handler with:

```python
    # Days older than a week live in prediction_archive_v1 (D18); prediction_history decodes
    # them (one decimal, gray = insufficient) and prefers a day still in historical_predictions.
    historical_query = text("""
        SELECT prediction_date, risk_score, color_code
        FROM prediction_history
        WHERE route_id = :route_id AND prediction_date >= :since
        ORDER BY prediction_date ASC
    """)

    try:
        since = utc_today() - timedelta(days=days)
        result = await db.execute(historical_query, {"route_id": mp_route_id, "since": since})
```

The existing loop already maps `(None, 'gray')` to insufficient and re-derives colours from the score through `displayed_risk` for every other row, so `'archived'` never reaches the response and archived days show the same one-decimal score and band as they did live. `reference_date` (weather volatility) is unchanged. Docs: CHANGELOG "Prediction compaction (PR 2b-5)" dated entry; DEPLOYMENT.md nightly-job section: "rows older than 7 days are folded daily into `prediction_archive_v1` by `data-daily.yml`; the archive keeps 400 days; the nightly writer no longer purges; the Phase 3 backtest reads `prediction_history` (history older than 400 days is only in the pre-2b5 dump)"; DATABASE_STRUCTURE.md: the archive table, its `score × 10` encoding and the view.

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green (existing trends tests in `test_ascent_analytics.py` included).
- [ ] **Step 4: Commit** — `git add backend/app/api/v1/mp_routes.py backend/tests/test_historical_trends_archive.py CHANGELOG.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md && git commit -m "feat(api): historical trends read prediction_history with an injected UTC today"`

---

### Task 10: OWNER/AGENT RUNBOOK — PR 2b-4 (exposure)

- [ ] **Step 1 (owner/agent)**: migrate (`0014`) on a Neon branch as `migrator` (TLS `sslmode=verify-full&sslrootcert=system`, foundations plan Task 9 pattern), apply `grants_phase2.sql` as owner, run `verify_roles_phase2.sql` (the `app` × `exposure_index` row must pass), then prod.
- [ ] **Step 2 (owner)**: fill `data/curated/nps_units.csv` (Yosemite, Joshua Tree, Rocky Mountain, Grand Teton, Zion, Acadia, New River Gorge, Denali, Mount Rainier, Black Canyon of the Gunnison, Devils Tower, North Cascades, Sequoia/Kings Canyon, Pinnacles — Red Rock is BLM, not NPS) with centre coordinates and a radius covering the climbing areas, and `nps_visits.csv` with monthly recreation visits from NPS IRMA Stats for the last complete years; commit on a docs branch; load with `curated nps-units` then `curated nps-visits`.
- [ ] **Step 3 (owner/agent)**: `python -m app.pipelines.exposure` (flag on) → rows, `null_points`, and the Spearman report; then the removal drill on the branch: run with `EXPOSURE_ENABLE_MP_TICKS=false` and the same `--cutoff`, confirm the verify cells pass and both MP keys are missing-flagged, drop that drill snapshot with `--drop-snapshot`, then rerun with the flag on. Run `VERIFY_DATABASE_URL=… uv run pytest -m db tests/verify/test_phase2b_exposure_storage.py -s`. Record `spearman_vs_permits`, `spearman_vs_mp_ticks` and `n_null_points` in the PR (spec 2b-4 acceptance); a high `n_null_points` means areas without ERA5 history or feature points — it is reported, never floored.

### Task 11: OWNER/AGENT RUNBOOK — PR 2b-5 (compaction)

- [ ] **Step 1 (owner/agent): Dump first** — `pg_dump -Fc -t public.historical_predictions` into `~/Developer/safeascent-private/backups/pre-2b5/` (foundations plan Task 8 pattern, `sslmode=verify-full&sslrootcert=system`). This dump is the only copy of history beyond the archive's 400 days; keep it with the other private backups. Note the current size: as analyst `SELECT pg_size_pretty(pg_total_relation_size('historical_predictions')), pg_size_pretty(pg_database_size(current_database()))`.
- [ ] **Step 2 (owner/agent): Branch rehearsal** — migrate (`0015`), grants, verify; `prediction_archive --dry-run` lists months; run for real (each month is checked for invalid scores and verified before deletion; any refusal or mismatch aborts that month untouched — report the month and count to the owner, never edit scores to make it pass); run it again → no months left except the last week.
- [ ] **Step 3 (owner): Deploy and prod** — deploy the backend (trends endpoint reads `prediction_history`; the writer no longer purges) → migrate prod → run the fold outside 01:00–10:00 UTC (the nightly run starts 01:00 and can run to ~10:00) → as owner, in a quiet window between 12:00 and 23:00 UTC on a day the healthchecks.io nightly check is already green and `SELECT count(*) FROM pg_stat_activity WHERE query ILIKE '%historical_predictions%' AND state = 'active'` returns 0, `VACUUM (FULL, ANALYZE) historical_predictions` via `SET ROLE migrator` (exclusive lock for minutes). Neon storage drops after its history-retention window.
- [ ] **Step 4 (owner/agent): Acceptance** — `pg_database_size` within the self-imposed ~4.5 GB budget (Neon Launch has no storage cap; storage is billed at $0.35/GB-month, so this is a cost target — spec 2b-5); trends endpoint shows a route's last 60 days identically before and after (compare JSON for 5 routes; scores and bands must match exactly, since the archive keeps one decimal); `data-daily.yml` folds and prunes daily thereafter. Tell the Phase 3 owner that the backtest reads `prediction_history` and covers 400 days.

---

## Self-review

- Spec coverage: 2b-4 — every component in the P2-9 table (route density, season share, OpenBeta ticks per D17 with plan 4's coverage sample as the revisit path, NPS visits, permits, objective popularity, in-app ticks, both optional MP components with the flag, censoring and CA/NV scope), `log(1+x)` is Phase 3's transform (the raw values are stored), bounds ×/÷3 and ×/÷1.5, Spearman validation, removal drill, proxy never served and `app` revoked, data cutoff for P3:397 splits (Tasks 1–6, 10). 2b-5 — compact layout, row-for-row verification, bands unchanged, 400-day retention, one reader view for the endpoint and the backtest, cadence unchanged, DB within the ~4.5 GB cost budget (Tasks 7–9, 11), with the storage placement and writer adjusted per D18.
- Review items: X1 (score×10), X2 (retention, writer purge removed, `prediction_history` for the backtest), X3 (no floor, no `present(0)` outside the sample, per-type-group ticks, zero-tick years counted, route-less objectives kept, no permit spreading), X4 (`data_cutoff`), SEC5 (REVOKE + migration test + `-m db` cell), D13 (no `trainer` grants), D17 note, and the minors (REAL tolerance, deterministic trends test, writer date text, VACUUM window, `test_0014` coverage, CLI code) are each in a named step.
- Placeholders: none beyond owner-curated CSV rows and Console values. `ingest_log.start_run`/`finish_run` are called with plan 1 Task 3's frozen keywords; the exposure validation numbers are merged into the run's `validation_report` by a follow-up UPDATE, not a new keyword.
- Types: `Component`, `Key`, `Scope`, `build(conn, *, today, cutoff, mp_enabled)`, `validate(conn, *, as_of, data_cutoff)`, `fold_month`, `prune_archive`, `ARCHIVE_GRAY`, `SCORE_SCALE` are used consistently; the archive encoding matches between `SRC`, `VERIFY_SQL`, the `prediction_history` view and the endpoint; `feature_points.tz`, `canonical_areas.point_key`, `cell_daily_conditions(grid_bucket, tz, date, record_kind)` and `grid_bucket_key()` follow the cross-plan contract (plans 1, 3, 5).
