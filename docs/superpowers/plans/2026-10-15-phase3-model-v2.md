# Phase 3 Model v2 (MVP-0 + MVP-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status (2026-10-15):** drafted overnight under the owner's instruction not to pause for decisions. Every design call this plan makes is marked `[default pending: DPn]` with the recommended default it is written against (Appendix A). The owner reviews them before execution starts; any overruled DP changes only the tasks that cite it.

**Goal:** Replace the hand-tuned kernel with an in-house, data-fit risk model. It is a Poisson GLM baseline plus empirical-Bayes shrinkage toward a blend of the geographic parent and a feature-similar archetype parent. It carries confidence and ranges, is calibrated and backtested (including against the kernel's `historical_predictions`), auto-promoted through a hard gate, and served from one stored score that the map, popup and breakdown all read.

**Architecture:**
- **Offline (GitHub Actions, `trainer` role).** `app.ml.train run` reads the clean views, R10 links, the MP catalog, `scoring_unit_features` and one `internal.exposure_index` snapshot. It assembles cells (MP area × type group × month) with point-in-time incident counts, then:
  - fits the stage-1 Poisson GLM (exposure + static covariates);
  - fits the stage-2 EB (geo hierarchy blended with feature archetypes);
  - tunes d\*, confidence thresholds and the interval factor on spatial and rolling-origin CV;
  - runs the gate against the active version;
  - materializes `model.route_scores` for every scorable route × month;
  - writes `model.registry` and promotes or rejects.
- **Serving (API, `app` role, read-only).** Endpoints read the active version's stored row. `explain()` decomposes that same number using the bundle. No score is computed at request time, and the nightly kernel is deleted.
- **Purity.** All new model code lives in `backend/app/ml/`. Everything except `dataset.py`, `registry.py`, `materialize.py` and `train.py` is pure (no DB, no network).

**Tech Stack:** Python 3.12, numpy (serving and training), scipy (`train` group only: gamma quantiles, `gammaln`) `[default pending: DP8]`, SQLAlchemy async + asyncpg, Alembic, Postgres (`ltree`), pytest + Hypothesis, GitHub Actions, React 19 + TypeScript (frontend contract).

**Spec:**
- `docs/superpowers/specs/2026-09-27-phase3-model-v2-design.md` (cited "P3:line").
- `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md` (cited "A:line").
- `docs/superpowers/specs/2026-10-15-phase3-amendment-mp-catalog.md` (cited "MP-A §n"; written with this plan; records every place P3 is superseded by the MP catalog and later decisions).
- Exposure plan `docs/superpowers/plans/2026-10-15-exposure-index.md`, "Conflicts with the Phase 3 spec" 1–7 (cited "X-conflict n").
- Foundation code read 2026-10-15 on `main` `e756aba`:
  - migrations `0001`–`0010`;
  - `app/models/*`;
  - `app/pipelines/{mp_catalog,static_features,normals,localday,mp_tick_aggregates,db,ingest_log,gnis}.py`;
  - `app/data/repair/{dates,legacy_links,dedupe,activity}.py`;
  - `app/services/*`, `app/api/v1/{predict,mp_routes}.py`, `app/tasks/safety_computation_optimized.py`;
  - `db/roles/*.sql`, `tests/test_roles_phase2.py`, `scripts/runbook_helpers.sh`;
  - `frontend/src/{services/api.ts,utils/riskUtils.ts}`.
- Binding owner rules (memory `feedback_safeascent_similarity_model`, 2026-09-28/30):
  - risk borrows from characteristically similar routes anywhere, and pooling ships in MVP-1;
  - zero local accidents never means safe;
  - low confidence shows the number, its range and a hatched marker, and gray means no evidence at all;
  - bands stay 25/50/75 until calibration says otherwise;
  - a confidence metric replaces minimum-ascent rules;
  - AAC year ranges are intervals.

**Scope:** MVP-0 and MVP-1 (P3:501–521) plus amendment D1–D5 and G1–G7 `[default pending: DP18]`. Out of scope, each in a later plan:
- v2.1 rules and the hazard banner (P3:523);
- v2.2 NumPyro (P3:527);
- v2.3 learned CM (P3:535);
- objective-level scoring (P3:163–169; waits on the objectives re-plan, X-conflict 7);
- Phase 4 PR 4-8 (incident weights, retrain trigger).

**Prerequisites:**
- `main` at `0010_accident_route_links` with the catalog refreshed and R10 applied.
- The exposure plan's X-2 merged, and `0011_exposure_index` on prod with at least one production snapshot.
- Rolling-origin CV additionally needs backtest snapshots built with `exposure build --cutoff` for 2004-01-01, 2008-01-01, 2012-01-01, 2016-01-01 and 2026-02-05 (Task 21 builds them).
- `cell_climate_normals` filled for route buckets in a Professional window. Until then `normals_status = 'pending'`, which this plan reads as missing, never as zero.

## PRs

| PR | Branch | Tasks | Migration | Milestone |
|---|---|---|---|---|
| 3-0 | `feat/p3-registry-trainer` | 1–4 | `0012_model_registry` | MVP-0: registry, `trainer` role, bundle, purge guard |
| 3-1 | `feat/p3-training-data` | 5–9 | none | MVP-0/1: point-in-time training data |
| 3-2 | `feat/p3-model-core` | 10–15 | none | MVP-1: GLM, archetypes, EB, posterior, explain |
| 3-3 | `feat/p3-backtest-gate` | 16–21 | none | MVP-0/1: metrics, backtest, tuning, gate, train CLI, workflows, kernel row, first candidate |
| 3-4 | `feat/p3-mvp1-serving` | 22–26 | `0013_drop_legacy_weather` | MVP-1: stored-score API, kernel removal, `weather` drop (hard gate), parity |
| 3-5 | `feat/p3-frontend-contract` | 27–29 | none | MVP-1: honest display (confidence, range, hatch, gray) |

PRs merge in order. 3-5 may open in parallel with 3-4 because 3-4's response contract is additive (Task 22), but it merges after.

## Migration numbering

- Exposure takes `0011_exposure_index`. This plan takes:
  - `0012_model_registry` (`down_revision = "0011_exposure_index"`, PR 3-0);
  - `0013_drop_legacy_weather` (`down_revision = "0012_model_registry"`, PR 3-4).
- **Phase 4 renumbers after this plan.** The exposure plan's Task 9 Step 4 moves Phase 4 to `0012_accounts`/`0013_reports`/`0014_report_promotions`. Those numbers now collide, so Phase 4 becomes:
  - `0014_accounts` (`down_revision = "0013_drop_legacy_weather"`);
  - `0015_reports`;
  - `0016_report_promotions`.

  The same renumbering covers its `test_migration_*` files, its `migrated_db(...)` pins, its downgrade targets and its runbook `MIG current` lines. Task 26 Step 5 edits that plan document. If Phase 4 PRs 4-1..4-7 are executed before PR 3-4 merges, `0013_drop_legacy_weather` is renumbered to follow them instead, and Task 24 re-pins its `down_revision`. The owner picks the order: "Phase 3 first" (2026-10-12) means this plan's numbers stand.
- Before writing each migration, run `cd backend && uv run alembic heads` and confirm the single head is the expected `down_revision`.

## Global Constraints

The foundations, MP-catalog and exposure plans' Global Constraints apply. Also:
- **Missing is never 0.** Every reader keeps SQL NULL as `None`/`NaN`. Inside the design matrix, a missing covariate is its training mean (standardized 0) **plus a missing indicator** `[default pending: DP20]`, never the physical value 0. `coalesce(…, 0)` on any model input or output is forbidden; `tests/test_no_coalesce_in_ml.py` greps `app/ml/` and `app/services/model_scores.py`.
- **Never show a fabricated score.** A route with no stored row, no active version or a malformed row renders "Unavailable" (HTTP 404 on the single-route endpoint; `safety: null` in bulk). `insufficient` is a stored, reasoned state (`no_evidence`), shown gray with no number. No `|| 0` / `?? 0` anywhere (`tests/test_no_fabricated_risk.py`, `riskUtils.test.ts` already enforce).
- **Zero local accidents never means safe.** A posterior rate is a Gamma mean with a positive prior shape (shape floor `MIN_PRIOR_SHAPE = 0.05`), so it is never 0. A route is `insufficient` only when there is neither geo evidence below national nor a feature neighbour within d\* (A:145–150).
- **Bands stay 25/50/75** (`risk_bands.RISK_BAND_THRESHOLDS`), applied to the stored national within-type-group percentile (P3:63, A:152). G7 is computed and reported; switching schemes is a separate owner-reviewed change.
- **Leak-free training.**
  - Incidents are weighted by the share of their date interval inside the window.
  - Exposure is read from the snapshot whose `data_cutoff` equals the fold cutoff.
  - Static features are time-invariant.
  - Archetypes, d\*, thresholds, φ and the GLM are fit on training folds only (G5).
- **AAC year ranges are intervals** (`year_lo..year_hi`). No code reads `year` or the synthetic July-1 `date` of a year-precision row as a point.
- **Least privilege.**
  - `trainer` reads exactly the allowlist in Task 3, and writes only `model.registry` (INSERT, UPDATE) and `model.route_scores` (INSERT, DELETE).
  - `app` and `analyst` get SELECT on `model.*` only.
  - `ingest` gets nothing on `model`.
  - Grants change only in lockstep: `grants_phase3.sql`, `verify_roles_phase3.sql` and `test_roles_phase3.py`, plus the edits to `verify_roles_phase2.sql`/`test_roles_phase2.py` that Task 3 names.
- **Parameterised SQL only.** Arrays travel as `CAST(:x AS bigint[])`/`real[]`/`text[]`. The only f-strings in SQL interpolate module constants. There is no DDL in `app/`.
- **Synthetic fixtures only.** MP ids ≥ 900000000, `Fixture …` names, generated incidents. No real export, no MP prose, no real ids in `docs/`, `data/` or `backend/tests/` (`scripts/check_no_mp_data.py`).
- **No LLM and no hosted model in scoring** (P3:19). Jev stays Phase 4 triage only.
- **Public artifacts are aggregate-only** `[default pending: DP16]`. Workflow logs, issue bodies and uploaded artifacts never carry route ids, coordinates or names. Per-route movers stay in `model.registry.diff_report`.
- **Runbooks** follow the plan-2 safety structure:
  - a Neon branch rehearsal first;
  - a snapshot branch before the first prod write;
  - a STOP for the owner before prod;
  - explicit `TARGET_HOST`;
  - `save_once` for evidence;
  - `gate_pass`/`gate_require` around every prod write;
  - helpers only from `backend/scripts/runbook_helpers.sh`.
- Every new module joins the strict mypy allowlist in `backend/pyproject.toml`, and `app.models.model_registry` joins the models block.
- `.env.example` mirrors `Settings` (`tests/test_env_example_parity.py`).
- `CHANGELOG.md` gets one dated entry per PR, and `CLAUDE.md` is updated where commands or roles change.

## Review Focus

1. **A route in an area with zero incidents, in a state with zero incidents, whose feature archetype holds incidents elsewhere.** Expect a number with a range and `conf_level` `low` or `medium`, never `insufficient` and never a percentile computed from a zero rate (Task 13 `test_no_local_incident_borrows_from_archetype`).
2. **An AAC row with `date_precision = 'year'`, `year_lo = 2003`, `year_hi = 2005` and a rolling-origin cutoff of 2004-01-01.** Expect 1/3 of its weight in training and 2/3 in the test window, spread across months by the type group's seasonal profile, and never counted at July 1 2004 (Task 6 `test_year_interval_straddles_cutoff`).
3. **A rock cell whose tick coverage is partial while national rock coverage is below 0.90.** Expect the tick covariate off for every rock-family cell, with no coverage or missingness term entering the fit (MNAR, X-conflict 5), and the design identical to a run with ticks disabled for that family (Task 8 `test_rock_ticks_gated_until_coverage`).
4. **A route whose stored row is missing, or whose registry has no active version.** Expect HTTP 404 "Unavailable" from `/safety` and `safety: null` in bulk, never a gray "insufficient" and never a number (Task 22 `test_missing_row_is_unavailable_not_insufficient`).
5. **An `/rollback` comment from someone other than the owner, or when `previous` is the kernel row.** Expect a refusal comment and no status change (Task 19 `test_rollback_refuses_kernel_and_non_owner`, Task 20 workflow guard).
6. **A Gamma posterior with tiny shape** (κ → 0 in a heterogeneous level). Expect `q05 ≤ mean ≤ q95` still to hold because of the prior-shape floor (Task 13 property test).
7. **An incident linked to a boulder route, or a point-only incident 5 km from the nearest scorable area.** Expect the boulder incident dropped `boulder_route`, and the far one placed at state level only (Task 6 tests).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/db/owner/schema_model_phase3.sql` | Create | Owner creates schema `model` (migrator lacks CREATE on the database). |
| `backend/alembic/versions/0012_model_registry.py` | Create | `model.registry`, `model.route_scores`. |
| `backend/app/models/model_registry.py`, `backend/app/models/__init__.py` | Create / Modify | ORM mirrors. |
| `backend/db/roles/trainer_login_phase3.sql` | Create | Owner gives `trainer` LOGIN plus a password (refuses a rerun). |
| `backend/db/roles/grants_phase3.sql`, `verify_roles_phase3.sql`, `backend/tests/test_roles_phase3.py` | Create | Phase 3 lockstep grants. |
| `backend/db/roles/verify_roles_phase2.sql`, `backend/tests/test_roles_phase2.py` | Modify | Trainer checks hand over to Phase 3; `weather` rows leave in PR 3-4. |
| `backend/scripts/write_role_url.py` | Modify | `ROLES` gains `trainer`. |
| `backend/app/config.py`, `.env.example` | Modify | `TRAINER_DATABASE_URL`, `HEALTHCHECKS_TRAIN_URL`, `HEALTHCHECKS_SCORES_URL`. |
| `backend/app/pipelines/db.py` | Modify | `verified_connect_args(url, var=…)` names the variable in its errors. |
| `backend/app/tasks/safety_computation_optimized.py` | Modify (3-0), delete (3-4) | Purge guard for the backtest window; later the whole kernel task goes. |
| `backend/app/ml/__init__.py`, `cells.py`, `db.py` | Create | Vocabulary; the trainer engine. |
| `backend/app/ml/bundle.py`, `registry.py` | Create | Bundle bytes; registry SQL (insert, promote, rollback, retention). |
| `backend/app/ml/incidents.py`, `dataset.py`, `features.py`, `folds.py` | Create | Placement, SQL readers, assembly, design matrix, CV folds. |
| `backend/app/ml/glm.py`, `archetypes.py`, `eb.py`, `posterior.py`, `explain.py`, `model.py` | Create | The model. |
| `backend/app/ml/metrics.py`, `backtest.py`, `kernel_adapter.py`, `tuning.py`, `promote.py`, `materialize.py`, `train.py`, `fixtures.py` | Create | Evaluation, gate, CLI, synthetic data. |
| `backend/app/ml/kernel_ref/` | Create (3-4, `git mv`) | Frozen kernel math, used only by `kernel_adapter`. |
| `.github/workflows/model-train.yml`, `model-rollback.yml`, `ci.yml` | Create / Modify | Training, rollback, the CI fixture backtest. |
| `backend/pyproject.toml`, `backend/uv.lock` | Modify | `train` group (scipy, hypothesis in dev); mypy allowlist. |
| `backend/app/services/model_scores.py` | Create | Stored-score reads for the API. |
| `backend/app/api/v1/mp_routes.py`, `predict.py`, `backend/app/schemas/mp_route.py`, `backend/app/utils/cache.py`, `backend/app/celery_app.py`, `backend/app/tasks/scores_heartbeat.py` | Modify / Create | Serving on stored scores; the heartbeat replaces the nightly kernel. |
| `backend/alembic/versions/0013_drop_legacy_weather.py` | Create | Hard MVP-1 gate. |
| `backend/app/pipelines/r6_verify.py`, `backend/app/models/weather.py`, `tests/test_r6_verify.py` | Delete (3-4) | The `weather` readers. |
| `frontend/src/services/api.ts`, `frontend/src/utils/riskUtils.ts`, `frontend/src/components/{MapView,RouteAnalyticsModal,RiskLegend}.jsx`, `frontend/src/hooks/useRouteSafety.ts` | Modify | The display contract. |
| tests under `backend/tests/ml/`, `backend/tests/`, `backend/tests/verify/` | Create | Per task. |
| `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`, `docs/model-card.md`, `docs/superpowers/plans/2026-10-12-phase4-users-incident-reporting.md` | Modify / Create | Docs; Phase 4 renumbering. |

## Pre-flight conflict ledger

| Task A | Task B | Shared interface | Resolution |
|---|---|---|---|
| 1 | 3, 19–22 | `model.registry` / `model.route_scores` columns and CHECKs | Frozen in Task 1. |
| 2 | 19, 20, 22 | `ModelBundle(version, arrays, manifest)`, `to_bytes`/`from_bytes`, `sha256` | Frozen in Task 2. |
| 5 | all | `TYPE_GROUPS`, `G_INDEX`, `FAMILY_OF`, `STATE_GROUP_OF`, `region_key` | Frozen in Task 5. |
| 6, 7 | 8, 15, 17 | `Placement`, `place`, `window_fraction`, `CellData`, `Counts`, `count_incidents` | Frozen in Tasks 6–7. |
| 8 | 12–15, 22 | `Design(X, names, penalty, nonneg, center, scale)`, `build_design`, `TickPolicy` | Frozen in Task 8. |
| 10–14 | 15 | `fit_poisson`, `kmeans`, `nearest_distance`, `fit_eb`, `EbFit.predict`, `posterior_rows`, `explain` | Frozen per task. |
| 15 | 17–20 | `fit_model(cells, counts, params) -> Fitted`, `Fitted.predict(cells, counts_train) -> CellPrediction` | Frozen in Task 15. |
| 16 | 17–19 | metric function names and `MetricsReport` keys | Frozen in Task 16. |
| 22 | 27–29 | API fields `confidence`, `score_range`, `model_version`, `evidence`, `insufficient_reason` | Additive; frozen in Task 22. |
| 3 | exposure plan Task 4 | `trainer` on `internal.exposure_index` | Granted here (X-conflict 1). |
| 24 | plan 3 (R6) | `public.weather` | Dropped here; `grants_phase2.sql` loses its `weather` grant in the same PR. |

---

# PR 3-0 — `feat/p3-registry-trainer`

### Task 1: Schema `model`, migration `0012_model_registry`, ORM

**Files:**
- Create: `backend/db/owner/schema_model_phase3.sql`, `backend/alembic/versions/0012_model_registry.py`, `backend/app/models/model_registry.py`, `backend/tests/test_migration_0012.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml` (models mypy block)

**Interfaces:**
- Consumes: `0011_exposure_index`; `canonical_routes.route_id`.
- Produces (DB) `[default pending: DP1]` (schema `model`, not `public`: Phase 1's `verify_roles.sql` requires `app` to write no public table except `historical_predictions`, and here `trainer` writes and `app` only reads):
  - `model.registry`:

    | Column | Type |
    |---|---|
    | `version` | text PK |
    | `family` | text CHECK IN (`kernel`, `eb_v1`) |
    | `created_at` | timestamptz default now() |
    | `data_hash` | text NOT NULL |
    | `train_start`, `data_cutoff` | date NOT NULL |
    | `exposure_as_of` | date NULL |
    | `metrics` | jsonb NOT NULL default `'{}'` |
    | `thresholds` | jsonb NULL |
    | `model_card` | jsonb NOT NULL default `'{}'` |
    | `bundle` | bytea NULL |
    | `bundle_sha256` | text NULL |
    | `bundle_bytes` | integer NULL |
    | `status` | text CHECK IN (`candidate`, `active`, `previous`, `rejected`, `retired`) |
    | `promoted_at` | timestamptz NULL |
    | `promoted_by` | text NULL CHECK IN (`auto`, `owner`) |
    | `parent_version` | text NULL FK → registry |
    | `gate_result`, `diff_report` | jsonb NULL |
    | `scores_rows` | integer NULL |

    CHECKs:
    - the three bundle columns are all NULL or all set;
    - an `eb_v1` row in `active`/`previous` has a bundle and `scores_rows > 0`;
    - `status IN ('active','previous','retired') = (promoted_at IS NOT NULL)` for non-kernel rows.

    Partial unique indexes `ux_registry_one_active` and `ux_registry_one_previous` on `((true))`.
  - `model.route_scores`:

    | Column | Type |
    |---|---|
    | `model_version` | text FK → registry RESTRICT |
    | `route_id` | bigint FK → `canonical_routes` |
    | `month` | smallint 1..12 |
    | `type_group` | text in the six groups |
    | `area_id` | bigint NOT NULL |
    | `insufficient` | bool |
    | `insufficient_reason` | text NULL CHECK IN (`no_evidence`) |
    | `rr_mean`, `rr_q05`, `rr_q95` | real NULL |
    | `percentile`, `pct_q05`, `pct_q95` | real NULL, 0..100 |
    | `conf_level` | text CHECK IN (`high`, `medium`, `low`, `insufficient`) |
    | `n_eff_geo`, `n_eff_feature` | real NULL |
    | `per10k_mean`, `per10k_q05`, `per10k_q95` | real NULL |
    | `per10k_reason` | text NULL |

    PK `(model_version, route_id, month)`. CHECKs:
    - `insufficient = (conf_level = 'insufficient')`;
    - `insufficient = (insufficient_reason IS NOT NULL)`;
    - the six rr/percentile columns are all NULL exactly when `insufficient`;
    - `0 < rr_q05 <= rr_mean <= rr_q95`;
    - `pct_q05 <= percentile <= pct_q95`;
    - per10k is all-or-none, and `(per10k_mean IS NULL) = (per10k_reason IS NOT NULL OR insufficient)`.
  - Both downgrades refuse while any row exists.
- Produces (Python): `app.models.model_registry.{ModelRegistry, RouteScore}`.

- [ ] **Step 1: Write the failing test.** `backend/tests/test_migration_0012.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
PIN = "0012_model_registry"
SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000110, 'Fixture Crag', 900000100, 40.2, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000001, 'Fixture Route', 900000110, 'Trad');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  (900000110, 900000100, '900000100.900000110', 2, 'us', 'CO', 40.2, -105.3, 'own', 900000110,
   '40.20000:-105.30000', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000001, 900000110, 'trad', false, '{trad}', 'rt-mp-v3', true, NULL, 'x', 'v');
INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status)
  VALUES ('k0', 'kernel', 'h', '1990-01-01', '2026-10-01', 'candidate');
"""
OK_ROW = ("INSERT INTO model.route_scores (model_version, route_id, month, type_group, area_id, insufficient, "
          "insufficient_reason, rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95, conf_level, n_eff_geo, "
          "n_eff_feature, per10k_mean, per10k_q05, per10k_q95, per10k_reason) VALUES "
          "('k0', 900000001, {m}, 'trad', 900000110, {ins}, {reason}, {rr}, {pct}, '{conf}', 1, 2, "
          "NULL, NULL, NULL, {p10reason})")


def _row(m=1, ins="false", reason="NULL", rr="1.0, 0.5, 2.0", pct="40, 20, 60", conf="medium",
         p10reason="'ticks_incomplete'"):
    return OK_ROW.format(m=m, ins=ins, reason=reason, rr=rr, pct=pct, conf=conf, p10reason=p10reason)


def test_0012_route_scores_checks():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, _row())
        run_sql(name, _row(m=2, ins="true", reason="'no_evidence'", rr="NULL, NULL, NULL", pct="NULL, NULL, NULL",
                           conf="insufficient", p10reason="NULL"))
        bad = [
            {"rr": "1.0, 1.5, 2.0"},                       # q05 above mean
            {"rr": "0, 0, 0"},                              # a zero rate
            {"pct": "40, 50, 60"},                          # pct_q05 above percentile
            {"ins": "true", "reason": "'no_evidence'"},     # insufficient with numbers
            {"conf": "insufficient"},                       # level without the flag
            {"p10reason": "NULL"},                          # per10k neither present nor explained
        ]
        for i, over in enumerate(bad, start=3):
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, _row(m=i, **over))


def test_0012_one_active_one_previous():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, "UPDATE model.registry SET status = 'active', promoted_at = now() WHERE version = 'k0'")
        run_sql(name, "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, "
                      "promoted_at) VALUES ('k1', 'kernel', 'h', '1990-01-01', '2026-10-01', 'retired', now())")
        with pytest.raises(asyncpg.UniqueViolationError):
            run_sql(name, "UPDATE model.registry SET status = 'active' WHERE version = 'k1'")


def test_0012_eb_active_needs_bundle_and_scores():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, status, "
                          "promoted_at) VALUES ('e1', 'eb_v1', 'h', '1990-01-01', '2026-10-01', 'active', now())")


def test_0012_models_match_at_head():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))


def test_0012_downgrade_refuses_while_rows_exist():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        with pytest.raises(RuntimeError, match="refusing to downgrade 0012"):
            command.downgrade(_alembic_cfg(name), "0011_exposure_index")
        run_sql(name, "DELETE FROM model.registry")
        command.downgrade(_alembic_cfg(name), "0011_exposure_index")
        assert fetch(name, "SELECT to_regclass('model.registry')") == [(None,)]
```

- [ ] **Step 2: Run it to confirm it fails.** `cd backend && uv run pytest tests/test_migration_0012.py -q` → FAIL (no revision `0012_model_registry`).

- [ ] **Step 3: Implement.** `backend/db/owner/schema_model_phase3.sql`:

```sql
-- Phase 3 (DP1): the model schema. Run once as the owner before `alembic upgrade 0012_model_registry`;
-- migrator cannot CREATE schemas (verify_roles.sql asserts it).
\set ON_ERROR_STOP on
CREATE SCHEMA IF NOT EXISTS model AUTHORIZATION migrator;
SET ROLE migrator;
REVOKE ALL ON SCHEMA model FROM PUBLIC;
RESET ROLE;
\echo 'schema model owned by migrator'
```

`backend/alembic/versions/0012_model_registry.py`:

```python
"""Phase 3 model registry and stored scores, in schema model (DP1): trainer writes, app and analyst
read, and Phase 1's public-schema write check stays untouched. Scores are per route x month per
version (DP2): with CM = 1 in MVP-1 a daily table would repeat each month's row ~30 times."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_model_registry"
down_revision = "0011_exposure_index"
branch_labels = None
depends_on = None

TYPE_GROUPS = "('sport', 'trad', 'rock', 'alpine', 'ice', 'mixed')"
STATUSES = "('candidate', 'active', 'previous', 'rejected', 'retired')"


def _ensure_model_schema() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT to_regnamespace('model')").scalar() is not None:
        return
    if not bind.exec_driver_sql("SELECT has_database_privilege(current_user, current_database(), 'CREATE')").scalar():
        raise RuntimeError("schema model does not exist: run backend/db/owner/schema_model_phase3.sql as the owner first")
    op.execute("CREATE SCHEMA model")


def upgrade() -> None:
    _ensure_model_schema()
    op.create_table(
        "registry",
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("family", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("data_hash", sa.Text(), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("data_cutoff", sa.Date(), nullable=False),
        sa.Column("exposure_as_of", sa.Date(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(), nullable=True),
        sa.Column("model_card", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("bundle", sa.LargeBinary(), nullable=True),
        sa.Column("bundle_sha256", sa.Text(), nullable=True),
        sa.Column("bundle_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_by", sa.Text(), nullable=True),
        sa.Column("parent_version", sa.Text(), sa.ForeignKey("model.registry.version"), nullable=True),
        sa.Column("gate_result", postgresql.JSONB(), nullable=True),
        sa.Column("diff_report", postgresql.JSONB(), nullable=True),
        sa.Column("scores_rows", sa.Integer(), nullable=True),
        sa.CheckConstraint("family IN ('kernel', 'eb_v1')", name="registry_family_check"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="registry_status_check"),
        sa.CheckConstraint("promoted_by IS NULL OR promoted_by IN ('auto', 'owner')", name="registry_promoted_by_check"),
        sa.CheckConstraint("num_nulls(bundle, bundle_sha256, bundle_bytes) IN (0, 3)", name="registry_bundle_check"),
        sa.CheckConstraint(
            "family = 'kernel' OR status NOT IN ('active', 'previous') OR (bundle IS NOT NULL AND scores_rows > 0)",
            name="registry_servable_check"),
        sa.CheckConstraint(
            "family = 'kernel' OR (status IN ('active', 'previous', 'retired')) = (promoted_at IS NOT NULL)",
            name="registry_promoted_at_check"),
        schema="model",
    )
    op.create_index("ux_registry_one_active", "registry", [sa.text("(true)")], unique=True, schema="model",
                    postgresql_where=sa.text("status = 'active'"))
    op.create_index("ux_registry_one_previous", "registry", [sa.text("(true)")], unique=True, schema="model",
                    postgresql_where=sa.text("status = 'previous'"))
    real = sa.REAL()
    op.create_table(
        "route_scores",
        sa.Column("model_version", sa.Text(), sa.ForeignKey("model.registry.version", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("route_id", sa.BigInteger(), sa.ForeignKey("canonical_routes.route_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("area_id", sa.BigInteger(), nullable=False),
        sa.Column("insufficient", sa.Boolean(), nullable=False),
        sa.Column("insufficient_reason", sa.Text(), nullable=True),
        *(sa.Column(c, real, nullable=True) for c in ("rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05",
                                                       "pct_q95")),
        sa.Column("conf_level", sa.Text(), nullable=False),
        *(sa.Column(c, real, nullable=True) for c in ("n_eff_geo", "n_eff_feature", "per10k_mean", "per10k_q05",
                                                       "per10k_q95")),
        sa.Column("per10k_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("model_version", "route_id", "month"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="route_scores_month_check"),
        sa.CheckConstraint(f"type_group IN {TYPE_GROUPS}", name="route_scores_type_group_check"),
        sa.CheckConstraint("insufficient_reason IS NULL OR insufficient_reason IN ('no_evidence')",
                           name="route_scores_reason_check"),
        sa.CheckConstraint("conf_level IN ('high', 'medium', 'low', 'insufficient')", name="route_scores_conf_check"),
        sa.CheckConstraint("insufficient = (conf_level = 'insufficient')", name="route_scores_conf_flag_check"),
        sa.CheckConstraint("insufficient = (insufficient_reason IS NOT NULL)", name="route_scores_reason_flag_check"),
        sa.CheckConstraint(
            "CASE WHEN insufficient THEN num_nonnulls(rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95) = 0 "
            "ELSE num_nulls(rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95) = 0 END",
            name="route_scores_numbers_check"),
        sa.CheckConstraint("rr_q05 IS NULL OR (0 < rr_q05 AND rr_q05 <= rr_mean AND rr_mean <= rr_q95)",
                           name="route_scores_rr_order_check"),
        sa.CheckConstraint(
            "percentile IS NULL OR (0 <= pct_q05 AND pct_q05 <= percentile AND percentile <= pct_q95 AND pct_q95 <= 100)",
            name="route_scores_pct_order_check"),
        sa.CheckConstraint("num_nulls(per10k_mean, per10k_q05, per10k_q95) IN (0, 3)", name="route_scores_per10k_check"),
        sa.CheckConstraint("(per10k_mean IS NULL) = (per10k_reason IS NOT NULL OR insufficient)",
                           name="route_scores_per10k_reason_check"),
        schema="model",
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("model.route_scores", "model.registry"):
        n = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if n:
            raise RuntimeError(f"refusing to downgrade 0012: {table} has {n} rows")
    op.drop_table("route_scores", schema="model")
    op.drop_index("ux_registry_one_previous", "registry", schema="model")
    op.drop_index("ux_registry_one_active", "registry", schema="model")
    op.drop_table("registry", schema="model")
```

`backend/app/models/model_registry.py` mirrors both tables column for column. It uses the same `CheckConstraint` names and texts, the two partial unique `Index(..., unique=True, postgresql_where=text("status = 'active'"))` definitions, and `{"schema": "model"}`. `command.check` in the test fails on any drift. Register it in `app/models/__init__.py` with `from app.models import model_registry  # noqa: F401  (Phase 3, 0012)`. Add `"app.models.model_registry"` to the models mypy block.

`tests/pgtest.migrated_db` creates databases as a superuser, so `_ensure_model_schema` creates the schema itself there. That matches the 0004 pattern for `internal`.

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/test_migration_0012.py tests/test_migrations.py -q && uv run mypy` → PASS.

- [ ] **Step 5: Commit.** `git add backend/db/owner/schema_model_phase3.sql backend/alembic/versions/0012_model_registry.py backend/app/models/ backend/pyproject.toml backend/tests/test_migration_0012.py && git commit -m "feat(db): 0012 model.registry and model.route_scores"`

---

### Task 2: Bundle bytes, registry SQL, trainer engine, settings

**Files:**
- Create: `backend/app/ml/__init__.py` (empty docstring module), `backend/app/ml/bundle.py`, `backend/app/ml/registry.py`, `backend/app/ml/db.py`, `backend/tests/ml/__init__.py`, `backend/tests/ml/test_bundle.py`, `backend/tests/ml/test_registry.py`
- Modify: `backend/app/pipelines/db.py`, `backend/app/config.py`, `.env.example`, `backend/pyproject.toml` (strict mypy: `app.ml`, `app.ml.bundle`, `app.ml.registry`, `app.ml.db`)

**Interfaces:**
- Produces:
  - `ModelBundle(version: str, family: str, arrays: Mapping[str, NDArray], manifest: Mapping[str, object])` (frozen), with:
    - `.to_bytes() -> bytes`: a deterministic zip of `<name>.npy` plus `manifest.json`, fixed timestamps, sorted names;
    - `ModelBundle.from_bytes(b) -> ModelBundle`;
    - `sha256(b) -> str`.

    The format is numpy-only, so serving needs no pyarrow `[default pending: DP7]`.
  - `registry`:
    - `insert_candidate(conn, *, version, family, data_hash, train_start, data_cutoff, exposure_as_of, metrics, thresholds, model_card, bundle: bytes | None, parent_version) -> None`;
    - `set_gate(conn, version, gate_result, diff_report, scores_rows) -> None`;
    - `reject(conn, version) -> None`;
    - `promote(conn, version, *, by: Literal["auto","owner"]) -> str | None` (returns the version that became `previous`);
    - `rollback(conn) -> tuple[str, str]` (retired, reactivated);
    - `active(conn) -> ActiveVersion | None`;
    - `load_bundle(conn, version) -> ModelBundle`;
    - `prune(conn, *, today, keep_days=90) -> PruneReport`.
  - `ml.db.trainer_engine() -> AsyncEngine` (refuses without `TRAINER_DATABASE_URL`, refuses the `-pooler` host, verify-full TLS).
  - `pipelines.db.verified_connect_args(url, var="INGEST_DATABASE_URL")`.
  - Settings: `TRAINER_DATABASE_URL: str | None = None`, `HEALTHCHECKS_TRAIN_URL: str | None = None`, `HEALTHCHECKS_SCORES_URL: str | None = None`.

- [ ] **Step 1: Write the failing tests.** `backend/tests/ml/test_bundle.py`:

```python
import numpy as np
import pytest

from app.ml.bundle import ModelBundle, sha256


def _bundle() -> ModelBundle:
    return ModelBundle(version="eb-2026-10-15", family="eb_v1",
                       arrays={"coef": np.array([0.5, -1.0]), "g": np.array([0, 5], dtype=np.int8)},
                       manifest={"names": ["intercept", "x"], "phi": 1.5})


def test_round_trip_and_determinism():
    b = _bundle()
    raw = b.to_bytes()
    assert raw == _bundle().to_bytes()
    back = ModelBundle.from_bytes(raw)
    assert back.version == b.version and back.manifest == b.manifest
    np.testing.assert_array_equal(back.arrays["coef"], b.arrays["coef"])
    assert back.arrays["g"].dtype == np.int8
    assert len(sha256(raw)) == 64


def test_refuses_object_arrays_and_nonfinite_manifest():
    with pytest.raises(ValueError, match="object"):
        ModelBundle(version="v", family="eb_v1", arrays={"x": np.array(["a"], dtype=object)}, manifest={}).to_bytes()
    with pytest.raises(ValueError, match="finite"):
        ModelBundle(version="v", family="eb_v1", arrays={}, manifest={"phi": float("nan")}).to_bytes()


def test_from_bytes_refuses_extra_members():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", '{"version": "v", "family": "eb_v1", "arrays": [], "manifest": {}}')
        z.writestr("evil.py", "print(1)")
    with pytest.raises(ValueError, match="unexpected member"):
        ModelBundle.from_bytes(buf.getvalue())
```

`backend/tests/ml/test_registry.py` (`requires_pg`; seeds 0012 with a kernel row via `migrated_db(revision="0012_model_registry", seed_sql=…)` reusing Task 1's `SEED` imported from `tests.test_migration_0012`):

```python
from datetime import date

import pytest

from app.ml import registry
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, run_sql
from tests.test_migration_0012 import SEED

pytestmark = requires_pg
ROW = ("INSERT INTO model.route_scores (model_version, route_id, month, type_group, area_id, insufficient, "
       "insufficient_reason, rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95, conf_level, n_eff_geo, "
       "n_eff_feature, per10k_mean, per10k_q05, per10k_q95, per10k_reason) VALUES "
       "('{v}', 900000001, 1, 'trad', 900000110, false, NULL, 1, 0.5, 2, 40, 20, 60, 'medium', 1, 1, "
       "NULL, NULL, NULL, 'ticks_incomplete')")


def _candidate(name: str, version: str) -> None:
    async def go(conn):
        await registry.insert_candidate(conn, version=version, family="eb_v1", data_hash="h",
                                        train_start=date(1990, 1, 1), data_cutoff=date(2026, 10, 1),
                                        exposure_as_of=None, metrics={}, thresholds={}, model_card={},
                                        bundle=b"zip", parent_version=None)
    run_on_conn(name, go)
    run_sql(name, ROW.format(v=version))
    run_on_conn(name, lambda c: registry.set_gate(c, version, {"passed": True}, {}, 1))


def test_promote_then_rollback_flips_atomically():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, "UPDATE model.registry SET status = 'active', promoted_at = now() WHERE version = 'k0'")
        _candidate(name, "e1")
        prev = run_on_conn(name, lambda c: registry.promote(c, "e1", by="auto"))
        assert prev is None  # DP15: the kernel row retires, it never becomes previous
        assert fetch(name, "SELECT version, status FROM model.registry ORDER BY version") == [
            ("e1", "active"), ("k0", "retired")]
        _candidate(name, "e2")
        assert run_on_conn(name, lambda c: registry.promote(c, "e2", by="auto")) == "e1"
        assert run_on_conn(name, registry.rollback) == ("e2", "e1")
        assert dict(fetch(name, "SELECT version, status FROM model.registry")) == {
            "e1": "active", "e2": "retired", "k0": "retired"}


def test_rollback_refuses_without_servable_previous():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_sql(name, "UPDATE model.registry SET status = 'active', promoted_at = now() WHERE version = 'k0'")
        _candidate(name, "e1")
        run_on_conn(name, lambda c: registry.promote(c, "e1", by="auto"))
        with pytest.raises(registry.RollbackRefused, match="no previous version"):
            run_on_conn(name, registry.rollback)
        assert fetch(name, "SELECT status FROM model.registry WHERE version = 'e1'") == [("active",)]


def test_promote_refuses_unscored_or_ungated():
    with migrated_db(revision="0012_model_registry", seed_sql=SEED) as name:
        run_on_conn(name, lambda c: registry.insert_candidate(
            c, version="e9", family="eb_v1", data_hash="h", train_start=date(1990, 1, 1),
            data_cutoff=date(2026, 10, 1), exposure_as_of=None, metrics={}, thresholds={}, model_card={}, bundle=b"z", parent_version=None))
        with pytest.raises(registry.PromotionRefused, match="gate"):
            run_on_conn(name, lambda c: registry.promote(c, "e9", by="auto"))
```

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/ml -q` → FAIL (`ModuleNotFoundError: app.ml`).

- [ ] **Step 3: Implement.** `backend/app/ml/bundle.py`:

```python
"""Model bundle bytes (P3 M3): a deterministic zip of .npy arrays plus manifest.json, stored in
model.registry.bundle. numpy-only (DP7) so the API image needs no Parquet stack; allow_pickle is
never enabled, so loading a bundle cannot execute code."""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

_EPOCH = (1980, 1, 1, 0, 0, 0)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check_finite(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"manifest {path} is not finite")
    if isinstance(value, Mapping):
        for k, v in value.items():
            _check_finite(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_finite(v, f"{path}[{i}]")


@dataclass(frozen=True)
class ModelBundle:
    version: str
    family: str
    arrays: Mapping[str, NDArray[Any]] = field(default_factory=dict)
    manifest: Mapping[str, Any] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        _check_finite(dict(self.manifest), "manifest")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name in sorted(self.arrays):
                arr = np.ascontiguousarray(self.arrays[name])
                if arr.dtype == object:
                    raise ValueError(f"array {name} has dtype object; bundles hold numeric arrays only")
                member = io.BytesIO()
                np.save(member, arr, allow_pickle=False)
                z.writestr(zipfile.ZipInfo(f"{name}.npy", date_time=_EPOCH), member.getvalue())
            head = {"version": self.version, "family": self.family, "arrays": sorted(self.arrays),
                    "manifest": dict(self.manifest)}
            z.writestr(zipfile.ZipInfo("manifest.json", date_time=_EPOCH),
                       json.dumps(head, sort_keys=True, separators=(",", ":")))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, raw: bytes) -> ModelBundle:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            head = json.loads(z.read("manifest.json"))
            expected = {"manifest.json", *(f"{n}.npy" for n in head["arrays"])}
            for member in z.namelist():
                if member not in expected:
                    raise ValueError(f"unexpected member in bundle: {member}")
            arrays = {n: np.load(io.BytesIO(z.read(f"{n}.npy")), allow_pickle=False) for n in head["arrays"]}
        return cls(version=head["version"], family=head["family"], arrays=arrays, manifest=head["manifest"])
```

`backend/app/ml/registry.py`:

```python
"""model.registry state machine (P3 M9). Every transition is one transaction and one statement
set; the partial unique indexes make a second active or previous row impossible."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ml.bundle import ModelBundle, sha256


class PromotionRefused(RuntimeError):
    pass


class RollbackRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class ActiveVersion:
    version: str
    family: str
    bundle_sha256: str | None


@dataclass(frozen=True)
class PruneReport:
    scores_deleted: int
    bundles_nulled: int


async def insert_candidate(conn: AsyncConnection, *, version: str, family: str, data_hash: str, train_start: date,
                           data_cutoff: date, exposure_as_of: date | None, metrics: dict[str, Any],
                           thresholds: dict[str, Any] | None, model_card: dict[str, Any], bundle: bytes | None,
                           parent_version: str | None) -> None:
    await conn.execute(text(
        "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, exposure_as_of, metrics, "
        "thresholds, model_card, bundle, bundle_sha256, bundle_bytes, status, parent_version) VALUES "
        "(:version, :family, :data_hash, :train_start, :data_cutoff, :exposure_as_of, CAST(:metrics AS jsonb), "
        "CAST(:thresholds AS jsonb), CAST(:model_card AS jsonb), :bundle, :sha, :nbytes, 'candidate', :parent)"),
        {"version": version, "family": family, "data_hash": data_hash, "train_start": train_start,
         "data_cutoff": data_cutoff, "exposure_as_of": exposure_as_of, "metrics": json.dumps(metrics),
         "thresholds": None if thresholds is None else json.dumps(thresholds), "model_card": json.dumps(model_card),
         "bundle": bundle, "sha": None if bundle is None else sha256(bundle),
         "nbytes": None if bundle is None else len(bundle), "parent": parent_version})


async def set_gate(conn: AsyncConnection, version: str, gate_result: dict[str, Any], diff_report: dict[str, Any],
                   scores_rows: int) -> None:
    await conn.execute(text(
        "UPDATE model.registry SET gate_result = CAST(:g AS jsonb), diff_report = CAST(:d AS jsonb), "
        "scores_rows = :n WHERE version = :v AND status = 'candidate'"),
        {"g": json.dumps(gate_result), "d": json.dumps(diff_report), "n": scores_rows, "v": version})


async def reject(conn: AsyncConnection, version: str) -> None:
    await conn.execute(text("UPDATE model.registry SET status = 'rejected' WHERE version = :v AND status = 'candidate'"),
                       {"v": version})


async def promote(conn: AsyncConnection, version: str, *, by: Literal["auto", "owner"]) -> str | None:
    row = (await conn.execute(text(
        "SELECT status, family, gate_result ->> 'passed', scores_rows FROM model.registry WHERE version = :v "
        "FOR UPDATE"), {"v": version})).one_or_none()
    if row is None:
        raise PromotionRefused(f"no such version {version}")
    status, family, passed, rows = row
    if family == "kernel":
        raise PromotionRefused("the kernel family is never re-promoted (DP15)")
    if by == "auto" and (status != "candidate" or passed != "true"):
        raise PromotionRefused(f"{version} has not passed the gate (status {status})")
    if not rows:
        raise PromotionRefused(f"{version} has no materialized scores")
    await conn.execute(text("UPDATE model.registry SET status = 'retired' WHERE status = 'previous'"))
    old = (await conn.execute(text(
        "UPDATE model.registry SET status = CASE WHEN family = 'kernel' THEN 'retired' ELSE 'previous' END "
        "WHERE status = 'active' RETURNING version, family"))).one_or_none()
    await conn.execute(text(
        "UPDATE model.registry SET status = 'active', promoted_at = now(), promoted_by = :by WHERE version = :v"),
        {"by": by, "v": version})
    return None if old is None or old[1] == "kernel" else str(old[0])


async def rollback(conn: AsyncConnection) -> tuple[str, str]:
    prev = (await conn.execute(text(
        "SELECT version FROM model.registry WHERE status = 'previous' AND family <> 'kernel' "
        "AND bundle IS NOT NULL FOR UPDATE"))).scalar_one_or_none()
    if prev is None:
        raise RollbackRefused("no previous version to restore; promote a specific version with workflow_dispatch")
    cur = (await conn.execute(text(
        "UPDATE model.registry SET status = 'retired' WHERE status = 'active' RETURNING version"))).scalar_one()
    await conn.execute(text(
        "UPDATE model.registry SET status = 'active', promoted_at = now(), promoted_by = 'owner' WHERE version = :v"),
        {"v": prev})
    return str(cur), str(prev)


async def active(conn: AsyncConnection) -> ActiveVersion | None:
    row = (await conn.execute(text(
        "SELECT version, family, bundle_sha256 FROM model.registry WHERE status = 'active'"))).one_or_none()
    return None if row is None else ActiveVersion(str(row[0]), str(row[1]), row[2])


async def load_bundle(conn: AsyncConnection, version: str) -> ModelBundle:
    row = (await conn.execute(text("SELECT bundle, bundle_sha256 FROM model.registry WHERE version = :v"),
                              {"v": version})).one()
    raw, digest = bytes(row[0]), row[1]
    if sha256(raw) != digest:
        raise RuntimeError(f"bundle of {version} does not match its sha256")
    return ModelBundle.from_bytes(raw)


async def prune(conn: AsyncConnection, *, today: date, keep_days: int = 90) -> PruneReport:
    """M3 retention: scores and bundle bytes of rejected/retired/stale candidate versions older
    than keep_days go; metadata rows stay. Active and previous are never touched."""
    stale = ("status IN ('rejected', 'retired') OR (status = 'candidate' AND created_at < "
             "CAST(:today AS date) - make_interval(days => :keep))")
    deleted = (await conn.execute(text(
        f"DELETE FROM model.route_scores s USING model.registry r WHERE s.model_version = r.version "
        f"AND r.created_at < CAST(:today AS date) - make_interval(days => :keep) AND ({stale})"),
        {"today": today, "keep": keep_days})).rowcount
    nulled = (await conn.execute(text(
        f"UPDATE model.registry SET bundle = NULL, bundle_sha256 = NULL, bundle_bytes = NULL "
        f"WHERE bundle IS NOT NULL AND created_at < CAST(:today AS date) - make_interval(days => :keep) AND ({stale})"),
        {"today": today, "keep": keep_days})).rowcount
    return PruneReport(int(deleted or 0), int(nulled or 0))
```

The registry rows never get `DELETE`, so the FK from `route_scores` (RESTRICT) can never block a prune.

`app/pipelines/db.py`: give `verified_connect_args` a keyword `var: str = "INGEST_DATABASE_URL"`, and use `{var}` in its three messages. `require_direct_host` gets the same keyword. The existing ingest calls stay byte-identical in behaviour.

`backend/app/ml/db.py`:

```python
"""The trainer role's engine (P3 M2). Training reads clean views and writes only model tables."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.pipelines.db import require_direct_host, verified_connect_args

VAR = "TRAINER_DATABASE_URL"


def trainer_engine() -> AsyncEngine:
    url = settings.TRAINER_DATABASE_URL
    if not url:
        raise SystemExit(f"{VAR} is not set (the trainer role's URL); see DEPLOYMENT.md")
    require_direct_host(url, var=VAR)
    return create_async_engine(url, poolclass=NullPool, connect_args=verified_connect_args(url, var=VAR))
```

`config.py` gets three fields, each with a one-line comment:
- `TRAINER_DATABASE_URL`: trainer role, training job only, never a service;
- `HEALTHCHECKS_TRAIN_URL`: 35-day dead-man check for the monthly training (P3:204);
- `HEALTHCHECKS_SCORES_URL`: the daily stored-score heartbeat (Task 23).

`.env.example` gets `TRAINER_DATABASE_URL=`, `HEALTHCHECKS_TRAIN_URL=` and `HEALTHCHECKS_SCORES_URL=`.

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/ml tests/test_env_example_parity.py tests/test_pipelines_db.py -q && uv run mypy` → PASS.

- [ ] **Step 5: Commit.** `git add backend/app/ml backend/app/pipelines/db.py backend/app/config.py .env.example backend/pyproject.toml backend/tests/ml && git commit -m "feat(ml): bundle bytes, registry state machine, trainer engine"`

---

### Task 3: `trainer` LOGIN and Phase 3 lockstep grants

**Files:**
- Create: `backend/db/roles/trainer_login_phase3.sql`, `backend/db/roles/grants_phase3.sql`, `backend/db/roles/verify_roles_phase3.sql`, `backend/tests/test_roles_phase3.py`
- Modify: `backend/db/roles/verify_roles_phase2.sql`, `backend/tests/test_roles_phase2.py`, `backend/scripts/write_role_url.py` (`ROLES = ("migrator", "app", "ingest", "trainer")`), `backend/tests/test_write_role_url.py`

**Interfaces:**
- Produces (roles) `[default pending: DP17]`:
  - **trainer:** LOGIN, `CONNECTION LIMIT 4`, USAGE on schemas `public`, `internal` and `model`.
    - SELECT only on: `public.accidents_clean`, `public.accident_route_links`, `public.canonical_areas`, `public.canonical_routes`, `public.scorable_routes`, `public.scoring_unit_features`, `public.historical_predictions`, `internal.exposure_index`, `model.registry`, `model.route_scores`.
    - INSERT, UPDATE on `model.registry`.
    - INSERT, DELETE on `model.route_scores`.
    - Nothing else. In particular: not `accidents`, not `accidents_clean_daily`/`accident_conditions` (v2.3), and nothing else in `internal`.
  - **app:** USAGE on `model`; SELECT on `model.registry` and `model.route_scores`.
  - **analyst:** USAGE on `model`; SELECT on both.
  - **ingest:** nothing on `model`.

- [ ] **Step 1: Write the failing test.** `backend/tests/test_roles_phase3.py`. It follows `test_roles_phase2.py`'s fixtures (`role_cleanup`, `fresh_db`, `_owner_db`, `_alembic_cfg_as`, `_as`, `_denied`, `_psql`, `_require_psql`, `PASSWORDS`, `_role_url`), imported from it:

```python
import pytest
from alembic import command

from tests.test_roles_phase2 import (  # noqa: F401  (fixtures)
    PASSWORDS, _alembic_cfg_as, _as, _denied, _fetch_row, _owner_db, _psql, _require_psql, _role_url, fresh_db,
    role_cleanup, run_phase2_roles,
)

ROLES_DIR = "db/roles"
TRAINER_PASSWORD = "t" * 40
ALLOW_SELECT = ("public.accidents_clean", "public.accident_route_links", "public.canonical_areas",
                "public.canonical_routes", "public.scorable_routes", "public.scoring_unit_features",
                "public.historical_predictions", "internal.exposure_index", "model.registry", "model.route_scores")


def _phase3(fresh_db):
    owner = _owner_db(fresh_db)
    run_phase2_roles(fresh_db)  # create_roles + create_roles_phase2 + upgrade head + grants_phase2
    _psql(owner, f"{ROLES_DIR}/../owner/schema_model_phase3.sql", {})
    command.upgrade(_alembic_cfg_as(fresh_db, owner), "head")
    login = _psql(owner, f"{ROLES_DIR}/trainer_login_phase3.sql", {"TRAINER_PASSWORD": TRAINER_PASSWORD})
    assert login.returncode == 0, login.stderr
    grants = _psql(owner, f"{ROLES_DIR}/grants_phase3.sql", {})
    assert grants.returncode == 0, grants.stderr
    return owner, _role_url(fresh_db, "trainer", TRAINER_PASSWORD)


def test_trainer_reads_allowlist_and_writes_only_model(role_cleanup, fresh_db):
    _require_psql()
    owner, trainer = _phase3(fresh_db)
    for rel in ALLOW_SELECT:
        _as(trainer, f"SELECT count(*) FROM {rel}")
    for rel in ("public.accidents", "public.accidents_clean_daily", "public.accident_conditions",
                "internal.mp_tick_aggregates", "internal.route_tick_scrapes", "public.mp_ticks", "public.weather"):
        _denied(trainer, f"SELECT count(*) FROM {rel}")
    _as(trainer, "UPDATE model.registry SET metrics = metrics WHERE false")
    _as(trainer, "DELETE FROM model.route_scores WHERE false")
    _denied(trainer, "DELETE FROM model.registry WHERE false")
    _denied(trainer, "UPDATE model.route_scores SET rr_mean = 1 WHERE false")
    _denied(trainer, "TRUNCATE model.route_scores")
    _denied(trainer, "CREATE TABLE model.nope (x int)")
    _denied(trainer, "UPDATE historical_predictions SET risk_score = 1 WHERE false")
    app = _role_url(fresh_db, "app", PASSWORDS["app"])
    _as(app, "SELECT count(*) FROM model.registry; SELECT count(*) FROM model.route_scores")
    _denied(app, "DELETE FROM model.route_scores WHERE false")
    ingest = _role_url(fresh_db, "ingest", PASSWORDS["ingest"])
    _denied(ingest, "SELECT count(*) FROM model.registry")
    verify = _psql(owner, f"{ROLES_DIR}/verify_roles_phase3.sql", {})
    assert verify.returncode == 0, verify.stdout + verify.stderr
    verify2 = _psql(owner, f"{ROLES_DIR}/verify_roles_phase2.sql", {})
    assert verify2.returncode == 0, verify2.stdout + verify2.stderr


def test_trainer_login_refuses_rerun_and_bad_passwords(role_cleanup, fresh_db):
    _require_psql()
    owner, _ = _phase3(fresh_db)
    again = _psql(owner, f"{ROLES_DIR}/trainer_login_phase3.sql", {"TRAINER_PASSWORD": TRAINER_PASSWORD})
    assert again.returncode != 0 and "trainer can already log in" in again.stderr


def test_verify_phase3_catches_stray_grants(role_cleanup, fresh_db):
    _require_psql()
    owner, _ = _phase3(fresh_db)
    _as(owner, "GRANT SELECT ON public.accidents TO trainer; GRANT UPDATE ON model.route_scores TO trainer; "
               "GRANT INSERT ON model.route_scores TO app; GRANT SELECT ON model.registry TO ingest;")
    out = _psql(owner, f"{ROLES_DIR}/verify_roles_phase3.sql", {})
    assert out.returncode != 0
    for check in ("trainer privileges match the allowlist on public.accidents",
                  "trainer privileges match the allowlist on model.route_scores",
                  "app holds only SELECT on model.route_scores",
                  "ingest holds nothing on model.registry"):
        assert check in out.stdout, check
```

`run_phase2_roles(fresh_db)` is a new helper extracted in `test_roles_phase2.py` from the body of `test_phase2_roles_least_privilege`'s setup (create roles, upgrade, `grants_phase2.sql`). Extract it as a pure refactor in this step, and keep the original test calling it.

Change `test_roles_phase2.py` in three places:
- `test_phase2_roles_least_privilege` still asserts `TRAINER_STATE_SQL == [False, False]`. It never runs Phase 3 scripts, so that stays true.
- In `test_verify_phase2_catches_stray_grants`, delete the trainer stray lines whose grants are now legal: `accidents_clean`, `scorable_routes`, `scoring_unit_features` and `accident_route_links`. Keep `GRANT SELECT ON public.accidents TO trainer;` and `GRANT SELECT ON public.accident_conditions TO trainer;`; Phase 2's check still owns those.
- Remove the matching expected-failure strings.

- [ ] **Step 2: Run to confirm failure.** `cd backend && PATH=/opt/homebrew/opt/libpq/bin:$PATH CI=1 uv run pytest tests/test_roles_phase3.py -q` → FAIL (missing scripts).

- [ ] **Step 3: Implement.** `backend/db/roles/trainer_login_phase3.sql`:

```sql
-- Phase 3 (D13/DP17): trainer gains LOGIN for the training job (Actions secret TRAINER_DATABASE_URL).
-- Run once as the owner; the password reaches psql only via \getenv, never argv.
\set ON_ERROR_STOP on
\getenv trainer_password TRAINER_PASSWORD
SELECT (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer') AS already \gset
\if :already
  DO $$ BEGIN RAISE EXCEPTION 'trainer can already log in; do not rerun trainer_login_phase3.sql'; END $$;
\endif
SELECT length(:'trainer_password') >= 32
   AND :'trainer_password' !~ '^SCRAM-SHA-256\$'
   AND :'trainer_password' !~ '^md5[0-9a-f]{32}$' AS password_ok \gset
\if :password_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'TRAINER_PASSWORD must be a plaintext password of at least 32 characters, not a SCRAM or md5 verifier'; END $$;
\endif
ALTER ROLE trainer LOGIN CONNECTION LIMIT 4 PASSWORD :'trainer_password';
\echo 'trainer can log in'
```

`backend/db/roles/grants_phase3.sql`:

```sql
-- Phase 3 grants (idempotent). Run as migrator (or an owner holding SET on migrator) after every
-- Phase 3 migration, then verify_roles_phase3.sql. Narrow first so a rerun removes stray grants.
\set ON_ERROR_STOP on
SET ROLE migrator;
REVOKE ALL ON ALL TABLES IN SCHEMA model FROM trainer, app, analyst, ingest;
GRANT USAGE ON SCHEMA model TO trainer, app, analyst;
GRANT SELECT ON model.registry, model.route_scores TO app, analyst;
GRANT SELECT, INSERT, UPDATE ON model.registry TO trainer;
GRANT SELECT, INSERT, DELETE ON model.route_scores TO trainer;
-- Training reads (allowlist, DP17): views without narratives, the catalog, static features, the
-- kernel's history for the prospective backtest, and the exposure snapshots (X-conflict 1).
GRANT USAGE ON SCHEMA public, internal TO trainer;
GRANT SELECT ON public.accidents_clean, public.accident_route_links, public.canonical_areas,
  public.canonical_routes, public.scorable_routes, public.scoring_unit_features,
  public.historical_predictions TO trainer;
GRANT SELECT ON internal.exposure_index TO trainer;
RESET ROLE;
```

`backend/db/roles/verify_roles_phase3.sql` follows `verify_roles_phase2.sql`'s temp-table pattern and exits non-zero on any failed row. It inserts:
- `'trainer can log in'`;
- `'trainer has USAGE on public/internal/model'` (three rows);
- `'trainer cannot CREATE in model'`;
- for every relation in `public`, `internal` and `model` (minus extension-owned, per the Phase 2 file): `'trainer privileges match the allowlist on ' || rel`. The expected set comes from a `VALUES` list of `(rel, priv)` mirroring the grant file, compared per privilege over SELECT/INSERT/UPDATE/DELETE/TRUNCATE.
- for both model tables: `'app holds only SELECT on ' || rel` and `'analyst holds only SELECT on ' || rel`;
- `'ingest holds nothing on ' || rel` for every `model` relation;
- `'no default privileges grant trainer anything'`.

`verify_roles_phase2.sql`:
- delete the rows `trainer cannot log in until Phase 3` and `trainer has no USAGE on schema internal`;
- change `trainer holds no privilege on …` to `FROM rels WHERE relkind <> 'S' AND tbl NOT IN ('public.accidents_clean', 'public.accident_route_links', 'public.canonical_areas', 'public.canonical_routes', 'public.scorable_routes', 'public.scoring_unit_features', 'public.historical_predictions', 'internal.exposure_index')`, adding the comment `-- Phase 3 grants these (verify_roles_phase3.sql owns trainer from here).`

- [ ] **Step 4: Run.** `cd backend && PATH=/opt/homebrew/opt/libpq/bin:$PATH CI=1 uv run pytest tests/test_roles_phase3.py tests/test_roles_phase2.py tests/test_migrations.py tests/test_write_role_url.py -q` → PASS (no psql skips).

- [ ] **Step 5: Commit.** `git add backend/db/roles backend/scripts/write_role_url.py backend/tests/test_roles_phase3.py backend/tests/test_roles_phase2.py backend/tests/test_write_role_url.py && git commit -m "feat(roles): trainer LOGIN and Phase 3 least-privilege grants"`

---

### Task 4: Backtest-window purge guard; PR 3-0 docs; OWNER/AGENT RUNBOOK (branch, then prod)

**Files:**
- Modify: `backend/app/tasks/safety_computation_optimized.py` (purge SQL), `backend/tests/test_insufficient_data.py` (or a new `backend/tests/test_backtest_window_kept.py`), `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`

**Why:**
- The nightly deletes `historical_predictions` older than one year (`safety_computation_optimized.py:904-909`).
- The prospective backtest window 2026-02-05 → 2026-07-02 (P3:381) would start disappearing on 2027-02-05.
- The kernel nightly ends in PR 3-4, but if MVP-1 slips past February the evidence is gone `[default pending: DP14]`.

- [ ] **Step 1: Write the failing test.** `backend/tests/test_backtest_window_kept.py`:

```python
import re
from pathlib import Path

SRC = Path(__file__).parents[1] / "app" / "tasks" / "safety_computation_optimized.py"


def test_purge_keeps_the_phase3_backtest_window():
    sql = re.search(r"DELETE FROM historical_predictions(.*?)\"\"\"", SRC.read_text(), re.S).group(1)
    assert "NOT (prediction_date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02')" in sql
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** In the purge SQL, add `AND NOT (prediction_date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02')`, with the dated comment `-- 2026-10-15 (Phase 3 DP14): the prospective kernel backtest window (P3:381); keep until Phase 3 records it.`

Docs:
- **`CLAUDE.md`**: Database section gains `0012_model_registry` (`model.registry`/`model.route_scores`; needs `backend/db/owner/schema_model_phase3.sql` first; refuses downgrade while any row exists). Roles: `trainer` (Phase 3: LOGIN, the allowlist, `grants_phase3.sql` then `verify_roles_phase3.sql` after every Phase 3 migration). Commands: `uv run --group train python -m app.ml.train …` (filled in by Task 20).
- **`DEPLOYMENT.md`**: "Phase 3 roles" (the owner creates schema `model`, then runs `trainer_login_phase3.sql` with `TRAINER_PASSWORD` from `.env.trainer`, then `grants_phase3.sql`/`verify_roles_phase3.sql`; then the Actions secret).
- **`data/DATABASE_STRUCTURE.md`**: both model tables, plus the purge guard.
- **`CHANGELOG.md`**: a dated entry.

- [ ] **Step 4: Run.** `cd backend && uv run pytest tests/test_backtest_window_kept.py tests/test_insufficient_data.py -q` → PASS.

- [ ] **Step 5: Commit.** `git commit -am "fix(nightly): keep the Phase 3 backtest window; docs for 0012 and trainer"`

- [ ] **Step 6 (agent, branch rehearsal).**

```bash
cd backend && source scripts/runbook_helpers.sh
export TARGET_HOST=<neon branch host p3-0-rehearsal>   # a fresh branch of production
save_once ../data/review/p3-0/heads.txt uv run alembic heads           # expect 0012_model_registry (head)
save_once ../data/review/p3-0/current-before.txt MIG current            # expect 0011_exposure_index
```

STOP. The owner, on the branch:
- runs `psql "$OWNER_URL" -f backend/db/owner/schema_model_phase3.sql`;
- generates the trainer password with `uv run python -m scripts.write_role_url --role trainer --env-file .env.trainer --generate-password` (agents never read that file);
- runs `TRAINER_PASSWORD=… psql "$OWNER_URL" -f backend/db/roles/trainer_login_phase3.sql`.

Then the agent:

```bash
MIG upgrade 0012_model_registry && save_once ../data/review/p3-0/current-after.txt MIG current
psql "$MIGRATOR_URL" -f db/roles/grants_phase3.sql && psql "$OWNER_URL" -f db/roles/verify_roles_phase3.sql \
  && psql "$OWNER_URL" -f db/roles/verify_roles_phase2.sql && gate_pass p3_0_branch
```

`MIG`, `OWNER_URL` and `MIGRATOR_URL` are the helpers and variables `runbook_helpers.sh` already defines for Phase 2 runbooks. A failed verify row stops here, and nothing reaches prod.

- [ ] **Step 7 (owner, then agent, prod).**
  - STOP for the owner's go.
  - The owner creates the snapshot branch `pre-p3-0` from `production`, then runs the schema and trainer-login scripts on prod.
  - The agent re-targets: `export TARGET_HOST=production`, `gate_require p3_0_branch`, then the Step 6 upgrade/grants/verify block, ending in `gate_pass p3_0_prod`.
  - The owner adds the Actions secret `TRAINER_DATABASE_URL` (its direct host, `ssl=verify-full`) and the healthchecks.io check `train` (period 35 days, grace 3 days) as `HEALTHCHECKS_TRAIN_URL`.

---

# PR 3-1 — `feat/p3-training-data`

### Task 5: Cell vocabulary and hierarchy

**Files:**
- Create: `backend/app/ml/cells.py`, `backend/tests/ml/test_cells.py`
- Modify: `backend/pyproject.toml` (strict mypy `app.ml.cells`)

**Interfaces:**
- Produces:
  - `TYPE_GROUPS = ("sport","trad","rock","alpine","ice","mixed")` `[default pending: DP3]`;
  - `G_INDEX: dict[str,int]`;
  - `FAMILY_OF: dict[str, Literal["rock_family","mountain"]]`;
  - `MONTHS = tuple(range(1,13))`;
  - `STATE_GROUPS: dict[str, tuple[str,...]]` `[default pending: DP10]` and `STATE_GROUP_OF: dict[str,str]`;
  - `region_key(path: str) -> str`;
  - `TRAIN_START = date(1990, 1, 1)` `[default pending: DP6]`;
  - `NEAREST_AREA_KM = 3.0`;
  - `TRUSTED_FOR_CELLS = ("exact", "crag", "area")`.

- [ ] **Step 1: Write the failing test.**

```python
import pytest

from app.ml.cells import FAMILY_OF, STATE_GROUP_OF, STATE_GROUPS, TYPE_GROUPS, region_key
from app.pipelines.gnis import STATE_CODES


def test_six_scorable_groups_two_families():
    assert TYPE_GROUPS == ("sport", "trad", "rock", "alpine", "ice", "mixed")
    assert {FAMILY_OF[g] for g in ("sport", "trad", "rock")} == {"rock_family"}
    assert {FAMILY_OF[g] for g in ("alpine", "ice", "mixed")} == {"mountain"}


def test_state_groups_partition_every_us_code_once():
    codes = sorted(STATE_GROUP_OF)
    assert len(STATE_GROUPS) == 8
    assert sum(len(v) for v in STATE_GROUPS.values()) == len(codes)
    assert set(codes) == set(STATE_CODES.values())


def test_region_key_is_the_depth_two_prefix():
    assert region_key("900000100.900000200.900000300") == "900000100.900000200"
    assert region_key("900000100.900000200") == "900000100.900000200"
    assert region_key("900000100") == "900000100"
    with pytest.raises(ValueError):
        region_key("Colorado.Boulder")
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.**

```python
"""Phase 3 cell vocabulary on the MP catalog: six scorable type groups pooled in two families
(DP3), the MP area hierarchy (area -> region = depth-2 ancestor -> state -> national), and the
eight state groups spatial-block CV leaves out (DP10)."""

from __future__ import annotations

from datetime import date
from typing import Final, Literal

TYPE_GROUPS: Final = ("sport", "trad", "rock", "alpine", "ice", "mixed")
G_INDEX: Final = {g: i for i, g in enumerate(TYPE_GROUPS)}
FAMILY_OF: Final[dict[str, Literal["rock_family", "mountain"]]] = {
    "sport": "rock_family", "trad": "rock_family", "rock": "rock_family",
    "alpine": "mountain", "ice": "mountain", "mixed": "mountain",
}
MONTHS: Final = tuple(range(1, 13))
TRAIN_START: Final = date(1990, 1, 1)
NEAREST_AREA_KM: Final = 3.0
TRUSTED_FOR_CELLS: Final = ("exact", "crag", "area")
STATE_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "pacific_nw": ("WA", "OR", "ID", "AK"),
    "california": ("CA", "NV", "HI"),
    "southwest": ("AZ", "NM", "UT"),
    "rockies_north": ("MT", "WY", "SD", "ND", "NE"),
    "colorado": ("CO",),
    "central": ("TX", "OK", "KS", "MN", "WI", "IA", "MO", "AR", "LA", "MI", "IL", "IN", "OH"),
    "southeast": ("KY", "TN", "MS", "AL", "GA", "FL", "SC", "NC", "VA", "WV", "DC", "MD", "DE"),
    "northeast": ("PA", "NJ", "NY", "CT", "RI", "MA", "VT", "NH", "ME"),
}
STATE_GROUP_OF: Final = {s: g for g, states in STATE_GROUPS.items() for s in states}


def region_key(path: str) -> str:
    labels = path.split(".")
    if not labels or not all(label.isdigit() for label in labels):
        raise ValueError(f"not an MP area path: {path!r}")
    return ".".join(labels[:2])
```

If `STATE_CODES.values()` holds codes beyond the 51 listed (for example territories), the partition test fails. Add them to the nearest group and record the choice under DP10.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): cell vocabulary, families, state groups`.

---

### Task 6: Incident placement and time weights

**Files:**
- Create: `backend/app/ml/incidents.py`, `backend/tests/ml/test_incidents.py`

**Interfaces:**
- Consumes: Task 5 constants; `app.pipelines.geo.haversine_km`; `app.pipelines.gnis.state_code`.
- Produces (time weighting `[default pending: DP19]`):
  - `IncidentRow` (frozen dataclass; fields in the code below);
  - `AreaInfo(area_id, state_code, lat, lon, routes_by_group: Mapping[str,int])`;
  - `AreaLookup(areas: Sequence[AreaInfo])` with `.get(area_id)` and `.nearest(lat, lon, max_km) -> AreaInfo | None`;
  - `Placement` (frozen);
  - `place(row, areas) -> Placement`;
  - `interval(row) -> tuple[date, date] | None` (half-open);
  - `window_fraction(p, start, end) -> float`.

Placement rules `[default pending: DP4]`:
1. Route link to a scorable route gives a `cell` at the route's area with weight 1 on its group.
2. Route link to a boulder (`type_group IS NULL`) is `dropped`/`boulder_route` (M10).
3. Route link to an `unknown`-type route, or an area link, places at that area with groups split by the area's scorable routes per group. An area with no scorable route is `state` level.
4. No link, `geocode_precision IN ('exact','crag','area')` with coordinates: the nearest area with scorable routes within `NEAREST_AREA_KM`, split as in 3; otherwise `state`.
5. Everything else with a verified US state code is `state` (`park_centroid` included, P3:87). With no state it is `dropped`/`no_state`.

A row with no date and no year range is `dropped`/`no_date`.

- [ ] **Step 1: Write the failing tests.**

```python
from datetime import date

import pytest

from app.ml.incidents import AreaInfo, AreaLookup, IncidentRow, interval, place, window_fraction

AREAS = AreaLookup([
    AreaInfo(900000110, "CO", 40.0, -105.0, {"trad": 3, "sport": 1}),
    AreaInfo(900000120, "CO", 40.5, -105.5, {"ice": 2}),
])


def row(**over) -> IncidentRow:
    base = dict(accident_id=1, date=date(2010, 7, 14), date_precision="day", year_lo=None, year_hi=None,
                state="Colorado", geocode_precision="exact", lat=40.001, lon=-105.001, inclusion_flag=None,
                link_method=None, link_route_id=None, link_area_id=None, route_area_id=None,
                route_type_group=None, route_scorable=None, weight=1.0)
    base.update(over)
    return IncidentRow(**base)


def test_route_link_places_cell_with_its_group():
    p = place(row(link_method="mp_route_id", link_route_id=900000001, route_area_id=900000110,
                  route_type_group="trad", route_scorable=True), AREAS)
    assert (p.level, p.area_id, p.group_weights, p.month) == ("cell", 900000110, (("trad", 1.0),), 7)


def test_boulder_link_is_dropped():
    p = place(row(link_method="mp_route_id", link_route_id=9, route_area_id=900000110, route_type_group=None,
                  route_scorable=False), AREAS)
    assert (p.level, p.reason) == ("dropped", "boulder_route")


def test_unknown_type_route_splits_by_area_routes():
    p = place(row(link_method="owner", link_route_id=9, route_area_id=900000110, route_type_group="unknown",
                  route_scorable=False), AREAS)
    assert p.level == "cell" and dict(p.group_weights) == pytest.approx({"trad": 0.75, "sport": 0.25})


def test_point_only_uses_nearest_area_within_3km_else_state():
    near = place(row(), AREAS)
    assert (near.level, near.area_id, near.reason) == ("cell", 900000110, "nearest_area")
    far = place(row(lat=40.05, lon=-105.05), AREAS)          # ~7 km away
    assert (far.level, far.state_code, far.area_id) == ("state", "CO", None)


def test_park_centroid_and_untrusted_points_are_state_only():
    for precision in ("park_centroid", "region_fallback", "unknown", None):
        assert place(row(geocode_precision=precision), AREAS).level == "state"


def test_no_state_and_no_date_are_dropped():
    assert place(row(geocode_precision=None, state=None), AREAS).reason == "no_state"
    assert place(row(date=None, date_precision=None), AREAS).reason == "no_date"


def test_year_interval_straddles_cutoff():
    r = row(date=date(2004, 7, 1), date_precision="year", year_lo=2003, year_hi=2005)
    p = place(r, AREAS)
    assert p.month is None
    assert interval(r) == (date(2003, 1, 1), date(2006, 1, 1))
    assert window_fraction(p, date(1990, 1, 1), date(2004, 1, 1)) == pytest.approx(365 / 1096)
    assert window_fraction(p, date(2004, 1, 1), date(2007, 1, 1)) == pytest.approx(731 / 1096)


def test_month_precision_is_its_month_and_day_is_its_day():
    m = place(row(date=date(1998, 3, 15), date_precision="month"), AREAS)
    assert m.month == 3 and window_fraction(m, date(1998, 3, 20), date(1999, 1, 1)) == pytest.approx(19 / 31)
    d = place(row(), AREAS)
    assert window_fraction(d, date(2010, 7, 14), date(2010, 7, 15)) == 1.0
    assert window_fraction(d, date(2010, 7, 15), date(2011, 1, 1)) == 0.0


def test_flags_carry_ski_approach_and_llm_link():
    p = place(row(inclusion_flag="ski_approach_best_effort", link_method="llm", link_route_id=9,
                  route_area_id=900000120, route_type_group="ice", route_scorable=True), AREAS)
    assert p.flags == frozenset({"ski_approach", "llm_link"})
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.**

```python
"""Incident placement (DP4) and point-in-time weights. A year-precision AAC row is the interval
[year_lo, year_hi] (owner 2026-09-30), never its synthetic July-1 date; a month row is its month,
never its synthetic day 15. Weight inside a window = overlap days / interval days."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import numpy as np

from app.ml.cells import NEAREST_AREA_KM, TRUSTED_FOR_CELLS, TYPE_GROUPS
from app.pipelines.geo import haversine_km
from app.pipelines.gnis import state_code


@dataclass(frozen=True)
class IncidentRow:
    accident_id: int
    date: date | None
    date_precision: str | None
    year_lo: int | None
    year_hi: int | None
    state: str | None
    geocode_precision: str | None
    lat: float | None
    lon: float | None
    inclusion_flag: str | None
    link_method: str | None
    link_route_id: int | None
    link_area_id: int | None
    route_area_id: int | None
    route_type_group: str | None
    route_scorable: bool | None
    weight: float = 1.0


@dataclass(frozen=True)
class AreaInfo:
    area_id: int
    state_code: str
    lat: float
    lon: float
    routes_by_group: Mapping[str, int]


class AreaLookup:
    def __init__(self, areas: Sequence[AreaInfo]) -> None:
        self._by_id = {a.area_id: a for a in areas}
        self._ids = np.array([a.area_id for a in areas], dtype=np.int64)
        self._lat = np.array([a.lat for a in areas], dtype=np.float64)
        self._lon = np.array([a.lon for a in areas], dtype=np.float64)

    def get(self, area_id: int | None) -> AreaInfo | None:
        return None if area_id is None else self._by_id.get(area_id)

    def nearest(self, lat: float, lon: float, max_km: float) -> AreaInfo | None:
        if self._ids.size == 0:
            return None
        box = (np.abs(self._lat - lat) < max_km / 100.0) & (np.abs(self._lon - lon) < max_km / 60.0)
        best: tuple[float, int] | None = None
        for i in np.flatnonzero(box):
            km = haversine_km(lat, lon, float(self._lat[i]), float(self._lon[i]))
            if km <= max_km and (best is None or (km, int(self._ids[i])) < best):
                best = (km, int(self._ids[i]))
        return None if best is None else self._by_id[best[1]]


@dataclass(frozen=True)
class Placement:
    accident_id: int
    level: Literal["cell", "state", "dropped"]
    reason: str
    area_id: int | None
    state_code: str | None
    group_weights: tuple[tuple[str, float], ...]
    month: int | None
    lo: date | None
    hi: date | None
    weight: float
    flags: frozenset[str]


def interval(row: IncidentRow) -> tuple[date, date] | None:
    if row.date_precision == "year" or (row.year_lo is not None and row.year_hi is not None
                                        and row.year_lo < row.year_hi):
        lo = row.year_lo if row.year_lo is not None else (row.date.year if row.date else None)
        hi = row.year_hi if row.year_hi is not None else lo
        return None if lo is None or hi is None else (date(lo, 1, 1), date(hi + 1, 1, 1))
    if row.date is None:
        return None
    if row.date_precision == "month":
        first = row.date.replace(day=1)
        return first, first + timedelta(days=monthrange(first.year, first.month)[1])
    return row.date, row.date + timedelta(days=1)


def _split(info: AreaInfo) -> tuple[tuple[str, float], ...]:
    total = sum(n for g, n in info.routes_by_group.items() if g in TYPE_GROUPS)
    return tuple((g, n / total) for g, n in sorted(info.routes_by_group.items()) if g in TYPE_GROUPS and n > 0)


def place(row: IncidentRow, areas: AreaLookup) -> Placement:
    span = interval(row)
    flags = frozenset(f for f, on in (("ski_approach", row.inclusion_flag == "ski_approach_best_effort"),
                                      ("llm_link", row.link_method == "llm")) if on)
    point_month = row.date.month if (span is not None and row.date is not None
                                     and row.date_precision in ("day", "month")
                                     and (span[1] - span[0]).days <= 31) else None
    st = state_code(row.state)

    def out(level: Literal["cell", "state", "dropped"], reason: str, info: AreaInfo | None = None,
            groups: tuple[tuple[str, float], ...] = ()) -> Placement:
        return Placement(row.accident_id, level, reason, None if info is None else info.area_id,
                         info.state_code if info is not None else st, groups, point_month,
                         None if span is None else span[0], None if span is None else span[1], row.weight, flags)

    if span is None:
        return out("dropped", "no_date")
    if row.link_route_id is not None and row.route_type_group is None and row.route_area_id is not None:
        return out("dropped", "boulder_route")
    linked = areas.get(row.route_area_id if row.link_route_id is not None else row.link_area_id)
    if row.link_route_id is not None and row.route_scorable and row.route_type_group in TYPE_GROUPS and linked:
        return out("cell", "route_link", linked, ((str(row.route_type_group), 1.0),))
    if linked is not None and _split(linked):
        return out("cell", "area_link", linked, _split(linked))
    if (row.link_route_id is None and row.link_area_id is None and row.geocode_precision in TRUSTED_FOR_CELLS
            and row.lat is not None and row.lon is not None):
        near = areas.nearest(row.lat, row.lon, NEAREST_AREA_KM)
        if near is not None and _split(near):
            return out("cell", "nearest_area", near, _split(near))
    if st is None:
        return out("dropped", "no_state")
    return out("state", "state_only")


def window_fraction(p: Placement, start: date, end: date) -> float:
    if p.lo is None or p.hi is None or p.level == "dropped":
        return 0.0
    overlap = (min(p.hi, end) - max(p.lo, start)).days
    return max(overlap, 0) / (p.hi - p.lo).days
```

A route link to a non-scorable route that is neither a boulder nor `unknown` (for example `not_us` or `no_coordinates`) has no `AreaInfo`, so it falls through to the point rule, then to state. That is intended.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): incident placement and interval time weights`.

---

### Task 7: SQL readers and cell assembly

**Files:**
- Create: `backend/app/ml/dataset.py`, `backend/tests/ml/test_dataset_assemble.py`, `backend/tests/ml/test_dataset_sql.py` (`requires_pg`)

**Interfaces:**
- Consumes: Tasks 5–6; views `accidents_clean`, `accident_route_links`, `scorable_routes`, `canonical_areas`, `scoring_unit_features`; `internal.exposure_index`.
- Produces:
  - `STATIC_FEATURES = ("elevation_m", "lat", "tmax_mean", "tmin_mean", "precip_mm", "snowfall_cm", "freeze_thaw_days")`;
  - `ExposureRow(area_id, type_group, month, n_routes, season_share: float|None, ticks_value: float|None, ticks_per_route: float|None, tick_coverage: float|None)`;
  - `StaticRow(area_id, month, values: tuple[float|None, ...])`;
  - `CellData` (frozen; numpy arrays; fields below);
  - `Counts(y: NDArray, state_mass: NDArray (n_states, 6), years: float, dropped: Mapping[str,int], placed: Mapping[str,float])`;
  - `read_incidents(conn) -> list[IncidentRow]`;
  - `read_areas(conn) -> list[AreaInfo]`;
  - `read_area_paths(conn) -> dict[int, str]`;
  - `pick_snapshot(conn, *, data_cutoff: date, production: bool, today: date) -> date`;
  - `read_exposure(conn, *, as_of, data_cutoff) -> list[ExposureRow]`;
  - `read_static(conn) -> list[StaticRow]`;
  - `assemble_cells(exposure, static, areas, paths) -> CellData`;
  - `count_incidents(placements, cells, *, start, end, exclude_flags=frozenset(), profile=None) -> Counts`;
  - `seasonal_profile(placements, *, start, end) -> NDArray (6, 12)`.

`CellData` fields:
- per cell (length n): `area_idx`, `g` (int8), `month` (int8), `n_routes`, `season_share` (NaN = missing), `ticks_value` (NaN), `ticks_per_route` (NaN), `tick_coverage` (NaN), `static` (n × 7, NaN = missing);
- per area (length A): `area_id`, `region_idx`, `state_idx`, `area_static` (A × 12 × 7, NaN = missing);
- lookups: `regions: tuple[str,...]`, `states: tuple[str,...]`, `index: Mapping[tuple[int,int,int], int]` mapping (area_id, g, month) to the cell.

- [ ] **Step 1: Write the failing tests.** `test_dataset_assemble.py` (pure):

```python
from datetime import date

import numpy as np
import pytest

from app.ml.dataset import ExposureRow, StaticRow, assemble_cells, count_incidents, seasonal_profile
from app.ml.incidents import AreaInfo, AreaLookup, Placement

AREAS = [AreaInfo(900000110, "CO", 40.0, -105.0, {"trad": 2}), AreaInfo(900000210, "UT", 38.0, -110.0, {"ice": 1})]
PATHS = {900000110: "900000100.900000110", 900000210: "900000200.900000210"}


def _exposure():
    rows = []
    for m in range(1, 13):
        rows.append(ExposureRow(900000110, "trad", m, 2, 0.5 if m != 2 else None, 10.0, 5.0, 1.0))
        rows.append(ExposureRow(900000210, "ice", m, 1, 0.2, None, None, 0.0))
    return rows


def _static():
    return [StaticRow(a, m, (2000.0, 40.0, None if a == 900000210 else 20.0, 5.0, 50.0, 10.0, 3.0))
            for a in (900000110, 900000210) for m in range(1, 13)]


def _p(aid, area, groups, month, lo, hi, level="cell", state="CO", flags=frozenset()):
    return Placement(aid, level, "route_link", area, state, groups, month, lo, hi, 1.0, flags)


def test_missing_stays_nan_never_zero():
    cells = assemble_cells(_exposure(), _static(), AREAS, PATHS)
    i = cells.index[(900000110, 1, 2)]                        # trad, February, season missing
    assert np.isnan(cells.season_share[i]) and cells.ticks_value[i] == 10.0
    j = cells.index[(900000210, 4, 1)]                        # ice: ticks missing, tmax missing
    assert np.isnan(cells.ticks_value[j]) and np.isnan(cells.static[j, 2]) and cells.tick_coverage[j] == 0.0
    assert cells.states == ("CO", "UT") and len(cells.regions) == 2


def test_counts_respect_window_month_spread_and_flags():
    cells = assemble_cells(_exposure(), _static(), AREAS, PATHS)
    day = _p(1, 900000110, (("trad", 1.0),), 7, date(2010, 7, 14), date(2010, 7, 15))
    year = _p(2, 900000110, (("trad", 1.0),), None, date(2003, 1, 1), date(2006, 1, 1))
    ski = _p(3, 900000210, (("ice", 1.0),), 1, date(2012, 1, 5), date(2012, 1, 6), state="UT",
             flags=frozenset({"ski_approach"}))
    state_only = _p(4, None, (), 8, date(2011, 8, 1), date(2011, 8, 2), level="state")
    profile = np.full((6, 12), 1 / 12)
    c = count_incidents([day, year, ski, state_only], cells, start=date(1990, 1, 1), end=date(2004, 1, 1),
                        profile=profile)
    assert c.y.sum() == pytest.approx(365 / 1096)            # only the year row overlaps, spread evenly
    assert c.y[cells.index[(900000110, 1, 5)]] == pytest.approx(365 / 1096 / 12)
    c2 = count_incidents([day, year, ski, state_only], cells, start=date(2004, 1, 1), end=date(2027, 1, 1),
                         profile=profile, exclude_flags=frozenset({"ski_approach"}))
    assert c2.y[cells.index[(900000110, 1, 7)]] == pytest.approx(1 + 731 / 1096 / 12)
    assert c2.y[cells.index[(900000210, 4, 1)]] == 0.0
    assert c2.state_mass.sum() == pytest.approx(1.0)          # the state-only row, split by state exposure
    assert c2.years == pytest.approx(23.0, abs=0.01)


def test_seasonal_profile_smooths_and_sums_to_one():
    day = _p(1, 900000110, (("trad", 1.0),), 7, date(2010, 7, 14), date(2010, 7, 15))
    prof = seasonal_profile([day], start=date(1990, 1, 1), end=date(2027, 1, 1))
    assert prof.shape == (6, 12) and np.allclose(prof.sum(axis=1), 1.0)
    assert prof[1, 6] > prof[1, 0] > 0.0                     # trad July above an empty month, never zero
```

`test_dataset_sql.py` (`requires_pg`) seeds a migrated database with:
- 0012 head;
- `canonical_areas`/`canonical_routes`/`mp_routes`/`mp_locations` as in Task 1's SEED, plus a second area;
- `feature_points`, `route_static_features` and `cell_normals_status`/`cell_climate_normals` rows for one area only;
- accidents with the repair columns set so `accidents_clean` holds 3 rows (one `mp_route_id`-linked, one `year` precision with `year_lo/hi`, one `excluded_reason = 'year_unverified'`);
- an `internal.exposure_index` snapshot (as_of 2026-10-01, data_cutoff 2026-10-01) and a backtest snapshot (as_of 2026-10-01, data_cutoff 2004-01-01).

It asserts:
- `read_incidents` returns 2 rows, the excluded one absent, and the route-linked row carrying `route_type_group`;
- `read_static` yields `None` for the unsampled area's elevation and its pending normals, never 0;
- `pick_snapshot(..., data_cutoff=date(2004,1,1), production=False)` → 2026-10-01;
- `pick_snapshot(..., production=True, today=date(2026,12,1))` raises `StaleExposure` (older than 35 days);
- `read_exposure` maps `{"value": null, "missing": true}` to `None` and a measured `0.0` to `0.0`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `backend/app/ml/dataset.py`:

```python
"""Training data on the MP catalog, read as trainer through views only (DP17). Readers keep SQL
NULL as None; assembly keeps it as NaN. Exposure comes from one internal.exposure_index snapshot
whose data_cutoff is the fold cutoff (P3:398, X-conflict 6)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ml.cells import G_INDEX, TYPE_GROUPS, region_key
from app.ml.incidents import AreaInfo, IncidentRow, Placement, window_fraction

STATIC_FEATURES = ("elevation_m", "lat", "tmax_mean", "tmin_mean", "precip_mm", "snowfall_cm", "freeze_thaw_days")
MAX_SNAPSHOT_AGE_DAYS = 35
PROFILE_PSEUDOCOUNT = 0.5

INCIDENTS_SQL = """
SELECT a.accident_id, a.date, a.date_precision, a.year_lo, a.year_hi, a.state, a.geocode_precision,
       a.latitude, a.longitude, a.inclusion_flag, l.method, l.route_id, l.area_id,
       r.area_id AS route_area_id, r.type_group AS route_type_group, r.scorable AS route_scorable
FROM accidents_clean a
LEFT JOIN accident_route_links l ON l.accident_id = a.accident_id
LEFT JOIN canonical_routes r ON r.route_id = l.route_id
"""
AREAS_SQL = """
SELECT s.area_id, s.state_code, a.lat, a.lon, s.type_group, count(*) AS n
FROM scorable_routes s JOIN canonical_areas a ON a.area_id = s.area_id
WHERE s.type_group <> 'unknown' AND a.lat IS NOT NULL
GROUP BY s.area_id, s.state_code, a.lat, a.lon, s.type_group
"""
PATHS_SQL = "SELECT DISTINCT area_id, area_path::text FROM scorable_routes WHERE area_path IS NOT NULL"
SNAPSHOT_SQL = """
SELECT max(as_of) FROM internal.exposure_index
WHERE data_cutoff = :cutoff AND (NOT :production OR as_of = data_cutoff)
"""
LATEST_PRODUCTION_SQL = "SELECT max(as_of) FROM internal.exposure_index WHERE as_of = data_cutoff"
EXPOSURE_SQL = """
SELECT area_id, type_group, month, n_routes, proxy, tick_coverage FROM internal.exposure_index
WHERE as_of = :as_of AND data_cutoff = :cutoff
"""
STATIC_SQL = """
SELECT s.area_id, u.month, min(u.elevation_m), min(u.lat), min(u.tmax_mean), min(u.tmin_mean), min(u.precip_mm),
       min(u.snowfall_cm), min(u.freeze_thaw_days), bool_and(u.normals_status IN ('complete', 'insufficient'))
FROM scoring_unit_features u JOIN scorable_routes s ON s.route_id = u.unit_id
WHERE u.unit_kind = 'route'
GROUP BY s.area_id, u.month
"""


class StaleExposure(RuntimeError):
    pass


@dataclass(frozen=True)
class ExposureRow:
    area_id: int
    type_group: str
    month: int
    n_routes: int
    season_share: float | None
    ticks_value: float | None
    ticks_per_route: float | None
    tick_coverage: float | None


@dataclass(frozen=True)
class StaticRow:
    area_id: int
    month: int
    values: tuple[float | None, ...]


@dataclass(frozen=True)
class CellData:
    area_idx: NDArray[np.int64]
    g: NDArray[np.int8]
    month: NDArray[np.int8]
    n_routes: NDArray[np.float64]
    season_share: NDArray[np.float64]
    ticks_value: NDArray[np.float64]
    ticks_per_route: NDArray[np.float64]
    tick_coverage: NDArray[np.float64]
    static: NDArray[np.float64]
    area_id: NDArray[np.int64]
    region_idx: NDArray[np.int64]
    state_idx: NDArray[np.int64]
    area_static: NDArray[np.float64]
    regions: tuple[str, ...]
    states: tuple[str, ...]
    index: Mapping[tuple[int, int, int], int]

    @property
    def n(self) -> int:
        return int(self.g.size)


@dataclass(frozen=True)
class Counts:
    y: NDArray[np.float64]
    state_mass: NDArray[np.float64]
    years: float
    dropped: Mapping[str, int]
    placed: Mapping[str, float]


def _component(proxy: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    comp = proxy.get(key)
    return None if not isinstance(comp, Mapping) or comp.get("missing") else comp


def _num(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]


async def read_incidents(conn: AsyncConnection) -> list[IncidentRow]:
    rows = (await conn.execute(text(INCIDENTS_SQL))).all()
    return [IncidentRow(int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], _num(r[7]), _num(r[8]), r[9], r[10],
                        r[11], r[12], r[13], r[14], r[15]) for r in rows]


async def read_areas(conn: AsyncConnection) -> list[AreaInfo]:
    groups: dict[int, dict[str, int]] = defaultdict(dict)
    meta: dict[int, tuple[str, float, float]] = {}
    for area_id, st, lat, lon, g, n in (await conn.execute(text(AREAS_SQL))).all():
        groups[int(area_id)][str(g)] = int(n)
        meta[int(area_id)] = (str(st), float(lat), float(lon))
    return [AreaInfo(a, *meta[a], dict(groups[a])) for a in sorted(meta)]


async def read_area_paths(conn: AsyncConnection) -> dict[int, str]:
    return {int(a): str(p) for a, p in (await conn.execute(text(PATHS_SQL))).all()}


async def pick_snapshot(conn: AsyncConnection, *, data_cutoff: date, production: bool, today: date) -> date:
    if production:
        as_of = (await conn.execute(text(LATEST_PRODUCTION_SQL))).scalar_one()
        if as_of is None or (today - as_of).days > MAX_SNAPSHOT_AGE_DAYS:
            raise StaleExposure(f"newest production exposure snapshot is {as_of}; run exposure build first")
        return as_of
    as_of = (await conn.execute(text(SNAPSHOT_SQL), {"cutoff": data_cutoff, "production": False})).scalar_one()
    if as_of is None:
        raise StaleExposure(f"no exposure snapshot with data_cutoff {data_cutoff}; run "
                            f"`exposure build --cutoff {data_cutoff}` (Task 21)")
    return as_of


async def read_exposure(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> list[ExposureRow]:
    out = []
    for area_id, g, month, n_routes, proxy, cov in (await conn.execute(
            text(EXPOSURE_SQL), {"as_of": as_of, "cutoff": data_cutoff})).all():
        proxy = proxy if isinstance(proxy, Mapping) else json.loads(proxy)
        season, ticks = _component(proxy, "season_share"), _component(proxy, "mp_ticks")
        out.append(ExposureRow(int(area_id), str(g), int(month), int(n_routes),
                               None if season is None else float(season["value"]),
                               None if ticks is None else float(ticks["value"]),
                               None if ticks is None else float(ticks["per_route_mean"]), _num(cov)))
    return out


async def read_static(conn: AsyncConnection) -> list[StaticRow]:
    out = []
    for r in (await conn.execute(text(STATIC_SQL))).all():
        normals_ok = bool(r[9])
        vals = [_num(r[2]), _num(r[3])] + [_num(v) if normals_ok else None for v in r[4:9]]
        out.append(StaticRow(int(r[0]), int(r[1]), tuple(vals)))
    return out


def assemble_cells(exposure: Sequence[ExposureRow], static: Sequence[StaticRow], areas: Sequence[AreaInfo],
                   paths: Mapping[int, str]) -> CellData:
    area_ids = sorted({e.area_id for e in exposure})
    a_pos = {a: i for i, a in enumerate(area_ids)}
    st_of = {a.area_id: a.state_code for a in areas}
    states = tuple(sorted({st_of[a] for a in area_ids}))
    regions = tuple(sorted({region_key(paths[a]) for a in area_ids}))
    area_static = np.full((len(area_ids), 12, len(STATIC_FEATURES)), np.nan)
    for s in static:
        if s.area_id in a_pos:
            area_static[a_pos[s.area_id], s.month - 1] = [np.nan if v is None else v for v in s.values]
    rows = sorted(exposure, key=lambda e: (e.area_id, G_INDEX[e.type_group], e.month))
    nan = lambda v: np.nan if v is None else v  # noqa: E731
    area_idx = np.array([a_pos[e.area_id] for e in rows], dtype=np.int64)
    month = np.array([e.month for e in rows], dtype=np.int8)
    return CellData(
        area_idx=area_idx, g=np.array([G_INDEX[e.type_group] for e in rows], dtype=np.int8), month=month,
        n_routes=np.array([e.n_routes for e in rows], dtype=np.float64),
        season_share=np.array([nan(e.season_share) for e in rows]), ticks_value=np.array([nan(e.ticks_value) for e in rows]),
        ticks_per_route=np.array([nan(e.ticks_per_route) for e in rows]),
        tick_coverage=np.array([nan(e.tick_coverage) for e in rows]),
        static=area_static[area_idx, month.astype(np.int64) - 1],
        area_id=np.array(area_ids, dtype=np.int64),
        region_idx=np.array([regions.index(region_key(paths[a])) for a in area_ids], dtype=np.int64),
        state_idx=np.array([states.index(st_of[a]) for a in area_ids], dtype=np.int64),
        area_static=area_static, regions=regions, states=states,
        index={(e.area_id, G_INDEX[e.type_group], e.month): i for i, e in enumerate(rows)},
    )


def seasonal_profile(placements: Sequence[Placement], *, start: date, end: date) -> NDArray[np.float64]:
    """National month profile per type group from month-known incidents in the window, with a
    0.5 pseudocount per month so a month with no reports is small, never impossible."""
    counts = np.full((len(TYPE_GROUPS), 12), PROFILE_PSEUDOCOUNT)
    for p in placements:
        if p.month is None or p.level != "cell":
            continue
        w = window_fraction(p, start, end) * p.weight
        for g, share in p.group_weights:
            counts[G_INDEX[g], p.month - 1] += w * share
    return counts / counts.sum(axis=1, keepdims=True)


def count_incidents(placements: Sequence[Placement], cells: CellData, *, start: date, end: date,
                    exclude_flags: frozenset[str] = frozenset(),
                    profile: NDArray[np.float64] | None = None) -> Counts:
    prof = seasonal_profile(placements, start=start, end=end) if profile is None else profile
    y = np.zeros(cells.n)
    state_mass = np.zeros((len(cells.states), len(TYPE_GROUPS)))
    dropped: Counter[str] = Counter()
    placed: Counter[str] = Counter()
    state_share = _state_group_share(cells)
    for p in placements:
        if p.level == "dropped" or p.flags & exclude_flags:
            dropped[p.reason if p.level == "dropped" else "excluded_flag"] += 1
            continue
        w = window_fraction(p, start, end) * p.weight
        if w == 0.0:
            continue
        if p.level == "state":
            if p.state_code in cells.states:
                state_mass[cells.states.index(p.state_code)] += w * state_share[cells.states.index(p.state_code)]
                placed["state"] += w
            else:
                dropped["state_without_cells"] += 1
            continue
        for g, share in p.group_weights:
            gi = G_INDEX[g]
            months = [(p.month, 1.0)] if p.month is not None else [(m, float(prof[gi, m - 1])) for m in range(1, 13)]
            for m, mw in months:
                key = (int(p.area_id), gi, m)  # level "cell" always carries an area
                if key in cells.index:
                    y[cells.index[key]] += w * share * mw
            placed["cell"] += w * share
    return Counts(y=y, state_mass=state_mass, years=(end - start).days / 365.25, dropped=dict(dropped),
                  placed=dict(placed))


def _state_group_share(cells: CellData) -> NDArray[np.float64]:
    """Each state's route share per type group, which splits a state-only incident's weight."""
    share = np.zeros((len(cells.states), len(TYPE_GROUPS)))
    january = cells.month == 1
    np.add.at(share, (cells.state_idx[cells.area_idx[january]], cells.g[january]), cells.n_routes[january])
    total = share.sum(axis=1, keepdims=True)
    return np.divide(share, total, out=np.zeros_like(share), where=total > 0)
```

Semantics the tests pin: `count_incidents` counts weight inside `[start, end)`; `years` is `(end − start)` in Julian years (23.0 for 2004-01-01..2027-01-01); `state_mass` rows sum to the placed state-level weight.

- [ ] **Step 4: Run.** `cd backend && uv run pytest tests/ml/test_dataset_assemble.py tests/ml/test_dataset_sql.py -q && uv run mypy` → PASS.

- [ ] **Step 5: Commit** `feat(ml): SQL readers, cell assembly, interval incident counts`.

---

### Task 8: Design matrix with missing indicators and tick gating

**Files:**
- Create: `backend/app/ml/features.py`, `backend/tests/ml/test_features.py`

**Interfaces:**
- Consumes: `CellData` (Task 7).
- Produces:
  - `TickPolicy(rock_coverage_min: float = 0.90, enabled: bool = True)` `[default pending: DP5]`, and `tick_families(cells, policy) -> frozenset[str]`, the families whose tick covariate is on;
  - `Design(X: NDArray (n,p), names: tuple[str,...], penalty: NDArray (p,), nonneg: tuple[int,...], center: NDArray, scale: NDArray, exposure_cols: tuple[int,...], tick_ref: float)`;
  - `build_design(cells, *, policy, lambdas=Lambdas(), fit_stats: Design | None = None) -> Design`. With `fit_stats` it reuses the training centring, scaling and `tick_ref`, so a test fold is never standardized on itself (G5);
  - `Lambdas(type_main=2.0, type_month=8.0, covariate=0.1)`.

Column order:
1. `intercept`, `fam_mountain`;
2. `a_<g>` ×6;
3. `m_<2..12>` ×11;
4. `d_<g>_<m>` ×72;
5. exposure: `x_density` (nonneg), `x_season`, `ind_season_missing`, `x_ticks`, `ind_ticks_off`;
6. static: `z_elevation_m`, `ind_elevation_missing`, `z_lat`, `z_tmax_mean`, `z_tmin_mean`, `z_precip_mm`, `z_snowfall_cm`, `z_freeze_thaw_days`, `ind_normals_missing`.

`x_season = log1p(30·share)`. `x_ticks = log1p(ticks_per_route / tick_ref)`, where `tick_ref` is the training cells' mean `ticks_per_route` over gated-on cells. Normalising within the snapshot makes an era's overall tick volume cancel across rolling-origin snapshots (DP5).

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np
import pytest

from app.ml.features import Design, TickPolicy, build_design, tick_families
from app.ml.fixtures import synthetic_cells


def test_missing_is_indicator_plus_centre_never_raw_zero():
    cells = synthetic_cells(seed=1, n_areas=40)
    cells.season_share[0] = np.nan
    cells.static[0, 0] = np.nan
    d = build_design(cells, policy=TickPolicy())
    col = {n: i for i, n in enumerate(d.names)}
    assert d.X[0, col["ind_season_missing"]] == 1.0 and d.X[0, col["x_season"]] == 0.0
    assert d.X[0, col["ind_elevation_missing"]] == 1.0 and d.X[0, col["z_elevation_m"]] == 0.0
    present = ~np.isnan(cells.static[:, 0])
    assert abs(d.X[present, col["z_elevation_m"]].mean()) < 1e-9


def test_test_fold_reuses_training_standardization():
    train, test = synthetic_cells(seed=1, n_areas=40), synthetic_cells(seed=2, n_areas=10)
    d = build_design(train, policy=TickPolicy())
    t = build_design(test, policy=TickPolicy(), fit_stats=d)
    np.testing.assert_array_equal(t.center, d.center)
    assert t.tick_ref == d.tick_ref


def test_rock_ticks_gated_until_coverage():
    cells = synthetic_cells(seed=3, n_areas=60, rock_tick_coverage=0.5)
    assert tick_families(cells, TickPolicy()) == frozenset({"mountain"})
    d = build_design(cells, policy=TickPolicy())
    off = build_design(cells, policy=TickPolicy(enabled=False))
    col = {n: i for i, n in enumerate(d.names)}
    rock = np.isin(cells.g, [0, 1, 2])
    np.testing.assert_array_equal(d.X[rock][:, col["x_ticks"]], 0.0)
    np.testing.assert_array_equal(d.X[rock][:, col["ind_ticks_off"]], 1.0)
    assert not any(n in d.names for n in ("tick_coverage", "ind_ticks_missing"))
    np.testing.assert_array_equal(off.X[:, col["ind_ticks_off"]], 1.0)


def test_penalty_and_nonneg_layout():
    d = build_design(synthetic_cells(seed=1, n_areas=20), policy=TickPolicy())
    col = {n: i for i, n in enumerate(d.names)}
    assert d.penalty[col["intercept"]] == 0 and d.penalty[col["m_7"]] == 0
    assert d.penalty[col["a_ice"]] == 2.0 and d.penalty[col["d_ice_7"]] == 8.0
    assert d.nonneg == (col["x_density"],)
    assert d.X.shape[1] == len(d.names) == 2 + 6 + 11 + 72 + 5 + 9
```

`app.ml.fixtures.synthetic_cells(seed, n_areas, rock_tick_coverage=1.0) -> CellData` is created in this task. It is the start of the Task 20 fixture module. It builds areas across four synthetic states (`CO`, `UT`, `WA`, `NH`, so CV groups exist), with one to three type groups per area and 12 months each. Columns are drawn from fixed distributions; `rock_tick_coverage` sets `tick_coverage` on rock-family cells, with NaN `ticks_per_route` on the uncovered share. No DB and no real ids: area ids start at 900000000.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `backend/app/ml/features.py`:

```python
"""Stage-1 design matrix (P3 M5 + A:54-61). Missing covariates enter as the training mean plus a
missing indicator (DP20). Rock-family ticks enter only once national rock tick coverage reaches
rock_coverage_min (DP5): rock routes near accidents were scraped first (X-conflict 5), so a
coverage or missingness term would encode the outcome. No coverage term ever enters."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.ml.cells import FAMILY_OF, TYPE_GROUPS
from app.ml.dataset import STATIC_FEATURES, CellData

LOG1P_FEATURES = frozenset({"precip_mm", "snowfall_cm"})


@dataclass(frozen=True)
class TickPolicy:
    rock_coverage_min: float = 0.90
    enabled: bool = True


@dataclass(frozen=True)
class Lambdas:
    type_main: float = 2.0
    type_month: float = 8.0
    covariate: float = 0.1


@dataclass(frozen=True)
class Design:
    X: NDArray[np.float64]
    names: tuple[str, ...]
    penalty: NDArray[np.float64]
    nonneg: tuple[int, ...]
    center: NDArray[np.float64]
    scale: NDArray[np.float64]
    exposure_cols: tuple[int, ...]
    tick_ref: float
    families_on: frozenset[str] = field(default_factory=frozenset)


def tick_families(cells: CellData, policy: TickPolicy) -> frozenset[str]:
    if not policy.enabled:
        return frozenset()
    rock = np.isin(cells.g, [TYPE_GROUPS.index(g) for g in ("sport", "trad", "rock")]) & (cells.month == 1)
    routes = cells.n_routes[rock].sum()
    covered = np.nansum(cells.tick_coverage[rock] * cells.n_routes[rock])
    on = {"mountain"}
    if routes > 0 and covered / routes >= policy.rock_coverage_min:
        on.add("rock_family")
    return frozenset(on)


def build_design(cells: CellData, *, policy: TickPolicy, lambdas: Lambdas = Lambdas(),
                 fit_stats: Design | None = None) -> Design:
    n = cells.n
    names: list[str] = ["intercept", "fam_mountain"]
    cols: list[NDArray[np.float64]] = [np.ones(n), np.array([FAMILY_OF[TYPE_GROUPS[g]] == "mountain" for g in cells.g],
                                                             dtype=np.float64)]
    pen: list[float] = [0.0, 0.0]
    for gi, g in enumerate(TYPE_GROUPS):
        names.append(f"a_{g}")
        cols.append((cells.g == gi).astype(np.float64))
        pen.append(lambdas.type_main)
    for m in range(2, 13):
        names.append(f"m_{m}")
        cols.append((cells.month == m).astype(np.float64))
        pen.append(0.0)
    for gi, g in enumerate(TYPE_GROUPS):
        for m in range(1, 13):
            names.append(f"d_{g}_{m}")
            cols.append(((cells.g == gi) & (cells.month == m)).astype(np.float64))
            pen.append(lambdas.type_month)
    families_on = fit_stats.families_on if fit_stats is not None else tick_families(cells, policy)
    fam = np.array([FAMILY_OF[TYPE_GROUPS[g]] for g in cells.g])
    tick_on = np.isin(fam, sorted(families_on)) & ~np.isnan(cells.ticks_per_route)
    tick_ref = fit_stats.tick_ref if fit_stats is not None else (
        float(cells.ticks_per_route[tick_on].mean()) if tick_on.any() and cells.ticks_per_route[tick_on].mean() > 0
        else 1.0)
    season_missing = np.isnan(cells.season_share)
    raw: dict[str, NDArray[np.float64]] = {
        "x_density": np.log1p(cells.n_routes),
        "x_season": np.where(season_missing, np.nan, np.log1p(30.0 * np.nan_to_num(cells.season_share, nan=0.0))),
        "ind_season_missing": season_missing.astype(np.float64),
        "x_ticks": np.where(tick_on, np.log1p(np.nan_to_num(cells.ticks_per_route, nan=0.0) / tick_ref), np.nan),
        "ind_ticks_off": (~tick_on).astype(np.float64),
    }
    for j, f in enumerate(STATIC_FEATURES):
        v = cells.static[:, j]
        raw[f"z_{f}"] = np.log1p(v) if f in LOG1P_FEATURES else v
    raw["ind_elevation_missing"] = np.isnan(cells.static[:, 0]).astype(np.float64)
    raw["ind_normals_missing"] = np.isnan(cells.static[:, 2:]).any(axis=1).astype(np.float64)
    order = ["x_density", "x_season", "ind_season_missing", "x_ticks", "ind_ticks_off", "z_elevation_m",
             "ind_elevation_missing", "z_lat", "z_tmax_mean", "z_tmin_mean", "z_precip_mm", "z_snowfall_cm",
             "z_freeze_thaw_days", "ind_normals_missing"]
    k0 = len(names)
    center = np.zeros(k0 + len(order))
    scale = np.ones(k0 + len(order))
    for k, name in enumerate(order, start=k0):
        v = raw[name]
        if name.startswith(("x_", "z_")):
            if fit_stats is not None:
                mu, sd = fit_stats.center[k], fit_stats.scale[k]
            else:
                ok = ~np.isnan(v)
                mu = float(v[ok].mean()) if ok.any() else 0.0
                sd = float(v[ok].std()) if ok.any() and v[ok].std() > 0 else 1.0
            center[k], scale[k] = mu, sd
            # A missing value is the training mean (standardized 0); its indicator carries the gap.
            v = np.where(np.isnan(v), 0.0, (v - mu) / sd)
        names.append(name)
        cols.append(v)
        pen.append(lambdas.covariate)
    idx = {nm: i for i, nm in enumerate(names)}
    return Design(X=np.column_stack(cols), names=tuple(names), penalty=np.array(pen), nonneg=(idx["x_density"],),
                  center=center, scale=scale,
                  exposure_cols=tuple(idx[c] for c in ("x_density", "x_season", "ind_season_missing", "x_ticks",
                                                       "ind_ticks_off")),
                  tick_ref=tick_ref, families_on=families_on)
```

The `x_season` raw value uses `np.nan_to_num` only inside a `where` whose missing branch is NaN. `tests/test_no_coalesce_in_ml.py` (Task 25) allowlists `nan_to_num` only when the same expression masks the missing rows. Document that in the test's docstring.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): design matrix, missing indicators, tick gating`.

---

### Task 9: CV folds

**Files:**
- Create: `backend/app/ml/folds.py`, `backend/tests/ml/test_folds.py`

**Interfaces:**
- Produces:
  - `Fold(name: str, kind: Literal["spatial","rolling","prospective"], train_start: date, train_end: date, test_start: date, test_end: date, held_out_states: frozenset[str], exposure_cutoff: date)`;
  - `spatial_folds(cutoff: date) -> list[Fold]`: 8 folds; train and test both span `TRAIN_START..cutoff`; the exposure cutoff is the production cutoff, because spatial folds test "where", not "when";
  - `rolling_folds() -> list[Fold]`: origins 2004, 2008, 2012 and 2016, a 3-year test, exposure cutoff = origin (P3:371);
  - `prospective_fold() -> Fold`: train to 2026-02-05, test 2026-02-05..2026-07-03, exposure cutoff 2026-02-05;
  - `BACKTEST_CUTOFFS = (date(2004,1,1), date(2008,1,1), date(2012,1,1), date(2016,1,1), date(2026,2,5))`.

- [ ] **Step 1: Write the failing tests.**

```python
from datetime import date

from app.ml.cells import STATE_GROUPS
from app.ml.folds import BACKTEST_CUTOFFS, prospective_fold, rolling_folds, spatial_folds


def test_spatial_folds_cover_each_state_group_once():
    folds = spatial_folds(date(2026, 10, 1))
    assert [f.name for f in folds] == [f"spatial:{g}" for g in STATE_GROUPS]
    held = [s for f in folds for s in f.held_out_states]
    assert sorted(held) == sorted(s for v in STATE_GROUPS.values() for s in v)


def test_rolling_folds_never_overlap_train_and_test():
    for f in rolling_folds():
        assert f.train_end == f.test_start == f.exposure_cutoff
        assert (f.test_end.year - f.test_start.year) == 3 and not f.held_out_states
    assert [f.test_start.year for f in rolling_folds()] == [2004, 2008, 2012, 2016]


def test_prospective_fold_matches_the_kernel_window():
    f = prospective_fold()
    assert (f.test_start, f.test_end, f.exposure_cutoff) == (date(2026, 2, 5), date(2026, 7, 3), date(2026, 2, 5))
    assert set(BACKTEST_CUTOFFS) >= {f.exposure_cutoff for f in rolling_folds()} | {f.exposure_cutoff}
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.**

```python
"""Validation folds (P3:365-384): spatial block CV over the eight state groups (primary),
rolling-origin on pre-2019 data, and the prospective kernel window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.ml.cells import STATE_GROUPS, TRAIN_START

ORIGINS = (2004, 2008, 2012, 2016)
TEST_YEARS = 3
PROSPECTIVE = (date(2026, 2, 5), date(2026, 7, 3))
BACKTEST_CUTOFFS = tuple(date(y, 1, 1) for y in ORIGINS) + (PROSPECTIVE[0],)


@dataclass(frozen=True)
class Fold:
    name: str
    kind: Literal["spatial", "rolling", "prospective"]
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    held_out_states: frozenset[str]
    exposure_cutoff: date


def spatial_folds(cutoff: date) -> list[Fold]:
    return [Fold(f"spatial:{g}", "spatial", TRAIN_START, cutoff, TRAIN_START, cutoff, frozenset(states), cutoff)
            for g, states in STATE_GROUPS.items()]


def rolling_folds() -> list[Fold]:
    return [Fold(f"rolling:{y}", "rolling", TRAIN_START, date(y, 1, 1), date(y, 1, 1), date(y + TEST_YEARS, 1, 1),
                 frozenset(), date(y, 1, 1)) for y in ORIGINS]


def prospective_fold() -> Fold:
    return Fold("prospective:2026", "prospective", TRAIN_START, PROSPECTIVE[0], PROSPECTIVE[0], PROSPECTIVE[1],
                frozenset(), PROSPECTIVE[0])
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ml): CV folds`.
- [ ] **Step 6: PR 3-1 docs.** Add the CHANGELOG entry. Add the `app.ml.*` modules to the mypy allowlist (already done per task); `uv run mypy` passes.

---

# PR 3-2 — `feat/p3-model-core`

### Task 10: Penalized Poisson GLM (from scratch)

**Files:**
- Create: `backend/app/ml/glm.py`, `backend/tests/ml/test_glm.py`

**Interfaces:**
- Produces:
  - `GlmFit(coef: NDArray, iterations: int, converged: bool, fixed_zero: tuple[int,...], deviance: float)`;
  - `fit_poisson(X, y, penalty, *, nonneg=(), tol=1e-9, max_iter=100) -> GlmFit`;
  - `linear_predictor(X, coef) -> NDArray`.

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np
import pytest

from app.ml.glm import fit_poisson


def test_intercept_only_recovers_the_mean():
    y = np.array([0.0, 1.0, 2.0, 5.0])
    fit = fit_poisson(np.ones((4, 1)), y, np.zeros(1))
    assert fit.converged and np.exp(fit.coef[0]) == pytest.approx(2.0, rel=1e-8)


def test_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(20000), rng.normal(size=20000), rng.integers(0, 2, 20000)])
    beta = np.array([-1.0, 0.4, -0.7])
    y = rng.poisson(np.exp(X @ beta)).astype(float)
    fit = fit_poisson(X, y, np.zeros(3))
    np.testing.assert_allclose(fit.coef, beta, atol=0.05)


def test_ridge_shrinks_a_penalized_column_toward_zero():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(200), rng.integers(0, 2, 200)])
    y = rng.poisson(np.exp(0.5 + 1.0 * X[:, 1])).astype(float)
    free = fit_poisson(X, y, np.array([0.0, 0.0])).coef[1]
    tight = fit_poisson(X, y, np.array([0.0, 1e4])).coef[1]
    assert abs(tight) < 0.05 < abs(free)


def test_nonneg_column_is_fixed_at_zero_when_the_data_want_it_negative():
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(5000), rng.normal(size=5000)])
    y = rng.poisson(np.exp(0.2 - 0.5 * X[:, 1])).astype(float)
    fit = fit_poisson(X, y, np.zeros(2), nonneg=(1,))
    assert fit.coef[1] == 0.0 and fit.fixed_zero == (1,)


def test_fractional_counts_are_accepted_and_negative_refused():
    fit = fit_poisson(np.ones((3, 1)), np.array([0.25, 0.5, 0.0]), np.zeros(1))
    assert np.exp(fit.coef[0]) == pytest.approx(0.25)
    with pytest.raises(ValueError, match="non-negative"):
        fit_poisson(np.ones((2, 1)), np.array([-1.0, 1.0]), np.zeros(1))
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.**

```python
"""Penalized Poisson regression by Newton/IRLS, written here rather than imported (owner rule:
the model is built in-house). Penalty is 0.5 * sum(p_j * b_j^2); a nonneg column found negative
is fixed at 0 and the fit is redone (active set), which keeps route density's gamma >= 0 so more
exposure with fixed incidents can never raise RR (P3:469)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

ETA_CLIP = 30.0


@dataclass(frozen=True)
class GlmFit:
    coef: NDArray[np.float64]
    iterations: int
    converged: bool
    fixed_zero: tuple[int, ...]
    deviance: float


def linear_predictor(X: NDArray[np.float64], coef: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(X @ coef, dtype=np.float64)


def _objective(X: NDArray[np.float64], y: NDArray[np.float64], b: NDArray[np.float64],
               pen: NDArray[np.float64]) -> float:
    eta = np.clip(X @ b, -ETA_CLIP, ETA_CLIP)
    return float(np.sum(np.exp(eta) - y * eta) + 0.5 * np.sum(pen * b * b))


def _irls(X: NDArray[np.float64], y: NDArray[np.float64], pen: NDArray[np.float64], tol: float,
          max_iter: int) -> tuple[NDArray[np.float64], int, bool]:
    b = np.zeros(X.shape[1])
    b[0] = np.log(max(float(y.mean()), 1e-12))
    obj = _objective(X, y, b, pen)
    for it in range(1, max_iter + 1):
        mu = np.exp(np.clip(X @ b, -ETA_CLIP, ETA_CLIP))
        grad = X.T @ (mu - y) + pen * b
        hess = (X.T * mu) @ X + np.diag(pen)
        step = np.linalg.solve(hess, grad)
        t = 1.0
        while True:
            cand = b - t * step
            cand_obj = _objective(X, y, cand, pen)
            if cand_obj <= obj + 1e-12 or t < 1e-8:
                break
            t /= 2.0
        done = np.max(np.abs(cand - b)) < tol * (1.0 + np.max(np.abs(b)))
        b, obj = cand, cand_obj
        if done:
            return b, it, True
    return b, max_iter, False


def fit_poisson(X: NDArray[np.float64], y: NDArray[np.float64], penalty: NDArray[np.float64], *,
                nonneg: Sequence[int] = (), tol: float = 1e-9, max_iter: int = 100) -> GlmFit:
    if np.any(y < 0) or not np.all(np.isfinite(y)):
        raise ValueError("counts must be finite and non-negative")
    if not np.allclose(X[:, 0], 1.0):
        raise ValueError("column 0 must be the intercept")
    fixed: list[int] = []
    while True:
        keep = [j for j in range(X.shape[1]) if j not in fixed]
        b_keep, iters, ok = _irls(X[:, keep], y, penalty[keep], tol, max_iter)
        coef = np.zeros(X.shape[1])
        coef[keep] = b_keep
        neg = [j for j in nonneg if j not in fixed and coef[j] < 0.0]
        if not neg:
            mu = np.exp(np.clip(X @ coef, -ETA_CLIP, ETA_CLIP))
            with np.errstate(divide="ignore", invalid="ignore"):
                term = np.where(y > 0, y * np.log(y / mu), 0.0)
            return GlmFit(coef, iters, ok, tuple(sorted(fixed)), float(2.0 * np.sum(term - (y - mu))))
        fixed.extend(neg)
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): penalized Poisson IRLS with nonneg active set`.

---

### Task 11: Feature archetypes and nearest-evidence distance

**Files:**
- Create: `backend/app/ml/archetypes.py`, `backend/tests/ml/test_archetypes.py`

**Interfaces:**
- Produces:
  - `ARCH_FEATURES = ("elevation_m", "lat", "tmax_jul", "tmin_jan", "precip_year", "snow_year", "freeze_thaw_year")`;
  - `area_feature_matrix(cells: CellData) -> NDArray (A, 7)` (NaN when an input month is missing);
  - `Standardizer.fit(F) / .apply(F)`;
  - `weighted_sqdist(A, B, w) -> NDArray (len(A), len(B))`: a partial distance over shared non-NaN features, rescaled by `sum(w)/sum(w_shared)`, and NaN when no feature is shared;
  - `kmeans(Z, k, w, *, seed=0, iters=50) -> tuple[labels (A,), centers (k, f)]`: k-means++ with a fixed seed, NaN-aware, an all-NaN row labelled −1;
  - `nearest_distance(Zq, Zev, w, *, chunk=4096) -> NDArray`: Euclidean (sqrt of the weighted square), +inf when there is no evidence row or no shared feature.

Weights `w` come from Task 18's tuning `[default pending: DP9]`.

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np
import pytest

from app.ml.archetypes import kmeans, nearest_distance, weighted_sqdist


def test_partial_distance_skips_missing_and_rescales():
    a = np.array([[0.0, np.nan, 2.0]])
    b = np.array([[1.0, 5.0, 2.0]])
    d = weighted_sqdist(a, b, np.array([1.0, 1.0, 1.0]))
    assert d[0, 0] == pytest.approx(1.0 * 3 / 2)
    assert np.isnan(weighted_sqdist(np.array([[np.nan, 1.0]]), np.array([[1.0, np.nan]]), np.ones(2))[0, 0])


def test_kmeans_is_deterministic_and_separates_clusters():
    rng = np.random.default_rng(0)
    Z = np.vstack([rng.normal(0, 0.1, (50, 2)), rng.normal(5, 0.1, (50, 2))])
    l1, _ = kmeans(Z, 2, np.ones(2), seed=0)
    l2, _ = kmeans(Z, 2, np.ones(2), seed=0)
    np.testing.assert_array_equal(l1, l2)
    assert len(set(l1[:50])) == 1 and len(set(l1[50:])) == 1 and l1[0] != l1[-1]


def test_all_missing_row_has_no_archetype():
    Z = np.array([[0.0, 0.0], [np.nan, np.nan], [1.0, 1.0]])
    labels, _ = kmeans(Z, 2, np.ones(2), seed=0)
    assert labels[1] == -1


def test_nearest_distance_inf_without_evidence():
    q = np.array([[0.0, 0.0], [3.0, 4.0]])
    assert np.all(np.isinf(nearest_distance(q, np.empty((0, 2)), np.ones(2))))
    np.testing.assert_allclose(nearest_distance(q, np.array([[0.0, 0.0]]), np.ones(2)), [0.0, 5.0])
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.**

```python
"""Feature-space similarity (A:71-79, D1): areas are clustered into archetypes so an Alaska
alpine crag and a Rocky Mountain alpine crag can share evidence, and each query's distance to
the nearest incident-bearing area defines "feature evidence exists" (d*, A:150). Missing features
are skipped in the distance, never filled."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.ml.dataset import STATIC_FEATURES, CellData

ARCH_FEATURES = ("elevation_m", "lat", "tmax_jul", "tmin_jan", "precip_year", "snow_year", "freeze_thaw_year")


def area_feature_matrix(cells: CellData) -> NDArray[np.float64]:
    s = cells.area_static
    j = {f: i for i, f in enumerate(STATIC_FEATURES)}
    return np.column_stack([
        s[:, 0, j["elevation_m"]], s[:, 0, j["lat"]], s[:, 6, j["tmax_mean"]], s[:, 0, j["tmin_mean"]],
        np.log1p(s[:, :, j["precip_mm"]].sum(axis=1)), np.log1p(s[:, :, j["snowfall_cm"]].sum(axis=1)),
        s[:, :, j["freeze_thaw_days"]].sum(axis=1),
    ])  # a NaN month makes the annual sum NaN: missing, never a partial year read as a full one


@dataclass(frozen=True)
class Standardizer:
    mean: NDArray[np.float64]
    sd: NDArray[np.float64]

    @classmethod
    def fit(cls, F: NDArray[np.float64]) -> Standardizer:
        mean = np.nanmean(np.where(np.isnan(F).all(axis=0), 0.0, F), axis=0)
        sd = np.nanstd(F, axis=0)
        return cls(np.nan_to_num(mean), np.where((sd > 0) & np.isfinite(sd), sd, 1.0))

    def apply(self, F: NDArray[np.float64]) -> NDArray[np.float64]:
        return (F - self.mean) / self.sd


def weighted_sqdist(A: NDArray[np.float64], B: NDArray[np.float64], w: NDArray[np.float64]) -> NDArray[np.float64]:
    total = float(w.sum())
    num = np.zeros((A.shape[0], B.shape[0]))
    wsum = np.zeros_like(num)
    for f in range(A.shape[1]):
        a, b = A[:, f][:, None], B[:, f][None, :]
        both = ~np.isnan(a) & ~np.isnan(b)
        num += np.where(both, w[f] * (a - b) ** 2, 0.0)
        wsum += np.where(both, w[f], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(wsum > 0, num * total / wsum, np.nan)


def kmeans(Z: NDArray[np.float64], k: int, w: NDArray[np.float64], *, seed: int = 0,
           iters: int = 50) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    rng = np.random.default_rng(seed)
    usable = np.flatnonzero(~np.isnan(Z).all(axis=1))
    labels = np.full(Z.shape[0], -1, dtype=np.int64)
    k = min(k, usable.size)
    if k == 0:
        return labels, np.empty((0, Z.shape[1]))
    centers = [Z[usable[rng.integers(usable.size)]]]
    for _ in range(1, k):
        d = np.nanmin(weighted_sqdist(Z[usable], np.array(centers), w), axis=1)
        d = np.nan_to_num(d, nan=0.0)
        p = d / d.sum() if d.sum() > 0 else np.full(d.size, 1.0 / d.size)
        centers.append(Z[usable[rng.choice(usable.size, p=p)]])
    C = np.array(centers)
    for _ in range(iters):
        d = weighted_sqdist(Z[usable], C, w)
        new = np.nanargmin(np.where(np.isnan(d), np.inf, d), axis=1)
        if np.array_equal(new, labels[usable]):
            break
        labels[usable] = new
        for c in range(k):
            members = Z[usable[new == c]]
            if members.size:
                C[c] = np.where(np.isnan(members).all(axis=0), C[c], np.nanmean(members, axis=0))
    return labels, C


def nearest_distance(Zq: NDArray[np.float64], Zev: NDArray[np.float64], w: NDArray[np.float64], *,
                     chunk: int = 4096) -> NDArray[np.float64]:
    out = np.full(Zq.shape[0], np.inf)
    if Zev.shape[0] == 0:
        return out
    for s in range(0, Zq.shape[0], chunk):
        d = weighted_sqdist(Zq[s:s + chunk], Zev, w)
        d = np.where(np.isnan(d), np.inf, d)
        out[s:s + chunk] = np.sqrt(d.min(axis=1))
    return out
```

`np.nan_to_num` in `Standardizer.fit` and `kmeans` acts on distances and moments, not on model inputs. The Task 25 grep allowlists this file by name with that justification.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): archetypes and nearest-evidence distance`.

---

### Task 12: Stage-2 empirical Bayes with the geo/archetype blend

**Files:**
- Create: `backend/app/ml/eb.py`, `backend/tests/ml/test_eb.py`

**Interfaces:**
- Produces:
  - `MIN_PRIOR_SHAPE = 0.05`;
  - `KAPPA_MAX = 1e6`;
  - `GammaPost(shape: NDArray, rate: NDArray)` with `.mean`;
  - `mom_kappa(O, E, parent) -> float`;
  - `EbFit` (frozen), holding per level:
    - `state` / `region` / `area`: dicts `key -> (O, E, shape, rate)`;
    - `arch`: `(arch, g) -> (O, E, shape, rate)`;
    - the kappas `kappa_state`, `kappa_region`, `kappa_area`, `kappa_arch`, `kappa_cell`;
    - `blend: bool`;
  - `fit_eb(cells, y, mu0, arch_of_area, *, blend=True) -> EbFit`;
  - `EbFit.cell_posterior(cells, y_own, mu0, arch_of_area) -> CellPost`, where `CellPost(post: GammaPost, prior_mean, omega_cell, blend_geo, chain: dict[str, NDArray])`. Held-out cells pass `y_own = 0` and their own `mu0`, and their level keys absent from the fit inherit the parent (geo) or national (archetype).

Math (closed form; method of moments, Clayton–Kaldor style):
- At each level, groups i have `O_i = Σ y`, `E_i = Σ μ0` and a parent mean `p_i`.
- `s² = Σ E_i (O_i/E_i − p_i)² / Σ E_i`, `p̄ = Σ E_i p_i / Σ E_i`, and `τ² = s² − p̄ / mean(E_i)`.
- `κ = p̄/τ²`, or `KAPPA_MAX` if `τ² ≤ 0`.
- Posterior `Gamma(O_i + κ p_i, E_i + κ)`, with the shape floored so `κ p_i ≥ MIN_PRIOR_SHAPE`.
- Levels run top-down: national ratio `O/E`, then state, region and area.
- Archetype × group has the national ratio as its parent.
- Cell prior mean: `π_c = (w_g θ_area + w_a θ_arch)/(w_g + w_a)`, with `w = rate²/shape` (inverse Gamma variance). `w_a = 0` when `blend=False` or the area has no archetype.
- Cell `κ_c` is the MoM over cells with parent `π_c`, and the posterior is `Gamma(y_c + κ_c π_c, μ0_c + κ_c)`.

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np
import pytest

from app.ml.eb import MIN_PRIOR_SHAPE, fit_eb, mom_kappa
from app.ml.fixtures import synthetic_cells


def test_mom_kappa_hand_computed():
    O, E, p = np.array([2.0, 8.0]), np.array([4.0, 4.0]), np.array([1.0, 1.0])
    s2 = (4 * (0.5 - 1) ** 2 + 4 * (2 - 1) ** 2) / 8
    assert mom_kappa(O, E, p) == pytest.approx(1.0 / (s2 - 1.0 / 4.0))
    assert mom_kappa(np.array([4.0, 4.0]), E, p) == 1e6          # no extra variance: full pooling


def test_poisson_gamma_posterior_matches_hand_value():
    cells = synthetic_cells(seed=5, n_areas=30)
    mu0 = np.full(cells.n, 0.1)
    y = np.zeros(cells.n)
    y[0] = 3.0
    fit = fit_eb(cells, y, mu0, np.full(len(cells.area_id), -1), blend=False)
    post = fit.cell_posterior(cells, y, mu0, np.full(len(cells.area_id), -1))
    k, pi = fit.kappa_cell, post.prior_mean[0]
    assert post.post.mean[0] == pytest.approx((3.0 + max(k * pi, MIN_PRIOR_SHAPE)) / (0.1 + k))


def test_zero_incident_cell_shrinks_to_parent_not_zero():
    cells = synthetic_cells(seed=6, n_areas=30)
    mu0 = np.full(cells.n, 0.2)
    y = np.random.default_rng(0).poisson(0.2, cells.n).astype(float)
    y[cells.area_idx == 0] = 0.0
    fit = fit_eb(cells, y, mu0, np.zeros(len(cells.area_id), dtype=np.int64))
    post = fit.cell_posterior(cells, y, mu0, np.zeros(len(cells.area_id), dtype=np.int64))
    zero = cells.area_idx == 0
    assert np.all(post.post.mean[zero] > 0) and np.all(post.post.shape >= MIN_PRIOR_SHAPE)


def test_held_out_area_inherits_parent_and_archetype():
    cells = synthetic_cells(seed=7, n_areas=30)
    mu0 = np.full(cells.n, 0.2)
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    y = np.zeros(cells.n)
    y[cells.area_idx == 1] = 1.0                       # evidence only in area 1 (same archetype)
    train = cells.area_idx != 0
    fit = fit_eb(cells, np.where(train, y, 0.0), np.where(train, mu0, 0.0), arch)
    post = fit.cell_posterior(cells, np.zeros(cells.n), mu0, arch)
    assert np.all(post.post.mean[cells.area_idx == 0] > 0)
    geo_only = fit_eb(cells, np.where(train, y, 0.0), np.where(train, mu0, 0.0), arch, blend=False)
    g = geo_only.cell_posterior(cells, np.zeros(cells.n), mu0, arch)
    assert not np.allclose(post.prior_mean, g.prior_mean)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.**

```python
"""Stage 2 (P3:138 as amended by A:89-93): closed-form Poisson-Gamma empirical Bayes up the MP
area hierarchy, with each cell shrunk toward a precision-weighted blend of its geographic parent
and its feature archetype x type group. Held-out levels inherit their parent, so a region with no
training data still borrows from similar routes anywhere (D1)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.ml.cells import TYPE_GROUPS, region_key  # noqa: F401  (region keys come from CellData)
from app.ml.dataset import CellData

MIN_PRIOR_SHAPE = 0.05
KAPPA_MAX = 1e6
Level = dict[object, tuple[float, float, float, float]]


@dataclass(frozen=True)
class GammaPost:
    shape: NDArray[np.float64]
    rate: NDArray[np.float64]

    @property
    def mean(self) -> NDArray[np.float64]:
        return self.shape / self.rate


@dataclass(frozen=True)
class CellPost:
    post: GammaPost
    prior_mean: NDArray[np.float64]
    omega_cell: NDArray[np.float64]
    blend_geo: NDArray[np.float64]
    chain: dict[str, NDArray[np.float64]] = field(default_factory=dict)


def mom_kappa(O: NDArray[np.float64], E: NDArray[np.float64], parent: NDArray[np.float64]) -> float:
    ok = E > 0
    if ok.sum() < 2:
        return KAPPA_MAX
    O, E, parent = O[ok], E[ok], parent[ok]
    s2 = float(np.sum(E * (O / E - parent) ** 2) / E.sum())
    pbar = float(np.sum(E * parent) / E.sum())
    tau2 = s2 - pbar / float(E.mean())
    return KAPPA_MAX if tau2 <= 0 else min(pbar / tau2, KAPPA_MAX)


def _shape(O: float, kappa: float, parent: float) -> float:
    return O + max(kappa * parent, MIN_PRIOR_SHAPE)


def _level(keys: NDArray[np.int64], y: NDArray[np.float64], mu0: NDArray[np.float64],
           parent_of: dict[int, float]) -> tuple[Level, float]:
    uniq = np.unique(keys[keys >= 0])
    O = np.array([y[keys == k].sum() for k in uniq])
    E = np.array([mu0[keys == k].sum() for k in uniq])
    P = np.array([parent_of[int(k)] for k in uniq])
    kappa = mom_kappa(O, E, P)
    return ({int(k): (float(o), float(e), _shape(float(o), kappa, float(p)), float(e) + kappa)
             for k, o, e, p in zip(uniq, O, E, P)}, kappa)


@dataclass(frozen=True)
class EbFit:
    national: float
    state: Level
    region: Level
    area: Level
    arch: Level
    kappa_state: float
    kappa_region: float
    kappa_area: float
    kappa_arch: float
    kappa_cell: float
    blend: bool

    def _theta(self, level: Level, key: int, parent: float, kappa: float) -> tuple[float, float]:
        if key in level:
            _, _, shape, rate = level[key]
            return shape, rate
        return max(kappa * parent, MIN_PRIOR_SHAPE), kappa

    def cell_posterior(self, cells: CellData, y_own: NDArray[np.float64], mu0: NDArray[np.float64],
                       arch_of_area: NDArray[np.int64]) -> CellPost:
        n = cells.n
        prior = np.empty(n)
        bgeo = np.empty(n)
        chain = {k: np.empty(n) for k in ("omega_area", "omega_region", "omega_state", "omega_arch",
                                          "O_area", "O_region", "O_state", "O_arch")}
        for i in range(n):
            a = int(cells.area_idx[i])
            s_sh, s_rt = self._theta(self.state, int(cells.state_idx[a]), self.national, self.kappa_state)
            r_sh, r_rt = self._theta(self.region, int(cells.region_idx[a]), s_sh / s_rt, self.kappa_region)
            a_sh, a_rt = self._theta(self.area, a, r_sh / r_rt, self.kappa_area)
            wg = a_rt * a_rt / a_sh
            k = int(arch_of_area[a])
            if self.blend and k >= 0:
                key = k * len(TYPE_GROUPS) + int(cells.g[i])
                k_sh, k_rt = self._theta(self.arch, key, self.national, self.kappa_arch)
                wa = k_rt * k_rt / k_sh
            else:
                key, k_sh, k_rt, wa = -1, 1.0, 1.0, 0.0
            prior[i] = (wg * a_sh / a_rt + wa * k_sh / k_rt) / (wg + wa)
            bgeo[i] = wg / (wg + wa)
            for name, level, kk in (("area", self.area, a), ("region", self.region, int(cells.region_idx[a])),
                                    ("state", self.state, int(cells.state_idx[a])), ("arch", self.arch, key)):
                O, E, _, _ = level.get(kk, (0.0, 0.0, 0.0, 0.0))
                kap = {"area": self.kappa_area, "region": self.kappa_region, "state": self.kappa_state,
                       "arch": self.kappa_arch}[name]
                chain[f"omega_{name}"][i] = E / (E + kap)
                chain[f"O_{name}"][i] = O
        shape = y_own + np.maximum(self.kappa_cell * prior, MIN_PRIOR_SHAPE)
        rate = mu0 + self.kappa_cell
        return CellPost(GammaPost(shape, rate), prior, mu0 / (mu0 + self.kappa_cell), bgeo, chain)


def fit_eb(cells: CellData, y: NDArray[np.float64], mu0: NDArray[np.float64], arch_of_area: NDArray[np.int64],
           *, blend: bool = True) -> EbFit:
    national = float(y.sum() / mu0.sum()) if mu0.sum() > 0 else 1.0
    a_of_cell = cells.area_idx
    st_keys = cells.state_idx[a_of_cell]
    state, k_s = _level(st_keys, y, mu0, {int(k): national for k in np.unique(st_keys)})
    rg_keys = cells.region_idx[a_of_cell]
    rg_parent = {int(cells.region_idx[a]): state.get(int(cells.state_idx[a]), (0, 0, national, 1.0))[2]
                 / state.get(int(cells.state_idx[a]), (0, 0, national, 1.0))[3] for a in np.unique(a_of_cell)}
    region, k_r = _level(rg_keys, y, mu0, rg_parent)
    ar_parent = {int(a): region[int(cells.region_idx[a])][2] / region[int(cells.region_idx[a])][3]
                 for a in np.unique(a_of_cell)}
    area, k_a = _level(a_of_cell.astype(np.int64), y, mu0, ar_parent)
    arch_keys = np.where(arch_of_area[a_of_cell] >= 0, arch_of_area[a_of_cell] * len(TYPE_GROUPS) + cells.g, -1)
    arch, k_k = _level(arch_keys.astype(np.int64), y, mu0, {int(k): national for k in np.unique(arch_keys)})
    partial = EbFit(national, state, region, area, arch, k_s, k_r, k_a, k_k, 1.0, blend)
    prior = partial.cell_posterior(cells, y, mu0, arch_of_area).prior_mean
    k_c = mom_kappa(y, mu0, prior)
    return EbFit(national, state, region, area, arch, k_s, k_r, k_a, k_k, k_c, blend)
```

Notes for the implementer:
- `_level` with zero-E groups (a held-out region's cells passed with `mu0 = 0`) still records the group with `O = E = 0`, so its posterior equals the prior. That is the intended inheritance.
- Fit-time callers pass training `y` and `mu0`, masked to 0 for held-out cells (Task 15).
- The per-cell Python loop is O(n). At ~450K cells it runs in seconds to tens of seconds; Task 21's capacity benchmark measures it. If the benchmark exceeds budget, vectorize it with `np.searchsorted` lookups. The loop is kept here for readability, and the behaviour is pinned by these tests.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): EB with geo/archetype blend`.

---

### Task 13: Posterior summaries: RR, ranges, percentiles, confidence, evidence, per-10K

**Files:**
- Create: `backend/app/ml/posterior.py`, `backend/tests/ml/test_posterior.py`
- Modify: `backend/pyproject.toml` (`[dependency-groups] train = ["scipy==<latest 1.x on PyPI at execution, pinned>"]`; add `hypothesis` to `dev`; `uv lock`). Pin the exact versions read from PyPI on the day; do not guess them here.

**Interfaces:**
- Consumes: `CellPost` (Task 12), `Design` (Task 8), and the GLM `coef`.
- Produces:
  - `Thresholds(d_star: float, conf_high: float, conf_medium: float, phi: float)`;
  - `CellSummary` (arrays per cell): `rr_mean`, `rr_q05`, `rr_q95`, `percentile`, `pct_q05`, `pct_q95`, `conf_level` (object array of str), `insufficient` (bool), `n_eff_geo`, `n_eff_feature`, `per10k_mean`, `per10k_q05`, `per10k_q95`, `per10k_reason`;
  - `summarize(cells, design, coef, cp: CellPost, *, thresholds, evidence_dist, years, geo_evidence) -> CellSummary`.

Definitions:
- **Hazard and RR.** `η_c = X_c·coef` and `η_exp = X_c[exposure_cols]·coef[exposure_cols]`; `r_c = θ_c · exp(η_c − η_exp)` (risk per unit exposure). `r_ref(g,m) = Σ μ0θ / Σ exp(η_exp)` over cells in (g,m). `RR = r / r_ref`.
- **Interval.** The φ-widened posterior `Gamma(shape/φ, rate/φ)`; quantiles via `scipy.special.gammaincinv`.
- **Percentile.** Route-weighted (`n_routes`), within (g, m), among sufficient cells only: `100·(count(<x) + 0.5·count(=x))/N`. `pct_q05`/`pct_q95` place `RR_q05`/`RR_q95` against the same reference.
- **Insufficient (A:145–150).** `geo_evidence[c]` is false (no incident at cell, area, region or state level in training, state-only mass included) **and** `evidence_dist[c] > d_star`.
- **Confidence (A:100–103).** `ratio = q95/q05`: `high` if ≤ `conf_high`, `medium` if ≤ `conf_medium`, else `low`.
- **n_eff (A:99).**
  - `n_eff_geo = ω_c y_c + (1−ω_c)·b·(ω_a O_a + (1−ω_a)(ω_r O_r + (1−ω_r) ω_s O_s))`;
  - `n_eff_feature = (1−ω_c)(1−b)·ω_k O_k`.
- **Per-10K (A:116–118)** `[default pending: DP13]`. `1e4 · (μ0 θ / years) / ticks_value`, only where `tick_coverage == 1` and `ticks_value > 0`. Otherwise `per10k_reason` is `ticks_incomplete` (coverage < 1), `ticks_missing` (NaN) or `no_ticks_logged` (0).

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from app.ml.eb import GammaPost, MIN_PRIOR_SHAPE
from app.ml.posterior import Thresholds, gamma_interval, percentile_in, summarize
from app.ml.fixtures import fitted_toy


@settings(max_examples=200, deadline=None)
@given(shape_extra=st.floats(0, 50), rate=st.floats(1e-3, 1e3), phi=st.floats(1.0, 4.0))
def test_interval_brackets_the_mean_even_at_tiny_shape(shape_extra, rate, phi):
    shape = MIN_PRIOR_SHAPE + shape_extra
    lo, mean, hi = gamma_interval(np.array([shape]), np.array([rate]), phi)
    assert 0 < lo[0] <= mean[0] <= hi[0]


def test_percentile_is_mid_rank_with_ties():
    ref, w = np.array([1.0, 2.0, 2.0, 3.0]), np.ones(4)
    assert percentile_in(np.array([2.0]), ref, w)[0] == pytest.approx(50.0)
    assert percentile_in(np.array([0.5]), ref, w)[0] == 0.0 and percentile_in(np.array([9.0]), ref, w)[0] == 100.0


def test_no_local_incident_borrows_from_archetype():
    toy = fitted_toy(local_incidents=0, archetype_incidents=5)
    s = summarize(**toy.inputs(thresholds=Thresholds(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)))
    i = toy.target_cell
    assert not s.insufficient[i] and s.conf_level[i] in ("low", "medium") and s.rr_mean[i] > 0
    assert s.n_eff_geo[i] == pytest.approx(0.0) and s.n_eff_feature[i] > 0


def test_insufficient_only_without_geo_and_feature_evidence():
    toy = fitted_toy(local_incidents=0, archetype_incidents=0)
    s = summarize(**toy.inputs(thresholds=Thresholds(d_star=0.5, conf_high=3.0, conf_medium=8.0, phi=1.0),
                               evidence_far=True))
    i = toy.target_cell
    assert s.insufficient[i] and s.conf_level[i] == "insufficient"
    assert np.isnan(s.rr_mean[i]) and np.isnan(s.percentile[i])


def test_raising_density_with_fixed_incidents_never_raises_rr():
    lo = fitted_toy(local_incidents=2, archetype_incidents=2, density=5)
    hi = fitted_toy(local_incidents=2, archetype_incidents=2, density=50)
    th = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)
    assert summarize(**hi.inputs(thresholds=th)).rr_mean[hi.target_cell] <= \
        summarize(**lo.inputs(thresholds=th)).rr_mean[lo.target_cell] + 1e-12


def test_per10k_only_at_full_tick_coverage():
    toy = fitted_toy(local_incidents=1, archetype_incidents=1, tick_coverage=0.5)
    s = summarize(**toy.inputs(thresholds=Thresholds(1.0, 3.0, 8.0, 1.0)))
    assert np.isnan(s.per10k_mean[toy.target_cell]) and s.per10k_reason[toy.target_cell] == "ticks_incomplete"
```

`app.ml.fixtures.fitted_toy(...)` is added in this task. It builds a small synthetic `CellData`, a `Design` and a coefficient vector (density coefficient fixed positive), runs `fit_eb` with a chosen archetype layout, and returns an object with:
- `.target_cell`;
- `.inputs(thresholds, evidence_far=False)`, the keyword dict `summarize` takes.

All values are synthetic.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `backend/app/ml/posterior.py`:

```python
"""From EB posteriors to what a route shows (A:95-110, A:112-131): RR with a 90% interval,
national within-type-group percentiles, the confidence level, the evidence split, and the
per-10,000-logged-ascents line where ticks are complete. Insufficient means no geo evidence and
no feature neighbour within d* (A:145-150), nothing else."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.special import gammaincinv

from app.ml.cells import TYPE_GROUPS
from app.ml.dataset import CellData
from app.ml.eb import CellPost
from app.ml.features import Design


@dataclass(frozen=True)
class Thresholds:
    d_star: float
    conf_high: float
    conf_medium: float
    phi: float


@dataclass(frozen=True)
class CellSummary:
    rr_mean: NDArray[np.float64]
    rr_q05: NDArray[np.float64]
    rr_q95: NDArray[np.float64]
    percentile: NDArray[np.float64]
    pct_q05: NDArray[np.float64]
    pct_q95: NDArray[np.float64]
    conf_level: NDArray[np.object_]
    insufficient: NDArray[np.bool_]
    n_eff_geo: NDArray[np.float64]
    n_eff_feature: NDArray[np.float64]
    per10k_mean: NDArray[np.float64]
    per10k_q05: NDArray[np.float64]
    per10k_q95: NDArray[np.float64]
    per10k_reason: NDArray[np.object_]


def gamma_interval(shape: NDArray[np.float64], rate: NDArray[np.float64],
                   phi: float) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    a, b = shape / phi, rate / phi
    return gammaincinv(a, 0.05) / b, shape / rate, gammaincinv(a, 0.95) / b


def percentile_in(x: NDArray[np.float64], ref: NDArray[np.float64], w: NDArray[np.float64]) -> NDArray[np.float64]:
    order = np.argsort(ref)
    r = ref[order]
    c0 = np.concatenate([[0.0], np.cumsum(w[order])])
    below, upto = c0[np.searchsorted(r, x, "left")], c0[np.searchsorted(r, x, "right")]
    return np.asarray(100.0 * (below + 0.5 * (upto - below)) / c0[-1], dtype=np.float64)


def summarize(cells: CellData, design: Design, coef: NDArray[np.float64], cp: CellPost, *,
              thresholds: Thresholds, evidence_dist: NDArray[np.float64], years: float,
              geo_evidence: NDArray[np.bool_]) -> CellSummary:
    n = cells.n
    eta = design.X @ coef
    eta_exp = design.X[:, list(design.exposure_cols)] @ coef[list(design.exposure_cols)]
    mu0 = np.exp(eta)
    lo, mean, hi = gamma_interval(cp.post.shape, cp.post.rate, thresholds.phi)
    hazard = np.exp(eta - eta_exp)
    insufficient = ~geo_evidence & (evidence_dist > thresholds.d_star)
    rr = np.full((3, n), np.nan)
    pct = np.full((3, n), np.nan)
    for gi in range(len(TYPE_GROUPS)):
        for m in range(1, 13):
            sel = (cells.g == gi) & (cells.month == m)
            if not sel.any():
                continue
            r_ref = float((mu0[sel] * mean[sel]).sum() / np.exp(eta_exp[sel]).sum())
            for k, theta in enumerate((lo, mean, hi)):
                rr[k, sel] = theta[sel] * hazard[sel] / r_ref
            ok = sel & ~insufficient
            if ok.any():
                for k in range(3):
                    pct[k, sel & ~insufficient] = percentile_in(rr[k, sel & ~insufficient], rr[1, ok],
                                                                cells.n_routes[ok])
    rr[:, insufficient] = np.nan
    ratio = rr[2] / rr[0]
    conf = np.where(insufficient, "insufficient",
                    np.where(ratio <= thresholds.conf_high, "high",
                             np.where(ratio <= thresholds.conf_medium, "medium", "low"))).astype(object)
    ch, b, wc = cp.chain, cp.blend_geo, cp.omega_cell
    geo_inner = ch["omega_area"] * ch["O_area"] + (1 - ch["omega_area"]) * (
        ch["omega_region"] * ch["O_region"] + (1 - ch["omega_region"]) * ch["omega_state"] * ch["O_state"])
    y_own = cp.post.shape - np.maximum(cp.post.rate - mu0, 0.0) * cp.prior_mean  # = y_c (shape - kappa*pi)
    n_geo = wc * np.maximum(y_own, 0.0) + (1 - wc) * b * geo_inner
    n_feat = (1 - wc) * (1 - b) * ch["omega_arch"] * ch["O_arch"]
    per_year = mu0 * np.vstack([lo, mean, hi]) / years
    full = (cells.tick_coverage == 1.0) & (cells.ticks_value > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        p10 = np.where(full & ~insufficient, 1e4 * per_year / cells.ticks_value, np.nan)
    reason = np.where(insufficient | full, None,
                      np.where(np.isnan(cells.ticks_value),
                               "ticks_missing",
                               np.where(cells.tick_coverage < 1.0, "ticks_incomplete", "no_ticks_logged"))).astype(object)
    return CellSummary(rr[1], rr[0], rr[2], pct[1], pct[0], pct[2], conf, insufficient,
                       np.where(insufficient, np.nan, n_geo), np.where(insufficient, np.nan, n_feat),
                       p10[1], p10[0], p10[2], reason)
```

The `y_own` reconstruction relies on `rate − μ0 = κ_c`, so `shape − κ_c π_c = y_c` (above the shape floor). If the floor bound, `y_own` slightly understates. n_eff is labelled "≈" in the UI, so this is acceptable; the comment on that line says so.

The `ticks_missing` reason applies when `tick_coverage` is NaN or `ticks_value` is NaN. The `ticks_incomplete` reason applies when `tick_coverage < 1` and ticks are present. Add a test row for each.

- [ ] **Step 4: Run** → PASS (including the Hypothesis property). **Step 5: Commit** `feat(ml): posterior summaries, confidence, evidence, per-10K`.

---

### Task 14: `explain()`: term decomposition of the stored number

**Files:**
- Create: `backend/app/ml/explain.py`, `backend/tests/ml/test_explain.py`

**Interfaces:**
- Produces:
  - `Term(name: str, label: str, log_effect: float)`;
  - `explain(bundle: ModelBundle, *, type_group: str, month: int, raw_features: Mapping[str, float | None], rr_mean: float, n_eff_geo: float, n_eff_feature: float) -> list[Term]`. It returns the top 3 non-exposure terms by |effect| plus one evidence term. The terms sum, with the bundle's reference constant for (g, m), to `log(rr_mean)` within 1e-6 (parity anchor).
  - `LABELS: dict[str, str]`: plain language, for example `z_elevation_m` → "Elevation", `z_freeze_thaw_days` → "Freeze-thaw days this month", `d_*`/`m_*`/`a_*` → "Season for this route type", and evidence → "Reported incidents here and on similar routes". Labels never say "safe".

The bundle carries:
- `coef`, `names`, `center`, `scale`, `tick_ref`, `families_on`;
- `log_rref` (6×12): the per-(g,m) `log r_ref`;
- `hazard_center`: the reference linear predictor removed so the terms are relative to the national typical route of that type and month.

The evidence term is `log(rr_mean) − Σ hazard terms − const(g,m)`, so the decomposition is exact by construction. The test checks it against an independent recomputation from Task 13's `summarize` on the toy fixture.

- [ ] **Step 1: Write the failing test.**

```python
import math

import pytest

from app.ml.explain import explain
from app.ml.fixtures import toy_bundle_and_row


def test_terms_reconstruct_the_stored_rr_and_name_no_exposure():
    bundle, row = toy_bundle_and_row()
    terms = explain(bundle, **row)
    assert len(terms) == 4 and terms[-1].name == "evidence"
    assert not any(t.name in ("x_density", "x_season", "x_ticks") for t in terms)
    const = bundle.manifest["const"][row["type_group"]][str(row["month"])]
    full = explain(bundle, **row, top=None)
    assert sum(t.log_effect for t in full) + const == pytest.approx(math.log(row["rr_mean"]), abs=1e-6)
    assert all("safe" not in t.label.lower() for t in full)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `explain()`:
1. Rebuilds the route's hazard covariates from `raw_features` with the bundle's `center`/`scale`. It uses the same transforms as `features.build_design`; import the private helpers it needs by extracting `features.transform_static(raw: Mapping[str, float | None]) -> dict[str, float]` in this task as a refactor of Task 8, covered by Task 8's tests.
2. Multiplies by `coef` for each non-exposure column.
3. Computes the evidence term as the residual to `log(rr_mean) − const(g,m)`.
4. Sorts the hazard terms by |effect|, returns the top three plus evidence, or all of them when `top=None`.

`const(g, m) = −log_rref[g, m]`, written into `bundle.manifest["const"]` by `model.to_bundle` (Task 15).

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): explain() decomposes the stored RR exactly`.

---

### Task 15: `fit_model` / `predict`, bundle export

**Files:**
- Create: `backend/app/ml/model.py`, `backend/tests/ml/test_model.py`

**Interfaces:**
- Consumes: Tasks 7, 8 and 10–13.
- Produces:
  - `ModelParams(k_arch: int = 16, arch_weights: tuple[float,...] = (1,)*7, blend: bool = True, lambdas: Lambdas = Lambdas(), tick_policy: TickPolicy = TickPolicy(), seed: int = 0)`;
  - `Fitted(design: Design, glm: GlmFit, eb: EbFit, arch_of_area: NDArray, std: Standardizer, ev_points: NDArray, params: ModelParams, years: float)`;
  - `fit_model(cells, counts, params, *, train_mask: NDArray[bool] | None = None) -> Fitted`. `train_mask` masks held-out cells: their y and μ0 enter neither stage.
  - `Fitted.predict(cells, y_own) -> tuple[NDArray mu0, CellPost, NDArray evidence_dist, NDArray geo_evidence]`;
  - `Fitted.summarize(cells, y_own, thresholds) -> CellSummary`;
  - `Fitted.to_bundle(version, thresholds, cells) -> ModelBundle`.

`geo_evidence` is true when the cell's area, region or state holds training incidents, or the state's `state_mass` is > 0. `ev_points` are the standardized archetype features of areas whose (area, g) holds training incidents, per group. `evidence_dist` is the distance from the cell's area to the nearest such area of the same g.

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np

from app.ml.fixtures import synthetic_problem
from app.ml.model import ModelParams, fit_model


def test_fit_is_deterministic_and_row_order_invariant():
    cells, counts = synthetic_problem(seed=11)
    a = fit_model(cells, counts, ModelParams()).predict(cells, counts.y)[1].post.mean
    b = fit_model(cells, counts, ModelParams()).predict(cells, counts.y)[1].post.mean
    np.testing.assert_array_equal(a, b)


def test_held_out_cells_do_not_see_their_own_counts():
    cells, counts = synthetic_problem(seed=12)
    mask = cells.state_idx[cells.area_idx] != 0
    fit = fit_model(cells, counts, ModelParams(), train_mask=mask)
    bumped = counts.y.copy()
    bumped[~mask] += 100.0
    fit2 = fit_model(cells, type(counts)(bumped, counts.state_mass, counts.years, counts.dropped, counts.placed),
                     ModelParams(), train_mask=mask)
    np.testing.assert_allclose(fit.glm.coef, fit2.glm.coef)


def test_pooling_beats_geo_only_on_feature_driven_synthetic_risk():
    cells, counts, truth = synthetic_problem(seed=13, return_truth=True)
    mask = cells.state_idx[cells.area_idx] != 0
    pooled = fit_model(cells, counts, ModelParams(), train_mask=mask)
    geo = fit_model(cells, counts, ModelParams(blend=False), train_mask=mask)
    zero = np.zeros(cells.n)
    err = lambda f: np.abs(np.log(f.predict(cells, zero)[1].post.mean[~mask]) - np.log(truth[~mask])).mean()  # noqa: E731
    assert err(pooled) < err(geo)
```

`synthetic_problem(seed, return_truth=False)` (in `fixtures.py`) draws true per-cell rates from a feature-driven log-linear model, with archetype-level effects shared across states, plus noise. It then draws Poisson counts and returns `CellData`, `Counts` and (optionally) the true rates. This is the synthetic ground truth CI backtests on (Task 20).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `model.py`:
1. **`fit_model`:** builds the design, then calls `fit_poisson(design.X[mask], counts.y[mask], design.penalty, nonneg=design.nonneg)`.
2. Computes `mu0 = exp(X @ coef)` for all cells.
3. Builds the area feature matrix and standardizer from the training areas.
4. Runs `kmeans` with `params.k_arch`, `np.array(params.arch_weights)` and `params.seed` on the training areas' standardized features, then assigns every area (training and held-out) to its nearest center with `weighted_sqdist` (−1 when all its features are missing).
5. Fits `fit_eb(cells, y·mask, mu0·mask, arch_of_area, blend=params.blend)`.
6. Builds evidence points per group from training cells with `y > 0`.
7. **`predict`:** calls `eb.cell_posterior` with the given `y_own`, and computes `nearest_distance` per group and `geo_evidence` from `eb` levels (`O > 0`) and `counts.state_mass`.
8. **`to_bundle`:** writes into the bundle:
   - arrays: `coef`, `center`, `scale`, `arch_centers`, `arch_weights`, `std_mean`, `std_sd`, `log_rref` (6×12, from the summaries of all cells);
   - manifest: `names`, `tick_ref`, `families_on`, `const`, `thresholds`, `params`, `years`, `kappas`.

It does not include per-cell posteriors; those live in `model.route_scores`.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): fit/predict orchestration and bundle export`.
- [ ] **Step 6: PR 3-2 docs.** Add a CHANGELOG entry. Create `docs/model-card.md` as a skeleton with headings filled by Task 20's generator (data window, event counts toward the v2.3 gate (P3:416), coverage by state and type group, gate metrics with and without MP ticks / ski approach / LLM links, G1–G7, thresholds).

---

# PR 3-3 — `feat/p3-backtest-gate`

### Task 16: Metrics

**Files:**
- Create: `backend/app/ml/metrics.py`, `backend/tests/ml/test_metrics.py`

**Interfaces:**
- Produces:
  - `nb_logpmf(k, shape, rate, scale) -> NDArray`: the predictive count over the test window, `θ ~ Gamma(shape, rate)`, `N | θ ~ Poisson(θ·scale)`, where `scale = μ0·τ`. It is continuous in k, so fractional counts score.
  - `mean_log_score(...) -> float`;
  - `poisson_deviance(k, mu) -> float`;
  - `Calibration(scheme: Literal["decile","quintile","insufficient_support"], bins: list[tuple[float,float,float]], n_pass: int, passed: bool)`, and `calibration(pred, obs, *, lo=0.8, hi=1.25, min_expected=10.0) -> Calibration`: 8 of 10 deciles in range, else quintiles needing 4 of 5 (P3:275);
  - `top_capture(pred, obs, frac=0.10) -> float`: tie-aware (tied mass at the cut splits proportionally, P3:383);
  - `pit_coverage(k, shape, rate, scale, *, seed=0) -> float`: randomized PIT on `floor(k)`, the share in [0.05, 0.95];
  - `oe_ci(O, E, level=0.90) -> tuple[float,float,float]`: exact Poisson;
  - `spearman(a, b) -> float | None`;
  - `MetricsReport = dict[str, object]`, with keys `log_score`, `deviance`, `top10`, `calibration`, `coverage`, `oe`, `n_cells`, `observed`, `expected`.

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np
import pytest
from scipy.stats import nbinom

from app.ml.metrics import calibration, nb_logpmf, oe_ci, pit_coverage, spearman, top_capture


def test_nb_logpmf_matches_scipy_on_integers():
    k, shape, rate, scale = np.array([0.0, 1.0, 4.0]), np.array([2.0]), np.array([3.0]), np.array([1.5])
    p = rate / (rate + scale)
    np.testing.assert_allclose(nb_logpmf(k, shape, rate, scale), nbinom.logpmf(k, shape, p), rtol=1e-10)


def test_top_capture_splits_ties():
    pred = np.array([5.0, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    obs = np.array([1.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 1.0])
    assert top_capture(pred, obs, frac=0.10) == pytest.approx(0.25)   # one of two tied cells, half the incident


def test_calibration_falls_back_to_quintiles_then_reports_no_support():
    rng = np.random.default_rng(0)
    pred = rng.gamma(2.0, 1.0, 2000)
    obs = rng.poisson(pred).astype(float)
    assert calibration(pred, obs).scheme == "decile" and calibration(pred, obs).passed
    small = calibration(pred[:60] / 50, obs[:60] * 0)
    assert small.scheme == "insufficient_support" and not small.passed


def test_pit_coverage_near_nominal_for_correct_model():
    rng = np.random.default_rng(1)
    shape, rate, scale = np.full(5000, 3.0), np.full(5000, 2.0), np.full(5000, 1.0)
    k = rng.poisson(rng.gamma(shape, 1 / rate) * scale).astype(float)
    assert 0.86 <= pit_coverage(k, shape, rate, scale) <= 0.94


def test_oe_ci_and_spearman():
    lo, oe, hi = oe_ci(10.0, 10.0)
    assert lo < 1.0 == oe < hi
    assert spearman(np.array([1, 2, 3.0]), np.array([3, 2, 1.0])) == pytest.approx(-1.0)
    assert spearman(np.array([1.0, 1.0]), np.array([1.0, 2.0])) is None
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.**

```python
"""Held-out metrics (P3:386-392, A:154-173). Every metric takes predictive Gamma posteriors so the
log score scores the full NegBin predictive, not a point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.special import betainc, gammaincinv, gammaln

Arr = NDArray[np.float64]


def nb_logpmf(k: Arr, shape: Arr, rate: Arr, scale: Arr) -> Arr:
    p = rate / (rate + scale)
    return np.asarray(gammaln(k + shape) - gammaln(shape) - gammaln(k + 1.0) + shape * np.log(p)
                      + k * np.log1p(-p), dtype=np.float64)


def mean_log_score(k: Arr, shape: Arr, rate: Arr, scale: Arr) -> float:
    return float(np.mean(nb_logpmf(k, shape, rate, scale)))


def poisson_deviance(k: Arr, mu: Arr) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        term = np.where(k > 0, k * np.log(k / mu), 0.0)
    return float(2.0 * np.sum(term - (k - mu)))


@dataclass(frozen=True)
class Calibration:
    scheme: Literal["decile", "quintile", "insufficient_support"]
    bins: list[tuple[float, float, float]]
    n_pass: int
    passed: bool


def _bins(pred: Arr, obs: Arr, n: int) -> list[tuple[float, float, float]]:
    order = np.argsort(pred, kind="stable")
    out = []
    for chunk in np.array_split(order, n):
        e, o = float(pred[chunk].sum()), float(obs[chunk].sum())
        out.append((e, o, o / e if e > 0 else float("nan")))
    return out


def calibration(pred: Arr, obs: Arr, *, lo: float = 0.8, hi: float = 1.25, min_expected: float = 10.0) -> Calibration:
    for scheme, n, need in (("decile", 10, 8), ("quintile", 5, 4)):
        bins = _bins(pred, obs, n)
        if all(e >= min_expected for e, _, _ in bins):
            k = sum(lo <= r <= hi for _, _, r in bins)
            return Calibration(scheme, bins, k, k >= need)  # type: ignore[arg-type]
    return Calibration("insufficient_support", _bins(pred, obs, 5), 0, False)


def top_capture(pred: Arr, obs: Arr, frac: float = 0.10) -> float:
    total = float(obs.sum())
    if total == 0:
        return float("nan")
    budget = frac * pred.size
    captured, used = 0.0, 0.0
    for value in np.unique(pred)[::-1]:
        tied = pred == value
        n = float(tied.sum())
        take = min(n, budget - used)
        if take <= 0:
            break
        captured += float(obs[tied].sum()) * take / n
        used += take
    return captured / total


def _nb_cdf(k: Arr, shape: Arr, rate: Arr, scale: Arr) -> Arr:
    p = rate / (rate + scale)
    return np.where(k < 0, 0.0, betainc(shape, np.floor(k) + 1.0, p))


def pit_coverage(k: Arr, shape: Arr, rate: Arr, scale: Arr, *, seed: int = 0) -> float:
    v = np.random.default_rng(seed).uniform(size=k.size)
    kk = np.floor(k)
    lo, hi = _nb_cdf(kk - 1.0, shape, rate, scale), _nb_cdf(kk, shape, rate, scale)
    u = lo + v * (hi - lo)
    return float(np.mean((u >= 0.05) & (u <= 0.95)))


def oe_ci(O: float, E: float, level: float = 0.90) -> tuple[float, float, float]:
    a = (1.0 - level) / 2.0
    lo = 0.0 if O == 0 else float(gammaincinv(O, a))
    hi = float(gammaincinv(O + 1.0, 1.0 - a))
    return lo / E, O / E, hi / E


def spearman(a: Arr, b: Arr) -> float | None:
    if a.size < 3 or np.unique(a).size < 2 or np.unique(b).size < 2:
        return None
    from app.pipelines.exposure_math import spearman as tie_aware  # the exposure plan's tie-aware version
    return tie_aware(a.tolist(), b.tolist())
```

`app.pipelines.exposure_math.spearman` exists once the exposure plan's Task 5 has merged (a prerequisite). The two-element case returns `None` there too.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): held-out metrics`.

---

### Task 17: Backtest harness, references, sensitivity variants, kernel adapter, prospective part

**Files:**
- Create: `backend/app/ml/backtest.py`, `backend/app/ml/kernel_adapter.py`, `backend/tests/ml/test_backtest.py`, `backend/tests/ml/test_kernel_adapter.py`

**Interfaces:**
- Consumes: Tasks 6–9, 15 and 16.
- Produces:
  - `RawData(placements: list[Placement], areas: list[AreaInfo], paths: dict[int,str], static: list[StaticRow], exposure_by_cutoff: dict[date, list[ExposureRow]], production_cutoff: date)`, loaded once by `load_raw(conn, cutoffs, *, today)` (SQL in `dataset`);
  - `reweight(raw, mult_by_state: Mapping[str, float]) -> RawData`: multiplies each placement's `weight` by its state's multiplier (the stability bootstrap);
  - `fit_full(raw, params, *, cutoff: date | None) -> tuple[Fitted, CellData, Counts]`: every cell is training, and `cutoff=None` means `raw.production_cutoff`;
  - `FoldData`, built by `prepare(raw, fold, *, exclude_flags=frozenset(), mp_ticks=True) -> FoldData`, with fields:
    - `cells: CellData`;
    - `train: Counts` (over the fold's train window);
    - `test: Counts`;
    - `train_mask`: cells not in held-out states;
    - `test_mask`;
    - `scale_tau: float`;
  - `FoldResult(fold, shape, rate, scale, obs, mu_point, conf, insufficient, zero_history, group, routes, evidence_dist, geo_evidence)` over test cells, with numpy arrays except `fold`;
  - `run_fold(fd, params, thresholds, *, stage1_cache: dict[str, Fitted] | None = None) -> FoldResult`. The cache is keyed by fold name plus `lambdas`/`tick_policy`, and reuses the stage-1 GLM when only stage-2 parameters change, through `fit_model(..., reuse_stage1=…)` (Task 18);
  - `report(results: list[FoldResult]) -> MetricsReport`, pooled, with sub-reports `by_kind`, `by_group`, `by_conf`, `zero_history` and `bands`. `bands` is G7 (A:169–173): for the 25/50/75 scheme and the P3 percentile scheme (<25, 25–75, 75–90, ≥90), it holds per-band O and E, O/E with `oe_ci`, monotone-rise and all-bands-in-[0.8, 1.25] flags, and a band-level log score. It is reported only; switching schemes is an owner-reviewed change (D3);
  - `run_suite(raw, params, thresholds, *, cutoff) -> Suite`, where `Suite` holds:
    - `main`: spatial + rolling;
    - `geo_only` (G1 reference): `blend=False`;
    - `permuted` (G3): area static rows permuted across state groups with a fixed seed;
    - sensitivities `no_ski`, `no_llm`, `no_mp_ticks`;
    - `prospective` (part 4) when test incidents exist in the window, else `None` with a reason;
    - `recent` (part 3): the 2019–2024 holdout reported with `oe_ci`, never gated.
  - `kernel_adapter.kernel_cell_scores(cells, placements, areas, *, train_end, reference_year) -> NDArray` and `kernel_report(raw, folds) -> MetricsReport`.

Rules:
- **τ.** For spatial folds τ = 1, since test cells share the training window; held-out cells use their own μ0 from a GLM fit without them. For rolling folds, `τ = test_years / train_years × level`. `level` is the national assigned-incident rate over the last 5 training years ÷ the training-window mean, which corrects for reporting-volume drift without reading the test window.
- **Rolling-fold cells** use `exposure_by_cutoff[fold.exposure_cutoff]`. A missing snapshot raises `StaleExposure` naming the `exposure build --cutoff` command.
- **Zero-history stratum (G2).** Test cells whose own training count is 0 **and** whose area had no training incident.
- **Kernel (P3:392).** The vectorized kernel's math is called per (area, type group, month) at `date(reference_year, m, 15)` with training-fold accidents only. Every accident gets `weather_pattern=None` (the kernel treats a missing pattern as neutral). The current pattern is a flat week built from the cell's monthly normals, or a 10 °C/0 mm week where normals are missing. `rock` is passed as `"trad"`, as the kernel scored unknown routes (A:32). Its log score uses a Poisson calibration map `count ~ a + b·log1p(score)` fit on the fold's training cells only. Top-10% capture uses the raw scores.

- [ ] **Step 1: Write the failing tests.** `backend/tests/ml/test_backtest.py`:

```python
from datetime import date

import numpy as np
import pytest

from app.ml.backtest import prepare, report, run_fold, run_suite
from app.ml.fixtures import synthetic_raw
from app.ml.folds import rolling_folds, spatial_folds
from app.ml.model import ModelParams
from app.ml.posterior import Thresholds

TH = Thresholds(d_star=2.0, conf_high=3.0, conf_medium=8.0, phi=1.5)


def test_spatial_fold_never_trains_on_held_out_states():
    raw = synthetic_raw(seed=21)
    fold = spatial_folds(date(2026, 10, 1))[0]
    fd = prepare(raw, fold)
    held = np.isin(np.array(fd.cells.states)[fd.cells.state_idx[fd.cells.area_idx]], sorted(fold.held_out_states))
    assert not (fd.train_mask & held).any() and fd.test_mask[held].all() and fd.scale_tau == 1.0


def test_rolling_fold_counts_split_at_origin_and_scale_by_years():
    raw = synthetic_raw(seed=22)
    fold = rolling_folds()[0]
    fd = prepare(raw, fold)
    assert fd.train.years == pytest.approx((fold.train_end - fold.train_start).days / 365.25)
    assert 0 < fd.scale_tau < 1


def test_report_has_strata_and_pooling_beats_geo_only_on_synthetic():
    raw = synthetic_raw(seed=23)
    suite = run_suite(raw, ModelParams(), TH, cutoff=date(2026, 10, 1))
    assert {"by_kind", "by_group", "by_conf", "zero_history", "bands"} <= set(suite.main)
    assert set(suite.main["bands"]) == {"quartile_25_50_75", "p3_percentile"}
    assert suite.main["log_score"] > suite.geo_only["log_score"]
    assert suite.permuted["log_score"] < suite.main["log_score"]
    assert suite.prospective is None or "top10" in suite.prospective


def test_missing_backtest_snapshot_names_the_command():
    raw = synthetic_raw(seed=24, drop_cutoffs=(date(2004, 1, 1),))
    with pytest.raises(Exception, match=r"exposure build --cutoff 2004-01-01"):
        prepare(raw, rolling_folds()[0])
```

`backend/tests/ml/test_kernel_adapter.py` checks:
- the adapter calls the kernel with `weather_pattern=None` accidents;
- it never passes a test-window accident (monkeypatch `calculate_safety_score_vectorized` to record its `accidents` argument and assert every `accident_date < train_end`);
- the calibration map is fit only on training cells.

`fixtures.synthetic_raw(seed, drop_cutoffs=())` builds:
- synthetic placements over 1990–2026 (day, month and year-interval precisions, some ski/LLM flags, some state-only);
- areas across the 8 state groups' states (2 states per group);
- static rows;
- exposure rows for every `BACKTEST_CUTOFFS` value and a production cutoff, except those dropped.

The true rate is the Task 15 generator's.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `backtest.py`:
1. **`prepare`:** builds cells from `exposure_by_cutoff[fold.exposure_cutoff]`, with `mp_ticks=False` setting `ticks_*` to NaN and `tick_coverage` to NaN, mirroring the removal drill. Profile: `seasonal_profile(placements, start=fold.train_start, end=fold.train_end)`, also used for test counts, so the test never informs the spread. Then train and test `count_incidents` with `exclude_flags`, the masks, and τ as specified.
2. **`run_fold`:** `fit_model(cells, train, params, train_mask=mask)`. For a spatial fold, `predict(cells, zeros)` on test cells, since the test is the same window and held-out cells never saw their counts. For a rolling fold, `predict(cells, train.y)`, since cells keep their past. Then `scale = mu0·τ`, then `summarize` for conf/insufficient. It returns test cells only.
3. **`report`** concatenates the fold results and computes:
   - `mean_log_score(obs, shape, rate, scale)`;
   - `calibration(shape/rate·scale, obs)`;
   - `top_capture`;
   - `pit_coverage`;
   - `oe_ci(obs.sum(), expected.sum())`.

   It does the same per kind, per group, per `conf` level (insufficient cells excluded from the score metrics and counted separately as `insufficient_share`) and for the zero-history stratum.
4. **`run_suite`** runs `main`, `geo_only`, `permuted` and the three sensitivities over `spatial_folds(cutoff) + rolling_folds()`. Then:
   - `prospective` over `prospective_fold()` when `count_incidents` finds > 0 test weight, else `None`;
   - `recent` over a `Fold("recent", "rolling", TRAIN_START, date(2019,1,1), date(2019,1,1), date(2025,1,1), frozenset(), date(2016,1,1))`, using the 2016 snapshot. That is the closest backtest cutoff not after 2019, since there is no 2019 snapshot by design; the exposure is then up to 3 years older than the origin, a conservative choice. It is reported with the O/E CI only, and an issue is opened when the 90% CI excludes [0.5, 2] (P3:377).

`kernel_adapter.py` holds the adapter described above, plus `prospective_kernel(conn, fold, cells) -> NDArray`. That function reads `historical_predictions` for the window, `SELECT route_id, prediction_date, risk_score FROM historical_predictions WHERE prediction_date >= :a AND prediction_date < :b`, parameterised. It maps routes to cells through `scorable_routes`, averages non-NULL scores per (area, g, month), and keeps NULL (insufficient) cells as NaN, which are excluded from the kernel's ranking and reported as kernel coverage.

- [ ] **Step 4: Run.** `cd backend && uv run --group train pytest tests/ml -q` → PASS.
- [ ] **Step 5: Commit** `feat(ml): backtest harness, references, kernel adapter`.

---

### Task 18: Tuning: archetypes, d\*, φ, confidence thresholds, stability

**Files:**
- Create: `backend/app/ml/tuning.py`, `backend/tests/ml/test_tuning.py`
- Modify: `backend/app/ml/model.py` (`fit_model(..., reuse_stage1: Fitted | None = None)` skips the GLM when only stage-2 parameters change)

**Interfaces:**
- Produces:
  - `K_GRID = (8, 16, 32, 64)`, `WEIGHT_GRID = (0.0, 1.0, 2.0)`, `PHI_GRID = (1.0, 1.25, 1.5, 2.0, 3.0, 4.0)`, `RATIO_GRID = (2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 15.0)`;
  - `tune(fold_data: list[FoldData]) -> TuneResult(params: ModelParams, thresholds: Thresholds, report: dict)`, with all selection on held-out folds only (G5);
  - `select_d_star(results_pooled, results_geo, dist) -> float`;
  - `select_conf(results, phi) -> tuple[float, float] | None`;
  - `stability(raw, params, thresholds, *, cutoff, n_boot=20, seed=0) -> dict` (`median_spearman`, `median_band_churn`, `passed`).

Selection rules, all recorded in `thresholds` and the model card:
1. **Archetypes.** `k_arch` = argmax pooled log score over `K_GRID` (weights all 1). Then one coordinate pass over the 7 feature weights in `WEIGHT_GRID`, keeping a change only if the log score improves by > 0.001 per cell `[default pending: DP9]`.
2. **φ** = the `PHI_GRID` value whose pooled `pit_coverage` is closest to 0.90 `[default pending: DP11]`.
3. **d\*** (A:150). Bands are the quantiles (0, 0.25, 0.5, 0.75, 0.9, 1.0) of held-out cells' evidence distances. Walking up the bands, the pooled-minus-geo-only log-score gain is computed per band with ≥ 20 expected incidents. d\* is the upper edge of the last band with gain > 0. If even the first band shows no gain, d\* = 0, so only geo evidence counts and the gate's G1 reports it.
4. **Confidence** `[default pending: DP11]`. Over pairs `t_high < t_medium` from `RATIO_GRID`, keep pairs where:
   - every level with ≥ 20 expected incidents has `pit_coverage` in [0.85, 0.95];
   - mean |log(O/E)| per level is non-increasing from low to high;
   - the Low stratum's `calibration` passes (A:151).

   Choose the pair with the largest High+Medium route share, ties to the smaller `t_high`. None passing gives `None`, and the gate fails G4.
5. **Stability** (P3:276). There are 20 bootstrap resamples of state groups with replacement; incident weights in a group drawn m times are multiplied by m. Each refits with the tuned params, and the stats are the Spearman of route percentiles against the full fit and the share of routes changing 25/50/75 band. It passes when median Spearman ≥ 0.8 and median churn ≤ 0.10.

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np

from app.ml.tuning import select_conf, select_d_star, stability, tune
from app.ml.fixtures import fold_results_toy, synthetic_fold_data, synthetic_raw


def test_d_star_is_the_last_helpful_band():
    pooled, geo, dist = fold_results_toy(gain_by_band=(0.2, 0.1, -0.05, -0.1, -0.2))
    assert select_d_star(pooled, geo, dist) == np.quantile(dist, 0.5)


def test_d_star_zero_when_neighbours_never_help():
    pooled, geo, dist = fold_results_toy(gain_by_band=(-0.1,) * 5)
    assert select_d_star(pooled, geo, dist) == 0.0


def test_conf_selection_respects_coverage_and_monotone_error():
    good = fold_results_toy(calibrated=True)[0]
    assert select_conf(good, phi=1.0) is not None
    assert select_conf(fold_results_toy(calibrated=False)[0], phi=1.0) is None


def test_tune_is_deterministic():
    fds = synthetic_fold_data(seed=31)
    assert tune(fds).thresholds == tune(fds).thresholds


def test_stability_passes_on_stable_synthetic_problem():
    s = stability(synthetic_raw(seed=32), *tune(synthetic_fold_data(seed=32))[:2], cutoff=None, n_boot=5)
    assert s["passed"] and s["median_spearman"] >= 0.8
```

`fold_results_toy` builds `FoldResult` lists with controlled per-band gains and calibration. `synthetic_fold_data` runs `prepare` over `synthetic_raw`'s spatial and rolling folds.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `backend/app/ml/tuning.py`:

```python
"""CV selection of everything a version freezes (P3 M6 as amended by A:151; G5): archetype K and
weights, phi, d*, confidence thresholds, plus bootstrap stability. Only held-out fold results are
read, so nothing is tuned on data it later scores."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from itertools import combinations
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray

from app.ml.backtest import FoldData, FoldResult, RawData, fit_full, reweight, run_fold
from app.ml.cells import STATE_GROUPS
from app.ml.metrics import calibration, mean_log_score, nb_logpmf, pit_coverage, spearman
from app.ml.model import Fitted, ModelParams
from app.ml.posterior import Thresholds, gamma_interval
from app.services.risk_bands import RISK_BAND_THRESHOLDS

K_GRID = (8, 16, 32, 64)
WEIGHT_GRID = (0.0, 1.0, 2.0)
PHI_GRID = (1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
RATIO_GRID = (2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 15.0)
BAND_QUANTILES = (0.0, 0.25, 0.5, 0.75, 0.9, 1.0)
MIN_BAND_EXPECTED = 20.0
MIN_LEVEL_EXPECTED = 20.0
MIN_LOW_CALIBRATION_EXPECTED = 50.0
WEIGHT_GAIN_MIN = 0.001  # mean log score is per cell, so this is a per-cell gain
NEUTRAL = Thresholds(d_star=float("inf"), conf_high=float("inf"), conf_medium=float("inf"), phi=1.0)


class TuneResult(NamedTuple):
    params: ModelParams
    thresholds: Thresholds
    report: dict[str, Any]


@dataclass(frozen=True)
class _Pooled:
    obs: NDArray[np.float64]
    shape: NDArray[np.float64]
    rate: NDArray[np.float64]
    scale: NDArray[np.float64]
    routes: NDArray[np.float64]
    evidence_dist: NDArray[np.float64]


def _cat(results: list[FoldResult]) -> _Pooled:
    return _Pooled(*(np.concatenate([getattr(r, f) for r in results])
                     for f in ("obs", "shape", "rate", "scale", "routes", "evidence_dist")))


def select_phi(results: list[FoldResult]) -> float:
    R = _cat(results)
    return min(PHI_GRID, key=lambda f: (abs(pit_coverage(R.obs, R.shape / f, R.rate / f, R.scale) - 0.90), f))


def select_d_star(pooled: list[FoldResult], geo: list[FoldResult], dist: NDArray[np.float64]) -> float:
    P, G = _cat(pooled), _cat(geo)
    gain = nb_logpmf(P.obs, P.shape, P.rate, P.scale) - nb_logpmf(G.obs, G.shape, G.rate, G.scale)
    expected = P.shape / P.rate * P.scale
    finite = np.isfinite(dist)
    if not finite.any():
        return 0.0
    edges = np.quantile(dist[finite], BAND_QUANTILES)
    d_star, lo = 0.0, -np.inf
    for hi in edges[1:]:
        band = (dist > lo) & (dist <= hi)
        lo = hi
        if expected[band].sum() < MIN_BAND_EXPECTED:
            continue
        if gain[band].mean() <= 0.0:
            break  # neighbours this far stop helping; farther bands are not considered
        d_star = float(hi)
    return d_star


def select_conf(results: list[FoldResult], phi: float) -> tuple[float, float] | None:
    R = _cat(results)
    lo, mean, hi = gamma_interval(R.shape, R.rate, phi)
    ratio, expected = hi / lo, mean * R.scale
    best: tuple[float, float, float] | None = None
    for th, tm in combinations(RATIO_GRID, 2):
        level = np.where(ratio <= th, 0, np.where(ratio <= tm, 1, 2))
        ok, errs = True, []
        for lv in (0, 1, 2):
            sel = level == lv
            if expected[sel].sum() < MIN_LEVEL_EXPECTED:
                continue
            cov = pit_coverage(R.obs[sel], R.shape[sel] / phi, R.rate[sel] / phi, R.scale[sel])
            ok &= 0.85 <= cov <= 0.95
            errs.append(abs(float(np.log(max(R.obs[sel].sum(), 0.5) / expected[sel].sum()))))
        ok &= all(a <= b for a, b in zip(errs, errs[1:]))  # error never falls as confidence drops
        low = level == 2
        if expected[low].sum() >= MIN_LOW_CALIBRATION_EXPECTED:
            ok &= calibration(expected[low], R.obs[low]).passed
        if ok:
            share = float(R.routes[level < 2].sum() / R.routes.sum())
            if best is None or (share, -th) > (best[0], -best[1]):
                best = (share, th, tm)
    return None if best is None else (best[1], best[2])


def _score(fold_data: list[FoldData], params: ModelParams, cache: dict[str, Fitted]) -> tuple[float, list[FoldResult]]:
    res = [run_fold(fd, params, NEUTRAL, stage1_cache=cache) for fd in fold_data]
    R = _cat(res)
    return mean_log_score(R.obs, R.shape, R.rate, R.scale), res


def tune(fold_data: list[FoldData]) -> TuneResult:
    cache: dict[str, Fitted] = {}
    base = ModelParams()
    scored = {k: _score(fold_data, replace(base, k_arch=k), cache) for k in K_GRID}
    k_best = max(K_GRID, key=lambda k: (scored[k][0], -k))
    params = replace(base, k_arch=k_best)
    best, res = scored[k_best]
    weights = list(params.arch_weights)
    for j in range(len(weights)):
        for w in WEIGHT_GRID:
            trial = weights.copy()
            trial[j] = w
            if w == weights[j] or sum(trial) == 0:
                continue
            sc, r = _score(fold_data, replace(params, arch_weights=tuple(trial)), cache)
            if sc > best + WEIGHT_GAIN_MIN:
                best, res, weights = sc, r, trial
    params = replace(params, arch_weights=tuple(weights))
    phi = select_phi(res)
    geo = [run_fold(fd, replace(params, blend=False), NEUTRAL, stage1_cache=cache) for fd in fold_data]
    d_star = select_d_star(res, geo, _cat(res).evidence_dist)
    conf = select_conf(res, phi)
    th = Thresholds(d_star, conf[0] if conf else float("inf"), conf[1] if conf else float("inf"), phi)
    return TuneResult(params, th, {"k_arch": k_best, "arch_weights": weights, "phi": phi, "d_star": d_star,
                                   "conf_selected": conf is not None, "cv_log_score": best})


def stability(raw: RawData, params: ModelParams, thresholds: Thresholds, *, cutoff: date | None,
              n_boot: int = 20, seed: int = 0) -> dict[str, Any]:
    fit, cells, counts = fit_full(raw, params, cutoff=cutoff)
    base = fit.summarize(cells, counts.y, thresholds)
    groups = list(STATE_GROUPS)
    rng = np.random.default_rng(seed)
    rhos, churn = [], []
    for _ in range(n_boot):
        draw = rng.choice(len(groups), size=len(groups), replace=True)
        mult = {s: float((draw == i).sum()) for i, g in enumerate(groups) for s in STATE_GROUPS[g]}
        f, c, k = fit_full(reweight(raw, mult), params, cutoff=cutoff)
        s = f.summarize(c, k.y, thresholds)
        ok = ~base.insufficient & ~s.insufficient
        rho = spearman(base.percentile[ok], s.percentile[ok])
        rhos.append(-1.0 if rho is None else rho)  # an undefined correlation counts as unstable, never as 0
        band = lambda p: np.digitize(p, RISK_BAND_THRESHOLDS)  # noqa: E731  lower-inclusive, as color_code_for
        churn.append(float(np.mean(band(base.percentile[ok]) != band(s.percentile[ok]))))
    med_rho, med_churn = float(np.median(rhos)), float(np.median(churn))
    return {"median_spearman": med_rho, "median_band_churn": med_churn, "n_boot": n_boot,
            "passed": med_rho >= 0.8 and med_churn <= 0.10}
```

With `conf` None, the confidence thresholds are `inf` (every sufficient cell reads `high`). `conf_selected = False` fails gate G4, so such a version is never promoted, and its stored rows are pruned with it.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): CV tuning of archetypes, d*, phi, confidence; stability`.


---

### Task 19: Gate, diff report, promotion issue (aggregate-only)

**Files:**
- Create: `backend/app/ml/promote.py`, `backend/tests/ml/test_promote.py`

**Interfaces:**
- Produces:
  - `GateResult(passed: bool, components: dict[str, dict])`, where each component is `{"passed": bool, "marginal": bool, "value": …, "threshold": …}`;
  - `evaluate_gate(challenger: dict, active: dict | None, *, active_family: str) -> GateResult`;
  - `DiffReport(band_shift: float, coverage_shift_points: float, new_family: bool, large_shift: bool, matrix: dict, per_group: dict, per_state: dict, metric_deltas: dict, movers: list[dict])`;
  - `diff_report(challenger_rows, active_rows, *, reference_month, new_family) -> DiffReport`. Rows are `(route_id, type_group, state, percentile|None)`, so `movers` stays in the DB;
  - `issue_markdown(version, gate, diff, *, public=True) -> str`. With `public=True` it **never** includes `movers`, route ids, coordinates or names `[default pending: DP16]`;
  - `rollback_guard(actor: str, owner: str, labels: list[str]) -> str | None` (a refusal reason or None).

Gate components (P3 M9 plus A:154–173). All must pass:

| Key | Pass rule |
|---|---|
| `spatial_log_score` | challenger − active > 0 (marginal if a bootstrap-over-folds 90% CI includes 0) |
| `rolling_log_score` | same |
| `spatial_top10`, `rolling_top10` | challenger ≥ active |
| `calibration` | `main.calibration.passed` |
| `g1_pooling` | main log score ≥ geo-only and main `calibration.n_pass` ≥ geo-only's |
| `g2_zero_history` | \|O/E − 1\| main ≤ geo-only on the zero-history stratum |
| `g3_negative_control` | permuted gain over geo-only < 50% of main's gain |
| `g4_confidence` | tuned thresholds exist (`select_conf` not None) |
| `stability` | `stability.passed` |
| `capacity` | wall < 5.5 h, peak RSS < 16 GB (hard limits, P3:216) |
| `prospective_top10` | only when the prospective part exists: challenger ≥ kernel (DP14) |

With `active_family == "kernel"`, the comparisons use the stored kernel metrics, and components the kernel lacks are evaluated on the challenger alone. Marginal flags never fail the gate (M9: "reported, not held").

- [ ] **Step 1: Write the failing tests.**

```python
from app.ml.promote import diff_report, evaluate_gate, issue_markdown, rollback_guard
from app.ml.fixtures import challenger_metrics, rows_for_diff


def test_gate_fails_on_any_component_and_passes_when_all_hold():
    good, weaker = challenger_metrics(), challenger_metrics(worse=True)
    assert evaluate_gate(good, weaker, active_family="eb_v1").passed
    for broken in ("calibration", "g1_pooling", "g2_zero_history", "g3_negative_control", "g4_confidence",
                   "stability", "capacity"):
        result = evaluate_gate(challenger_metrics(break_=broken), weaker, active_family="eb_v1")
        assert not result.passed and not result.components[broken]["passed"]
    assert not evaluate_gate(good, None, active_family="kernel").passed  # nothing to beat: fail closed


def test_large_shift_banner_rules():
    ch, ac = rows_for_diff(changed_share=0.06)
    d = diff_report(ch, ac, reference_month=10, new_family=False)
    assert d.large_shift and "LARGE SHIFT" in issue_markdown("v", evaluate_gate(challenger_metrics(), None,
                                                                                active_family="kernel"), d)
    ch, ac = rows_for_diff(changed_share=0.02, coverage_shift=0.11)
    assert diff_report(ch, ac, reference_month=10, new_family=False).large_shift
    ch, ac = rows_for_diff(changed_share=0.0)
    assert diff_report(ch, ac, reference_month=10, new_family=True).large_shift


def test_public_issue_never_carries_route_ids():
    ch, ac = rows_for_diff(changed_share=0.2)
    d = diff_report(ch, ac, reference_month=10, new_family=False)
    md = issue_markdown("v", evaluate_gate(challenger_metrics(), None, active_family="kernel"), d)
    assert d.movers and not any(str(m["route_id"]) in md for m in d.movers)
    assert "9000" not in md


def test_rollback_refuses_kernel_and_non_owner():
    assert rollback_guard("mallory", "owner", ["model-promotion"]) == "only the repository owner may roll back"
    assert rollback_guard("owner", "owner", ["bug"]) == "not a model-promotion issue"
    assert rollback_guard("owner", "owner", ["model-promotion"]) is None
```

The "previous is the kernel" refusal is the registry's (`RollbackRefused`, Task 2); the workflow surfaces its message.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** The metrics dict `train.py` writes to `model.registry.metrics`, and that the gate reads, has these keys:
- `main`, `geo_only` and `permuted`: Task 17 reports;
- `sensitivity: {no_ski, no_llm, no_mp_ticks}`;
- `prospective`, `prospective_kernel` (or null) and `recent`;
- `thresholds`: the tuning report;
- `stability` and `capacity: {wall_hours, peak_rss_gb, bundle_mb, curve}`;
- `vs_active: {spatial: {log_score_ci}, rolling: {log_score_ci}}`: a fold-bootstrap 90% CI of the challenger-minus-active log score.

A missing key fails its component. `backend/app/ml/promote.py`:

```python
"""Validation gate and promotion report (P3 M9, A:154-173). Fail closed: a missing metric fails its
component. The public issue is aggregate-only (DP16); per-route movers stay in
model.registry.diff_report."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.services.risk_bands import color_code_for

BAND_SHIFT_ALERT = 0.05
COVERAGE_SHIFT_ALERT = 10.0
WALL_LIMIT_HOURS = 5.5
RSS_LIMIT_GB = 16.0
Row = tuple[int, str, str, float | None]  # route_id, type_group, state_code, percentile (None = insufficient)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    components: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class DiffReport:
    band_shift: float
    coverage_shift_points: float
    new_family: bool
    large_shift: bool
    matrix: dict[str, int]
    per_group: dict[str, dict[str, int]]
    per_state: dict[str, dict[str, float]]
    metric_deltas: dict[str, Any] = field(default_factory=dict)
    movers: list[dict[str, Any]] = field(default_factory=list)


def _get(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _comp(value: Any, threshold: Any, ok: bool, marginal: bool = False) -> dict[str, Any]:
    return {"passed": bool(ok), "marginal": bool(marginal), "value": value, "threshold": threshold}


def evaluate_gate(ch: dict[str, Any], active: dict[str, Any] | None, *, active_family: str) -> GateResult:
    c: dict[str, dict[str, Any]] = {}
    for kind in ("spatial", "rolling"):
        mine, theirs = _get(ch, "main", "by_kind", kind, "log_score"), _get(active, "main", "by_kind", kind, "log_score")
        ci = _get(ch, "vs_active", kind, "log_score_ci")
        c[f"{kind}_log_score"] = _comp(mine, theirs, mine is not None and theirs is not None and mine > theirs,
                                       marginal=bool(ci) and ci[0] <= 0.0)
        mt, tt = _get(ch, "main", "by_kind", kind, "top10"), _get(active, "main", "by_kind", kind, "top10")
        c[f"{kind}_top10"] = _comp(mt, tt, mt is not None and tt is not None and mt >= tt)
    cal = _get(ch, "main", "calibration") or {}
    need = 8 if cal.get("scheme") == "decile" else 4
    c["calibration"] = _comp(cal.get("n_pass"), need, cal.get("passed") is True, marginal=cal.get("n_pass") == need)
    m_ls, g_ls, p_ls = _get(ch, "main", "log_score"), _get(ch, "geo_only", "log_score"), _get(ch, "permuted", "log_score")
    m_np, g_np = _get(ch, "main", "calibration", "n_pass"), _get(ch, "geo_only", "calibration", "n_pass")
    c["g1_pooling"] = _comp(m_ls, g_ls, None not in (m_ls, g_ls, m_np, g_np) and m_ls >= g_ls and m_np >= g_np)
    zm, zg = _get(ch, "main", "zero_history", "oe"), _get(ch, "geo_only", "zero_history", "oe")
    c["g2_zero_history"] = _comp(zm, zg, zm is not None and zg is not None and abs(zm[1] - 1) <= abs(zg[1] - 1))
    c["g3_negative_control"] = _comp(p_ls, g_ls, None not in (m_ls, g_ls, p_ls) and m_ls > g_ls
                                     and (p_ls - g_ls) < 0.5 * (m_ls - g_ls))
    c["g4_confidence"] = _comp(_get(ch, "thresholds"), "selected", _get(ch, "thresholds", "conf_selected") is True)
    st = _get(ch, "stability") or {}
    c["stability"] = _comp(st, {"median_spearman": 0.8, "median_band_churn": 0.10}, st.get("passed") is True)
    cap = _get(ch, "capacity") or {}
    c["capacity"] = _comp(cap, {"wall_hours": WALL_LIMIT_HOURS, "peak_rss_gb": RSS_LIMIT_GB},
                          cap.get("wall_hours", 1e9) < WALL_LIMIT_HOURS and cap.get("peak_rss_gb", 1e9) < RSS_LIMIT_GB)
    pm, pk = _get(ch, "prospective", "top10"), _get(ch, "prospective_kernel", "top10")
    if pm is not None:  # DP14: gated only once the refresh has test incidents in the window
        c["prospective_top10"] = _comp(pm, pk, pk is not None and pm >= pk)
    return GateResult(all(v["passed"] for v in c.values()), c)


def _band(p: float | None) -> str:
    return "insufficient" if p is None else color_code_for(round(p, 1))


def diff_report(challenger_rows: Sequence[Row], active_rows: Sequence[Row], *, reference_month: int,
                new_family: bool) -> DiffReport:
    old = {r[0]: r for r in active_rows}
    matrix: Counter[str] = Counter()
    per_group: dict[str, Counter[str]] = defaultdict(Counter)
    deltas: dict[str, list[float]] = defaultdict(list)
    movers: list[dict[str, Any]] = []
    changed = both = 0
    for rid, g, st, p in challenger_rows:
        if rid not in old:
            continue
        q = old[rid][3]
        key = f"{_band(q)}->{_band(p)}"
        matrix[key] += 1
        per_group[g][key] += 1
        both += 1
        changed += _band(q) != _band(p)
        if p is not None and q is not None:
            deltas[st].append(p - q)
            movers.append({"route_id": rid, "type_group": g, "old": q, "new": p, "month": reference_month})
    ins = lambda rows: (sum(r[3] is None for r in rows) / len(rows)) if rows else None  # noqa: E731
    new_cov, old_cov = ins(challenger_rows), ins(active_rows)
    cov_shift = 0.0 if new_cov is None or old_cov is None else 100.0 * (new_cov - old_cov)
    band_shift = changed / both if both else 0.0
    return DiffReport(
        band_shift=band_shift, coverage_shift_points=cov_shift, new_family=new_family,
        large_shift=band_shift > BAND_SHIFT_ALERT or abs(cov_shift) > COVERAGE_SHIFT_ALERT or new_family,
        matrix=dict(matrix), per_group={g: dict(v) for g, v in per_group.items()},
        per_state={s: {"routes": float(len(d)), "mean_delta": float(np.mean(d))} for s, d in sorted(deltas.items())},
        movers=sorted(movers, key=lambda m: -abs(m["new"] - m["old"]))[:25],
    )


def issue_markdown(version: str, gate: GateResult, diff: DiffReport, *, public: bool = True) -> str:
    out: list[str] = []
    if diff.large_shift:
        why = [w for w, on in ((f"band shift {100 * diff.band_shift:.1f}%", diff.band_shift > BAND_SHIFT_ALERT),
                               (f"coverage shift {diff.coverage_shift_points:+.1f} points",
                                abs(diff.coverage_shift_points) > COVERAGE_SHIFT_ALERT),
                               ("first version of a new model family", diff.new_family)) if on]
        out.append(f"**⚠ LARGE SHIFT** ({'; '.join(why)}): see the band matrix and the per-state table.")
    out += [f"## Model {version}: {'promoted' if gate.passed else 'rejected'}", "",
            "### Band from → to (all routes)", "| from → to | routes |", "|---|---|",
            *(f"| {k} | {v} |" for k, v in sorted(diff.matrix.items())), "",
            "### Per type group", "| group | from → to | routes |", "|---|---|---|",
            *(f"| {g} | {k} | {v} |" for g, m in sorted(diff.per_group.items()) for k, v in sorted(m.items())), "",
            "### Per state", "| state | routes compared | mean percentile change |", "|---|---|---|",
            *(f"| {s} | {int(v['routes'])} | {v['mean_delta']:+.1f} |" for s, v in diff.per_state.items()), "",
            "### Gate", "| component | passed | marginal | value | threshold |", "|---|---|---|---|---|",
            *(f"| {k} | {v['passed']} | {v['marginal']} | {json.dumps(v['value'], default=str)[:80]} | "
              f"{json.dumps(v['threshold'], default=str)[:80]} |" for k, v in gate.components.items()),
            "", "Comment `/rollback` (owner only) to restore the previous version."]
    if not public:
        out += ["", "### Biggest movers", *(f"- {m['route_id']} {m['type_group']}: {m['old']:.1f} → {m['new']:.1f}"
                                            for m in diff.movers)]
    return "\n".join(out)


def rollback_guard(actor: str, owner: str, labels: list[str]) -> str | None:
    if actor != owner:
        return "only the repository owner may roll back"
    if "model-promotion" not in labels:
        return "not a model-promotion issue"
    return None
```

The issue body's order follows P3:315–321 minus the PNG map `[default pending: DP16]`; the per-state table stands in for it. `metric_deltas` is filled by `train.py` with `dataclasses.replace` (challenger minus active for every numeric gate metric).

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ml): gate, diff report, aggregate-only promotion issue`.


---

### Task 20: Materialize scores, train CLI, workflows, CI fixture backtest

**Files:**
- Create: `backend/app/ml/materialize.py`, `backend/app/ml/train.py`, `backend/app/ml/fixtures.py` (consolidate the synthetic builders from Tasks 8–19), `.github/workflows/model-train.yml`, `.github/workflows/model-rollback.yml`, `backend/Dockerfile.train`, `backend/tests/ml/test_materialize.py`, `backend/tests/ml/test_train_cli.py`, `backend/tests/test_workflows_model.py`
- Modify: `.github/workflows/ci.yml` (fixture backtest step; `uv sync --frozen --group pipelines --group train`), `backend/app/pipelines/ingest_log.py` (unchanged; training logs to the registry, not the ingest log), `CLAUDE.md`

**Interfaces:**
- `materialize.write_scores(conn, version, cells, summary, routes_by_cell) -> int`. It inserts one row per scorable route × month with `INSERT INTO model.route_scores (...) SELECT * FROM unnest(CAST(:route_id AS bigint[]), CAST(:month AS smallint[]), …)` in batches of 20,000 routes. `NaN` becomes `NULL` explicitly (`None` in the arrays), never 0. `routes_by_cell` comes from `SELECT route_id, area_id, type_group FROM scorable_routes WHERE type_group <> 'unknown'`.
- `train.py` CLI (`uv run --group train python -m app.ml.train …`), as `trainer`:
  - `run [--today YYYY-MM-DD] [--benchmark]`. It loads raw (production exposure snapshot plus all `BACKTEST_CUTOFFS`), tunes, runs the suite, fits the final model on all data (cutoff = today), builds the bundle and summaries, and runs stability and capacity (`resource.getrusage`, wall clock). Then:
    1. `insert_candidate`;
    2. `write_scores`;
    3. reads the active version's rows for `diff_report`;
    4. `evaluate_gate`;
    5. `set_gate`;
    6. `promote` or `reject`;
    7. `prune`.

    It writes `out/issue.md` (public, aggregate-only), `out/status.json` (`{"version","promoted","large_shift","rejected_reason"}`) and the model card into `model_card`. It pings `HEALTHCHECKS_TRAIN_URL` on success and `/fail` on error. `--benchmark` additionally fits 1×/2×/4× synthetic cell counts and stores the power-law curve in `metrics.capacity` (P3:223).
  - `kernel-baseline [--today]`: computes `kernel_report` over the same folds and inserts the `kernel` row as `active` only when the registry is empty (MVP-0 accept, P3:505). It refuses otherwise.
  - `rollback [--version V]`: with no version, `registry.rollback`. With a version, it promotes that version with `by="owner"`, and only if it has a bundle and scores. The registry refuses the kernel.
- `app.ml.backtest` CLI `--fixture small`: runs `run_suite` on `fixtures.synthetic_raw(seed=0)` and checks the floors: main log score > geo-only, `calibration.passed`, coverage in [0.80, 0.97], and permuted < main. It exits 1 on any failure. No DB, no network.

- [ ] **Step 1: Write the failing tests.**
  - `test_materialize.py` (`requires_pg`, 0012 head with the Task 1 SEED and a scored `fitted_toy`):
    - rows land with NULLs exactly where `summary` is NaN;
    - an `insufficient` row has `conf_level = 'insufficient'`;
    - a second `write_scores` for the same version raises (PK);
    - nothing is written for an `unknown`-type route.
  - `test_train_cli.py`: `run` against a migrated DB seeded through `fixtures.seed_training_db(name)` (synthetic catalog, accidents, exposure snapshots, static features) with `TRAINER_DATABASE_URL` pointed at it:
    - the first run inserts a candidate and, against a seeded kernel row, promotes it;
    - `out/issue.md` exists and carries no `9000`-prefixed id;
    - a second run with corrupted data (all incidents moved to one area) is `rejected` and `promoted` is false in `status.json`;
    - `rollback` with no `previous` exits non-zero with the registry's message.
  - `test_workflows_model.py` parses both YAML files and asserts:
    - `model-train.yml` has `schedule` + `workflow_dispatch`, `timeout-minutes: 330`, `permissions: {contents: read, issues: write}`, no `pull_request` trigger, and `TRAINER_DATABASE_URL` only from `secrets`;
    - `model-rollback.yml` triggers on `issue_comment` and `workflow_dispatch`, its job `if:` checks `github.event.comment.user.login == github.repository_owner`, `contains(github.event.issue.labels.*.name, 'model-promotion')` and `github.event.issue.pull_request == null`, and no `run:` step interpolates `github.event.comment.body` (it is passed through `env:` only);
    - both pin actions by SHA, as `ci.yml` does.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `model-train.yml`:

```yaml
name: model-train
on:
  schedule:
    - cron: "0 7 3 * *"        # monthly baseline retrain (P3:420)
  workflow_dispatch:
    inputs:
      benchmark: { type: boolean, default: false }
      kernel_baseline: { type: boolean, default: false }
permissions:
  contents: read
  issues: write
concurrency: { group: model-train, cancel-in-progress: false }
jobs:
  train:
    runs-on: ubuntu-latest
    timeout-minutes: 330
    defaults: { run: { working-directory: backend } }
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with: { version: "0.11.3", python-version: "3.12" }
      - run: uv sync --frozen --no-group pipelines --group train
      - name: Kernel baseline (once, MVP-0)
        if: ${{ inputs.kernel_baseline }}
        env: { TRAINER_DATABASE_URL: "${{ secrets.TRAINER_DATABASE_URL }}" }
        run: uv run python -m app.ml.train kernel-baseline
      - name: Train, gate, promote
        if: ${{ !inputs.kernel_baseline }}
        env:
          TRAINER_DATABASE_URL: ${{ secrets.TRAINER_DATABASE_URL }}
          HEALTHCHECKS_TRAIN_URL: ${{ secrets.HEALTHCHECKS_TRAIN_URL }}
          BENCHMARK: ${{ inputs.benchmark && '--benchmark' || '' }}
        run: uv run python -m app.ml.train run $BENCHMARK
      - name: Open the promotion or rejection issue
        if: ${{ !inputs.kernel_baseline && always() && hashFiles('backend/out/status.json') != '' }}
        env: { GH_TOKEN: "${{ github.token }}", OWNER: "${{ github.repository_owner }}" }
        run: |
          label=$(python3 -c 'import json;s=json.load(open("out/status.json"));print("model-promotion" if s["promoted"] else "model-rejected")')
          { echo "@$OWNER"; echo; cat out/issue.md; } > out/body.md
          gh issue create --title "Model $(python3 -c 'import json;print(json.load(open("out/status.json"))["version"])')" \
            --label "$label" --body-file out/body.md --assignee "$OWNER"
```

`model-rollback.yml`:

```yaml
name: model-rollback
on:
  issue_comment: { types: [created] }
  workflow_dispatch:
    inputs:
      version: { type: string, required: false }
permissions:
  contents: read
  issues: write
jobs:
  rollback:
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (github.event.issue.pull_request == null &&
       contains(github.event.issue.labels.*.name, 'model-promotion') &&
       github.event.comment.user.login == github.repository_owner &&
       startsWith(github.event.comment.body, '/rollback'))
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: backend } }
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0  (default branch: never PR code)
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with: { version: "0.11.3", python-version: "3.12" }
      - run: uv sync --frozen --no-group pipelines --group train
      - name: Roll back
        id: rb
        env:
          TRAINER_DATABASE_URL: ${{ secrets.TRAINER_DATABASE_URL }}
          VERSION: ${{ inputs.version }}
        run: |
          set +e
          uv run python -m app.ml.train rollback ${VERSION:+--version "$VERSION"} > out.txt 2>&1
          echo "rc=$?" >> "$GITHUB_OUTPUT"
      - name: Report
        if: ${{ github.event_name == 'issue_comment' }}
        env: { GH_TOKEN: "${{ github.token }}", ISSUE: "${{ github.event.issue.number }}" }
        run: gh issue comment "$ISSUE" --body-file out.txt
      - if: ${{ steps.rb.outputs.rc != '0' }}
        run: exit 1
```

`backend/Dockerfile.train` is the Railway one-off fallback (P3:226). It copies the `Dockerfile.jobs` pattern, syncs `--group train --no-group pipelines`, and has `CMD ["uv", "run", "python", "-m", "app.ml.train", "run"]`; nothing deploys it until the fallback trigger fires. Add it to `scripts/check_compose_matches_railway.py`'s exemptions only if a `railway-train.toml` is ever added, which this plan does not do.

`ci.yml` backend job:
- after `Test`: `- name: Fixture backtest` / `run: uv run python -m app.ml.backtest --fixture small`;
- the install line gains `--group train`;
- the dependency audit's export gains `--group train`.

`CLAUDE.md` Commands:
- `uv run --group train python -m app.ml.train run|kernel-baseline|rollback [--version V]` (as `trainer` via `TRAINER_DATABASE_URL`; GitHub Actions `model-train.yml`/`model-rollback.yml`);
- `uv run --group train python -m app.ml.backtest --fixture small`.

- [ ] **Step 4: Run.** `cd backend && uv run --group train pytest tests/ml tests/test_workflows_model.py -q && uv run python -m app.ml.backtest --fixture small && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** `feat(ml): score materialization, train CLI, training and rollback workflows, CI fixture backtest`.

---

### Task 21: OWNER/AGENT RUNBOOK: backtest snapshots, kernel row, first candidate (branch, then prod)

No code. Prerequisite: PR 3-3 merged; prod at `0012`; trainer secret set.

- [ ] **Step 1 (agent, Mac, as `ingest`): backtest exposure snapshots.** Follow the exposure plan's Task 10 Step 5 gated block once per cutoff in `BACKTEST_CUTOFFS` (2004-01-01, 2008-01-01, 2012-01-01, 2016-01-01, 2026-02-05). Each is `INGMOD app.pipelines.exposure build --cutoff <d>` inside that block's `gate_pass`/`gate_require` structure, with the branch rehearsal first. Then `save_once ../data/review/p3-3/snapshots.txt psql "$ANALYST_URL" -c "SELECT data_cutoff, as_of, count(*) FROM internal.exposure_index GROUP BY 1, 2 ORDER BY 1"`. Expect one row per cutoff plus the production snapshot.

- [ ] **Step 2 (agent, branch rehearsal):**
  1. Create the Neon branch `p3-3-rehearsal` from `production` (`TARGET_HOST` set).
  2. `export TRAINER_DATABASE_URL=<trainer URL for the branch host>`.
  3. Run, in order:
     - `save_once ../data/review/p3-3/kernel.txt uv run --group train python -m app.ml.train kernel-baseline`;
     - `save_once ../data/review/p3-3/run1.json sh -c 'uv run --group train python -m app.ml.train run --benchmark && cat out/status.json'`.
  4. Check:
     - the registry holds `kernel` active→retired, plus `eb-…` active (if the gate passed) or rejected;
     - `out/issue.md` carries no `9000`/real-id pattern (`grep -E '[0-9]{9}' out/issue.md` prints nothing);
     - capacity is within budget (`metrics.capacity`);
     - `scores_rows` = 12 × scorable non-unknown routes.
  5. `gate_pass p3_3_branch` only when all hold.

  If the gate rejects on the branch, STOP. The owner reads the rejection issue text (`out/issue.md`), and nothing goes to prod.

- [ ] **Step 3 (owner, then agent, prod):**
  1. STOP for the owner. The owner snapshots `pre-p3-3` from `production`, and creates the labels the workflows use: `gh label create model-promotion`, `gh label create model-rejected`, `gh label create model-capacity` (mutating `gh` calls are the owner's).
  2. Agent: `gate_require p3_3_branch`.
  3. The owner runs `workflow_dispatch` `model-train` with `kernel_baseline: true`, then with `benchmark: true`.
  4. Agent saves the registry state: `save_once ../data/review/p3-3/prod-registry.txt psql "$ANALYST_URL" -c "SELECT version, family, status, scores_rows FROM model.registry ORDER BY created_at"`.
  5. The promotion issue is opened by the workflow.
  6. The API still serves the kernel until PR 3-4, so a promotion here changes nothing user-visible.

- [ ] **Step 4 (owner): `/rollback` drill in staging (P3:520).** On the branch, run a second candidate (any passing retrain), then comment `/rollback` on its issue in a test repo fork (or `workflow_dispatch` with no version against the branch secret). Confirm active/previous flip back and the comment posts. Record the run URL in `../data/review/p3-3/rollback-drill.txt`.

---

# PR 3-4 — `feat/p3-mvp1-serving`

### Task 22: Stored-score reads and the additive API contract

**Files:**
- Create: `backend/app/services/model_scores.py`, `backend/tests/test_model_scores_api.py`
- Modify: `backend/app/schemas/mp_route.py`, `backend/app/api/v1/mp_routes.py` (`/safety`, `/map-with-safety`, `/risk-breakdown`, `/forecast`, `/time-of-day`, `/historical-trends`), `backend/app/api/v1/predict.py`, `backend/app/utils/cache.py`

**Interfaces:**
- `model_scores`:
  - `StoredScore(route_id, month, model_version, type_group, insufficient, insufficient_reason, rr_mean, rr_q05, rr_q95, percentile, pct_q05, pct_q95, conf_level, n_eff_geo, n_eff_feature, per10k_mean, per10k_q05, per10k_q95, per10k_reason)`;
  - `active_version(db) -> str | None`;
  - `get_score(db, route_id, month) -> StoredScore | None`;
  - `get_scores(db, route_ids: Sequence[int], month) -> dict[int, StoredScore]` (`= ANY(CAST(:ids AS bigint[]))`);
  - `to_risk(s: StoredScore) -> tuple[float | None, SafetyColorCode, DataStatus]`. Insufficient gives `(None, "gray", "insufficient_data")`. Otherwise `(round(percentile, 1), color_code_for(round(percentile, 1)), "ok")`, with no 0.05 floor: the kernel-only `MIN_ESTIMABLE_SCORE` rule retires (A:144).
  - Redis keys are `safety:{model_version}:{route_id}:{month}` (A:138), so a promotion or rollback misses the cache by construction `[default pending: DP22]`.
- Schemas (additive; existing fields unchanged). `MpRouteSafetyResponse` and `SafetyScore` gain:
  - `confidence: Literal["high","medium","low"] | None`;
  - `score_range: tuple[float, float] | None`;
  - `model_version: str | None`;
  - `evidence: {"geo": float, "similar": float} | None`;
  - `insufficient_reason: Literal["no_evidence"] | None`;
  - `relative_rate: {"mean","lo","hi"} | None` (RR × national for the type and month, the Ascents headline, DP13);
  - `per_10k_logged_ascents: {"mean","lo","hi"} | None` and `per_10k_reason: str | None`.

  A validator enforces:
  - `confidence`, `score_range` and `evidence` are set exactly when `data_status == "ok"`;
  - `score_range[0] <= risk_score <= score_range[1]`;
  - insufficient carries `insufficient_reason`.

Endpoint decisions:

| Endpoint | MVP-1 behaviour |
|---|---|
| `POST /mp-routes/{id}/safety` | Stored row for `target_date.month`. `bypass_cache` is accepted and ignored (A:137). No row or no active version → 404 `{"detail": "score unavailable"}`. |
| `GET /mp-routes/map-with-safety` | Bulk stored rows; `safety: null` for a missing row; `meta.computed_routes = 0` always, plus `meta.model_version`. |
| `GET /mp-routes/{id}/risk-breakdown` | `explain()` on the stored row with `scoring_unit_features` for the route and month; returns `terms` (top 3 plus evidence), `evidence`, `model_version`. Nearby incidents stay in `/accidents`, labelled "context, not cause" (P3:450). |
| `GET /mp-routes/{id}/forecast` | Each day carries the stored score of its month (CM = 1 in MVP-1) and the forecast weather as today. No kernel call. |
| `GET /mp-routes/{id}/time-of-day` | Weather by hour unchanged; `risk` per hour is `null` with `risk_note: "Hour-by-hour risk arrives with the conditions rules (v2.1)."` `[default pending: DP24]`. Never a kernel number. |
| `GET /mp-routes/{id}/historical-trends` | Returns the active version's 12-month profile (percentile, range, confidence) instead of kernel history `[default pending: DP24]`. |
| `POST /predict` | 410 Gone, `{"detail": "Arbitrary-point prediction was retired with the kernel model; select a route on the map."}` `[default pending: DP23]`. |

- [ ] **Step 1: Write the failing tests.** `backend/tests/test_model_scores_api.py` uses the existing API test client fixtures with a DB seeded at head: one active `eb_v1` version and rows for 3 routes (ok/high, ok/low, insufficient).

```python
def test_safety_reads_the_stored_row_with_range_and_confidence(client, seeded_scores):
    r = client.post("/api/v1/mp-routes/900000001/safety", params={"target_date": "2026-07-04", "bypass_cache": True})
    body = r.json()
    assert r.status_code == 200 and body["risk_score"] == 40.0 and body["color_code"] == "yellow"
    assert body["confidence"] == "high" and body["score_range"] == [20.0, 60.0]
    assert body["model_version"] == seeded_scores.version


def test_low_confidence_keeps_number_and_range(client, seeded_scores):
    body = client.post("/api/v1/mp-routes/900000002/safety", params={"target_date": "2026-07-04"}).json()
    assert body["data_status"] == "ok" and body["confidence"] == "low" and body["risk_score"] is not None


def test_insufficient_is_gray_with_reason_and_no_number(client, seeded_scores):
    body = client.post("/api/v1/mp-routes/900000003/safety", params={"target_date": "2026-07-04"}).json()
    assert (body["risk_score"], body["color_code"], body["data_status"], body["insufficient_reason"]) == \
        (None, "gray", "insufficient_data", "no_evidence")


def test_missing_row_is_unavailable_not_insufficient(client, seeded_scores):
    assert client.post("/api/v1/mp-routes/900000099/safety", params={"target_date": "2026-07-04"}).status_code == 404


def test_no_active_version_is_unavailable(client, seeded_scores_no_active):
    assert client.post("/api/v1/mp-routes/900000001/safety", params={"target_date": "2026-07-04"}).status_code == 404


def test_map_and_popup_agree(client, seeded_scores):
    bulk = {r["mp_route_id"]: r["safety"] for r in client.get(
        "/api/v1/mp-routes/map-with-safety", params={"target_date": "2026-07-04"}).json()["routes"]}
    single = client.post("/api/v1/mp-routes/900000002/safety", params={"target_date": "2026-07-04"}).json()
    assert bulk[900000002]["risk_score"] == single["risk_score"]
    assert bulk[900000002]["confidence"] == single["confidence"]


def test_predict_is_gone(client):
    assert client.post("/api/v1/predict", json={"latitude": 40.0, "longitude": -105.0,
                                                "route_type": "trad", "planned_date": "2026-07-04"}).status_code == 410
```

`seeded_scores` is a new fixture in `tests/conftest.py`. It uses `migrated_db()` plus Task 1's SEED and inserts the registry and rows directly (synthetic).

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the table. Every SQL statement is parameterised. `get_scores` batches the map bbox's route ids.
- [ ] **Step 4: Run.** `cd backend && uv run pytest tests/test_model_scores_api.py tests/test_no_fabricated_risk.py tests/test_risk_bands.py tests/test_insufficient_data.py -q`. Update the existing kernel-era assertions in those three files to the stored-score contract in this step, keeping their intent: no fabricated number, one band definition, insufficient is gray with no number.
- [ ] **Step 5: Commit** `feat(api): serve the active model's stored scores with confidence and range`.

---

### Task 23: Remove the kernel from serving; heartbeat replaces the nightly

**Files:**
- Delete: `backend/app/tasks/safety_computation_optimized.py`, `backend/app/services/location_safety_computation.py`, `backend/app/services/safety_algorithm.py`, `backend/app/services/safety_algorithm_vectorized.py`, and their kernel-only tests (`test_safety_algorithm.py`, `test_daily_variation*.py`, `test_longs_peak_daily.py`, `test_known_outcomes_validation.py`, `test_edge_cases_performance.py`, `test_performance.py`, `test_prediction_integration.py`, `test_predict_integration.py`, `test_hourly_is_climbable_cutoff.py` where it tests kernel hours)
- Move (`git mv`): the kernel math the adapter needs into `backend/app/ml/kernel_ref/`:
  - `safety_algorithm.py` (the dataclasses only, plus `calculate_safety_score`'s helpers it imports);
  - `safety_algorithm_vectorized.py`;
  - `weather_similarity.py`;
  - `algorithm_config.py`;
  - `{spatial,temporal,elevation,grade,route_type,severity}_weighting.py`;
  - `route_type_mapper.py`.

  `kernel_adapter.py` (Task 17) then imports from `app.ml.kernel_ref`.
- Create: `backend/app/tasks/scores_heartbeat.py`, `backend/tests/test_kernel_not_served.py`, `backend/tests/test_scores_heartbeat.py`
- Modify:
  - `backend/app/celery_app.py`: the beat entry `scores-heartbeat` at 02:00 UTC replaces the kernel nightly;
  - `backend/app/utils/time_utils.py`, `backend/app/utils/geo_utils.py`: constants they imported from `algorithm_config` are inlined with a pointer comment;
  - `utc_today`: moves from `app.services.temporal_weighting` to `app.utils.time_utils`, and the four pipeline imports (`mp_ticks_quarantine`, `mp_tick_aggregates`, `era5_window`, `accident_facts`) update;
  - `backend/app/tasks/cache_warming.py`: warms `safety:{version}:{route}:{month}` from `model.route_scores`.

**Interfaces:**
- `scores_heartbeat.run() -> dict` checks:
  - there is exactly one active version;
  - it has rows for the current month for every scorable route;
  - its `bundle_sha256` matches.

  It pings `HEALTHCHECKS_SCORES_URL` (and keeps pinging `HEALTHCHECKS_NIGHTLY_URL` so the existing dead-man check stays meaningful), or `/fail` with the reason.

- [ ] **Step 1: Write the failing tests.**

```python
# backend/tests/test_kernel_not_served.py
import ast
from pathlib import Path

APP = Path(__file__).parents[1] / "app"
SERVED = ("api", "tasks", "services", "schemas", "utils")


def test_nothing_served_imports_the_frozen_kernel():
    for pkg in SERVED:
        for f in (APP / pkg).rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                    assert not (mod or "").startswith("app.ml.kernel_ref"), f"{f} imports the kernel"


def test_kernel_modules_left_services():
    for name in ("safety_algorithm.py", "safety_algorithm_vectorized.py", "location_safety_computation.py",
                 "weather_similarity.py", "algorithm_config.py"):
        assert not (APP / "services" / name).exists()
```

`test_scores_heartbeat.py` covers:
- no active version → `/fail` with `no_active_version`;
- missing current-month rows → `/fail` with the count;
- healthy → success ping.

It uses `fakeredis` and the `healthchecks.ping` monkeypatch pattern from `test_healthchecks.py`.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Make the moves and deletions, then `rg "safety_computation_optimized|location_safety_computation|safety_algorithm|weather_similarity|algorithm_config" backend frontend scripts` and fix every remaining reference. `scripts/perf/*` kernel benchmarks are deleted too.
- [ ] **Step 4: Run.** `cd backend && uv run --group train pytest -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** `refactor: remove the kernel from serving; frozen copy for the backtest only; scores heartbeat`.

---

### Task 24: Migration `0013_drop_legacy_weather` (hard MVP-1 gate)

**Files:**
- Create: `backend/alembic/versions/0013_drop_legacy_weather.py`, `backend/tests/test_migration_0013.py`
- Delete: `backend/app/models/weather.py` (and its `__init__` export), `backend/app/pipelines/r6_verify.py`, `backend/tests/test_r6_verify.py`, `backend/tests/verify/` cells that read `weather`
- Modify:
  - `backend/db/roles/grants_phase2.sql`: delete `GRANT SELECT ON public.weather TO ingest;` and its comment, and add `REVOKE ALL ON public.weather FROM ingest;` guarded by `to_regclass` (no-op once dropped);
  - `backend/db/roles/verify_roles_phase2.sql`: delete the `('public.weather', 'SELECT')` row;
  - `backend/tests/test_roles_phase2.py`: delete the R6 `weather` block (lines 435–439) and the stray line `REVOKE SELECT ON public.weather FROM ingest;`;
  - `backend/tests/test_roles_phase3.py`: drop `public.weather` from the denied list;
  - `backend/pyproject.toml`: remove `app.pipelines.r6_verify` from mypy;
  - `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`.

**Interfaces:**
- `upgrade()` refuses unless an `ok` R6 run exists: `SELECT count(*) FROM source_ingest_log WHERE source = 'r6_verify' AND status = 'ok'` must be > 0, the evidence plan 3 required before the table could go. It then runs `DROP TABLE public.weather` (its owned sequence goes with it).
- `downgrade()` raises `NotImplementedError("0013 is one-way: restore weather from the pre-p3-4 snapshot branch")`, following the 0002 convention.
- No dump is committed. The rollback path is the `pre-p3-4` Neon snapshot branch, kept 30 days `[default pending: DP21]`.

- [ ] **Step 1: Write the failing test.**

```python
import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
R6_OK = ("INSERT INTO source_ingest_log (run_id, source, status, finished_at) VALUES "
         "('00000000-0000-0000-0000-000000000006', 'r6_verify', 'ok', now())")


def test_0013_refuses_without_r6_evidence():
    with migrated_db(revision="0012_model_registry") as name:
        with pytest.raises(RuntimeError, match="no ok r6_verify run"):
            command.upgrade(_alembic_cfg(name), "0013_drop_legacy_weather")
        assert fetch(name, "SELECT to_regclass('public.weather')") != [(None,)]


def test_0013_drops_weather_and_is_one_way():
    with migrated_db(revision="0012_model_registry", seed_sql=R6_OK) as name:
        command.upgrade(_alembic_cfg(name), "0013_drop_legacy_weather")
        assert fetch(name, "SELECT to_regclass('public.weather')") == [(None,)]
        with pytest.raises(NotImplementedError):
            command.downgrade(_alembic_cfg(name), "0012_model_registry")


def test_no_code_reads_weather_table():
    import re
    from pathlib import Path
    app = Path(__file__).parents[1] / "app"
    hits = [p for p in app.rglob("*.py") if re.search(r"\b(FROM|JOIN)\s+weather\b|models\.weather", p.read_text())]
    assert hits == []
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `PATH=/opt/homebrew/opt/libpq/bin:$PATH CI=1 uv run pytest tests/test_migration_0013.py tests/test_roles_phase2.py tests/test_roles_phase3.py tests/test_migrations.py -q` → PASS.

The other migration tests that pin `head` still pass. Any test that inserted into `weather` at head is updated to pin `0012` or drop the insert.

- [ ] **Step 5: Commit** `feat(db): 0013 drops the legacy weather table (MVP-1 hard gate)`.

---

### Task 25: Parity, no-coalesce guard, docs

**Files:**
- Create: `backend/tests/test_parity_scores.py`, `backend/tests/test_no_coalesce_in_ml.py`, `backend/tests/verify/test_model_scores.py` (`-m db`)
- Modify: `CHANGELOG.md`, `CLAUDE.md` (Safety-accuracy rules: the insufficient rule becomes "no geo evidence and no feature neighbour within d\*, frozen per version"; `MIN_ESTIMABLE_SCORE` is retired), `DEPLOYMENT.md`, `README.md` (the disclaimer text, P3:449), `docs/model-card.md`, `backend/app/services/risk_bands.py`

`risk_bands.py` change: delete `MIN_ESTIMABLE_SCORE` and `estimable_score`, and update `displayed_risk` and `cached_safety` to the stored contract. The docstring records "Phase 3 MVP-1 (2026-10-15): the interim raw<0.05 rule retired; insufficient is a stored model state."

- [ ] **Step 1: Write the failing tests.**
  - `test_parity_scores.py` (G6, A:140), on the `seeded_scores` DB plus a bundle built by `fixtures.toy_bundle_and_row`. For 100 sampled routes (all seeded ones in the fixture), `/safety`, `map-with-safety` and `/risk-breakdown` return identical `risk_score`, `confidence` and `score_range`, and `exp(sum(terms) + const)` equals the stored `rr_mean` within 1e-6.
  - `test_no_coalesce_in_ml.py` greps `app/ml/**/*.py` and `app/services/model_scores.py` for `coalesce(`, `or 0`, `nan_to_num(` and `fillna(0`. Its allowlist names exactly `features.py`'s masked `x_season`/`x_ticks` expressions and `archetypes.py`'s distance moments, each by line content, not line number.
  - `tests/verify/test_model_scores.py` (`-m db`, analyst): on prod or a branch, exactly one active version, every scorable non-unknown route × 12 months has a row, no `insufficient` row has a number, and `conf_level` counts match the model card.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → PASS. **Step 5: Commit** `test: score parity across endpoints; no-coalesce guard; docs for MVP-1`.

---

### Task 26: OWNER/AGENT RUNBOOK: migrate `0013`, deploy MVP-1 (branch, then prod); Phase 4 renumbering

- [ ] **Step 1 (agent, branch):**
  1. On a fresh branch `p3-4-rehearsal` of `production` (with `TARGET_HOST` set), save the evidence:
     - `save_once ../data/review/p3-4/r6.txt psql "$ANALYST_URL" -c "SELECT count(*) FROM source_ingest_log WHERE source='r6_verify' AND status='ok'"` (expect ≥ 1);
     - `save_once ../data/review/p3-4/active.txt psql "$ANALYST_URL" -c "SELECT version, scores_rows FROM model.registry WHERE status='active'"` (expect an `eb_v1` version).
  2. Run `MIG upgrade 0013_drop_legacy_weather`, `grants_phase2.sql`, `verify_roles_phase2.sql`, `grants_phase3.sql` and `verify_roles_phase3.sql`.
  3. Run `VERIFY_DATABASE_URL="$ANALYST_URL" uv run pytest -m db tests/verify/test_model_scores.py`.
  4. Point a local API at the branch and run the parity spot check `curl` for 3 routes (popup = map).
  5. `gate_pass p3_4_branch`.
- [ ] **Step 2 (owner, then agent, prod).**
  1. STOP for the owner's go. The owner snapshots `pre-p3-4` (kept 30 days, DP21).
  2. Agent: `gate_require p3_4_branch`, then the same block with `TARGET_HOST=production`, then `gate_pass p3_4_prod`.
  3. Migrate before deploy (`DEPLOYMENT.md`): only after `0013` is on prod does the owner merge PR 3-4 and let Railway deploy. Never merge first: the deleted `Weather` model would not matter, but the heartbeat and API need `model.*` grants, which are already in place since PR 3-0.
- [ ] **Step 3 (owner):**
  - add the healthchecks check `scores` (period 1 day, grace 2 h) as `HEALTHCHECKS_SCORES_URL` on the worker service;
  - remove the kernel nightly's Redis keys with the admin purge (`/mp-routes/admin/purge-queue` is unrelated; use `redis-cli --scan --pattern 'safety:*' | xargs redis-cli del` from the owner's machine against the Railway Redis).
- [ ] **Step 4 (agent): relaunch notes.** `CHANGELOG.md` states that the map, popup and breakdown now show one stored number. The A:44 relaunch caveat (two scorers disagree) is retired.
- [ ] **Step 5 (agent): Phase 4 renumbering.** Edit `docs/superpowers/plans/2026-10-12-phase4-users-incident-reporting.md`:
  - `0012_accounts` → `0014_accounts` (`down_revision = "0013_drop_legacy_weather"`), `0013_reports` → `0015_reports`, `0014_report_promotions` → `0016_report_promotions`;
  - the test files, `migrated_db` pins, downgrade targets and `MIG current` lines.

  Commit `docs(plan): Phase 4 migrations follow Phase 3 (0014-0016)`.

---

# PR 3-5 — `feat/p3-frontend-contract`

### Task 27: Types and fetch contract

**Files:**
- Modify: `frontend/src/services/api.ts`, `frontend/src/services/api.test.ts`, `frontend/src/hooks/useRouteSafety.ts`, `frontend/src/hooks/useRouteSafety.test.ts`

**Interfaces:**

```ts
export type Confidence = 'high' | 'medium' | 'low';
export type ScoredRisk = {
  risk_score: number; color_code: RiskColorCode; data_status: 'ok';
  confidence: Confidence; score_range: [number, number]; model_version: string;
  evidence: { geo: number; similar: number };
  relative_rate: { mean: number; lo: number; hi: number } | null;
  per_10k_logged_ascents: { mean: number; lo: number; hi: number } | null; per_10k_reason: string | null;
};
export type InsufficientRisk = {
  risk_score: null; color_code: 'gray'; data_status: 'insufficient_data';
  insufficient_reason: 'no_evidence'; model_version: string;
};
```

`fetchRouteSafety` drops `bypass_cache` (A:137). `parseSafety` rejects:
- a `ScoredRisk` missing `confidence` or `score_range`;
- a range not bracketing the score;
- any non-finite number.

A rejection throws, so the caller shows "Unavailable". A 404 also renders "Unavailable", never gray.

- [ ] **Step 1: Failing tests** in `api.test.ts`:
  - a well-formed low-confidence payload parses;
  - a payload with `score_range` [50, 60] and score 40 throws;
  - an insufficient payload without `insufficient_reason` throws;
  - `fetchRouteSafety` sends no `bypass_cache` param;
  - 404 → `Unavailable` state in `useRouteSafety`.
- [ ] **Step 2:** `cd frontend && npm run test:run -- api useRouteSafety` → FAIL. **Step 3: Implement.** **Step 4:** `npm run test:run && npm run typecheck && npm run lint` → PASS. **Step 5: Commit** `feat(frontend): stored-score contract with confidence and range`.

---

### Task 28: Honest display: number + range + hatch for low, gray only for no evidence, neutral lowest band

**Files:**
- Modify: `frontend/src/utils/riskUtils.ts`, `frontend/src/utils/riskUtils.test.ts`, `frontend/src/utils/riskPalette.test.ts`, `frontend/src/components/MapView.jsx`, `frontend/src/components/RouteAnalyticsModal.jsx`, `frontend/src/components/RiskLegend.jsx`, `frontend/src/components/RiskLegend.test.jsx`, `frontend/src/components/RouteAnalyticsModal.safety.test.jsx`, `backend/app/services/risk_bands.py` and `backend/tests/test_risk_bands.py` (only if the colour name changes; see below)

**Rules:**
- **Bands stay 25/50/75** (`RISK_BAND_THRESHOLDS` unchanged; `test_risk_bands.py` still pins the mirror).
- **Lowest band** `[default pending: DP12]`: rendered in a neutral slate, labelled "Lower than typical for this type", never "Low Risk" or "safe" (P3:445, A:179 Q3). The wire value `color_code: "green"` stays, so the backend contract and the drift test are untouched; only the palette and copy change. `RISK_DESCRIPTIONS.low` loses "Favorable conditions".
- **Low confidence** shows:
  - the number and its range ("40 (likely 20–60)");
  - a "Low confidence" label;
  - on the map, the band colour with a diagonal hatch (an SVG `pattern` fill on the marker).
- **Medium/High** show the number and the range, with no hatch.
- **Insufficient** shows the gray chip "Not enough reported incidents or similar routes to estimate" with no number and no range. On the map: a gray marker with the neutral hatch (P3:446).
- **Unavailable** shows an em-dash, neutral, with no hatch.
- **Disclaimer on every score** (P3:449): "Based on reported incidents and estimated climbing traffic, not on the current condition of this route."
- **Evidence line:** "≈ {geo} local, {similar} from similar routes elsewhere", from `evidence` (A:99), rounded to whole numbers, with "≈" always shown.

- [ ] **Step 1: Failing tests.**
  - `riskUtils.test.ts`:
    - `getRiskDescription(10)` contains "Lower than typical" and not "safe"/"Low Risk";
    - `markerStyle({confidence: 'low'})` has `hatch: true`;
    - `markerStyle(insufficient)` is gray with the neutral hatch;
    - `markerStyle(unavailable)` has no colour.
  - `RouteAnalyticsModal.safety.test.jsx`:
    - low confidence renders "Low confidence", the range and the disclaimer;
    - insufficient renders the gray chip and no digits;
    - a 404 renders "Unavailable".
  - `RiskLegend.test.jsx`: the legend shows the hatch swatch "Low confidence", the gray swatch "Not enough evidence", and no "safe".
- [ ] **Step 2:** → FAIL. **Step 3: Implement.** **Step 4:** `npm run test:run && npm run typecheck && npm run lint && npm run build` → PASS. **Step 5: Commit** `feat(frontend): confidence hatch, ranges, neutral lowest band, disclaimer`.

---

### Task 29: Ascents tab headline

**Files:**
- Modify: `frontend/src/components/RouteAnalyticsModal.jsx`, `frontend/src/utils/ascentCounts.ts`, `frontend/src/utils/ascentCounts.test.ts`, `frontend/src/components/RouteAnalyticsModal.ascentsChart.test.jsx`

**Rules** `[default pending: DP13]`:
- **Headline:** "{mean}× ({lo}–{hi}×) the reported-incident rate of {type} routes nationally in {month}", with the confidence label, from `relative_rate` (A:121–131 option C).
- **Below it:** "About {N} per 10,000 logged ascents (likely {A}–{B}) · {confidence}", only when `per_10k_logged_ascents` is present (A:117). Otherwise one line from `per_10k_reason`: "Logged ascents still being collected for this area" (`ticks_incomplete`), "No logged ascents for this route type here" (`no_ticks_logged`), "Logged ascents unavailable" (`ticks_missing`).
- **The existing counts** ("N accidents · M logged ascents", A:118) stay as context, labelled "logged ascents, a partial sample", with no rate computed in the frontend.
- **Insufficient** routes show the gray chip instead of the headline.

- [ ] **Step 1: Failing tests:**
  - the headline renders from `relative_rate`;
  - the per-10K line appears only with data, and each reason renders its sentence;
  - no division occurs in `ascentCounts.ts` (a grep test: no `/` operator applied to accident and tick counts).
- [ ] **Step 2:** → FAIL. **Step 3: Implement.** **Step 4:** `npm run test:run && npm run typecheck && npm run lint` → PASS. **Step 5: Commit** `feat(frontend): Ascents tab relative-rate headline and per-10K line`.
- [ ] **Step 6:** CHANGELOG entry for PR 3-5.

---

## Appendix A: Design points, each `[default pending: DPn]` (owner to confirm or overrule)

| DP | Question | Default this plan is written against | Main alternative |
|---|---|---|---|
| DP1 | Where do the registry and stored scores live? | New schema `model` (owner-created). `trainer` writes, `app`/`analyst` read. Phase 1's public write check is untouched. | `public` tables plus an edit to `verify_roles.sql`'s `app_writes`. |
| DP2 | Daily `route_daily_scores` from a Celery nightly, or per-month rows per version? | Per (version, route, month), materialized by the trainer at train time. CM = 1 in MVP-1, so daily rows would repeat; `app` stays read-only. The daily table arrives with v2.1 rules. | The P3 nightly writing `route_daily_scores` as `app` (~67M rows per 400 days, plus an `app` write grant). |
| DP3 | `rock` (63% of routes)? | Its own type group, pooled with sport/trad through a family effect and a ridge on type effects. | Fold `rock` into sport/trad by a rule. That loses the 63% that has no discipline. |
| DP4 | Placing incidents in cells | Route link → its area and group. Area link or `unknown`-type route → that area, split by route counts. Point-only trusted geocode → nearest scorable area within 3 km. Else state-only (counts toward evidence, not cells). Boulders dropped. | Use the kernel's text type-inference for unlinked incidents; allocate state-only incidents to cells by exposure. |
| DP5 | Rock ticks in the fit while scraping is partial and accident-first | Tick covariate on for ice/mixed now; on for the rock family only once national rock coverage ≥ 0.90. No coverage or missingness term ever enters the fit (it would encode the outcome). Ticks are normalized per snapshot. | Enter rock ticks now with coverage as a covariate (exposure plan DP2 option). |
| DP6 | Training window | Incidents from 1990-01-01 (matches the ERA5 floor and the exposure era). | All years with trustworthy dates. |
| DP7 | Bundle format | Zip of `.npy` plus `manifest.json`, numpy-only, no pickle. | P3:136 Parquet (adds pyarrow to the API image). |
| DP8 | Training libraries | scipy in a new `train` group (gamma quantiles, NB/PIT). NumPyro/JAX wait for v2.2. Hypothesis in `dev`. | Write gamma quantile inversion by hand to avoid scipy. |
| DP9 | Archetypes | Own k-means (k-means++, fixed seed) on 7 standardized area features; K from {8,16,32,64}; feature weights from one coordinate pass over {0,1,2}, all chosen on held-out folds. | Equal weights only; or k-prototypes with expert-set weights (A:180 Q4). |
| DP10 | Spatial CV regions | 8 fixed state groups (`cells.STATE_GROUPS`). | Data-driven clusters of states by incident volume. |
| DP11 | Confidence and interval calibration | φ widening chosen for 90% PIT coverage. `t_high`/`t_medium` from {2,…,15}, maximizing High+Medium share subject to per-level coverage, monotone error and Low-stratum calibration. | Fixed thresholds (for example 3 and 8) without CV. |
| DP12 | Lowest band colour (A:179 Q3) | Neutral slate, "Lower than typical for this type". The wire value stays `green`, so the contract and drift test are unchanged. | Keep green through MVP-1. |
| DP13 | Ascents headline (A:177 Q1) | Option C relative rate as the headline. The per-10K line only at full tick coverage, otherwise a reason sentence. | Per-10K as the headline wherever ticks exist. |
| DP14 | Prospective kernel backtest | Keep the window out of the 1-year purge now. Gate on top-10% capture. Kernel log score uses an in-window calibration map (kernel-favouring), reported, not gated. | Gate on log score too; or freeze a copy of the window in `model`. |
| DP15 | Rollback target | Never the kernel. The kernel row retires at the first promotion; `/rollback` with no servable previous refuses. | Keep kernel code servable as a fallback. |
| DP16 | Public promotion artifacts | Aggregate-only issue (band matrix, per-state/type tables, metric deltas). No PNG map in MVP-1, no route ids. Per-route movers stay in the DB. | The P3:319 PNG + GeoJSON artifacts (route-level data in a public repo's artifacts). |
| DP17 | Trainer grants | LOGIN via owner script, connection limit 4. SELECT on an explicit allowlist of views/tables plus `internal.exposure_index`; writes only on `model.*`. | Broader "SELECT on data tables" (P3:230). |
| DP18 | Plan scope | MVP-0 + MVP-1 only. v2.1 rules/hazard banner, v2.2, v2.3 and objective-level scoring are later plans. | Fold v2.1 rules into this plan. |
| DP19 | Year-precision spread | Weight inside a window = overlap days / interval days; months spread by the group's national seasonal profile (0.5 pseudocount per month). | Uniform month spread. |
| DP20 | Missing covariates in the GLM | Standardized 0 (training mean) plus a missing indicator; never the physical value 0. | Drop rows with missing features (loses whole regions). |
| DP21 | `weather` drop recovery | No committed dump; the `pre-p3-4` snapshot branch is kept 30 days; R6 evidence is required by the migration. | Export the table to the owner's private storage first. |
| DP22 | Map bulk reads | DB reads by route-id batch, cached in Redis under `(version, route, month)`. | Precomputed per-tile caches. |
| DP23 | `POST /predict` (arbitrary point) | Retired (410). No type or features exist for an arbitrary point; the map is the surface. | Score the nearest scorable area within 3 km. |
| DP24 | Kernel-only endpoints (`time-of-day` risk, `historical-trends`) | Hourly risk `null` with a note until v2.1. Trends show the v2 12-month profile, not kernel history. | Keep showing kernel history labelled "previous model". |

## Appendix B: Inputs this plan expects from other plans

- **Exposure plan (X-2):**
  - `internal.exposure_index`, with the `proxy` keys `route_density`, `season_share` and `mp_ticks` (with `per_route_mean`, `coverage`);
  - `tick_coverage`;
  - `exposure build --cutoff`;
  - `exposure_math.spearman`.
- **Objectives re-plan:** objective-level scoring and `objective_daily_scores` (P3:159, 163–169).
- **Phase 4 PR 4-8:** per-incident weights. `IncidentRow.weight` (default 1.0) is the hook; PR 4-8 fills it from `reports.report_promotions.model_weight` and adds the retrain trigger and caps.
- **R11 refresh:** Feb–Jul 2026 incidents. Until they land, `Suite.prospective` is `None` with reason `no_test_incidents` and DP14's gate component is skipped, not failed.

## Self-review (2026-10-15)

- **Spec coverage:**
  - P3 MVP-0:
    - registry and `trainer` (Tasks 1–3);
    - the kernel through the harness and the kernel row (Tasks 17, 20–21);
    - re-measured counts on the MP tree, reported in the model card (Task 20 `model_card.catalog`: route-bearing regions and leaf areas from `CellData`);
    - the audit doc is not committed here (it lives with the 2026-09 audit owner notes; flagged as a gap for the owner).
  - P3 MVP-1:
    - `exposure.py` (folded into `features.py`), `baseline_eb.py` (`glm.py` + `eb.py`), `scoring.py` (`posterior.py` + `materialize.py`), `bundle.py`, `thresholds.py` (`tuning.py`), `promote.py`;
    - stored scores (DP2), serving and the detail endpoint on stored scores plus `explain()`;
    - the insufficient UX, the disclaimer, `model-train.yml`/`model-rollback.yml` and the capacity benchmark (Tasks 8–29);
    - the three kernel implementations removed from serving (Task 23).
  - Amendment:
    - D1 (Tasks 11–12, 15);
    - D2 (Tasks 13, 28);
    - D3 (Tasks 13, 22, 28, G7 reported in Task 17's report by band scheme);
    - D4 (Task 22);
    - D5 (Task 29);
    - G1–G7 (Tasks 17–19, 25).
  - Hard gate: the `weather` drop (Task 24).
- **Gaps flagged, not silently dropped:**
  - The audit doc commit (P3:504).
  - The map PNG (DP16).
  - Objective scoring (DP18).
  - `model.registry` metrics jsonb shape is defined by Task 20's code, not a migration CHECK (it is checked by `test_train_cli.py`).
- **Placeholder scan.** The core selection and gate code (Tasks 18–19) is given in full. Tasks 22–29 (serving and frontend) give exact contracts, decision tables, copy strings and complete tests; their bodies are direct translations of those tables. No "TBD" remains.
- **Type consistency:**
  - `Thresholds(d_star, conf_high, conf_medium, phi)` is used identically in Tasks 13, 15, 17 and 18.
  - `ModelParams` fields are used identically in Tasks 15, 17 and 18.
  - `Counts(y, state_mass, years, dropped, placed)` is used in Tasks 7, 15 and 17.
  - `CellPost.chain` keys (`omega_*`, `O_*`) are defined in Task 12 and read in Task 13.
- **Review Focus:** each item names its owning test.
