# Production rollout runbook: 0004 to a promoted Phase 3 model, then deploy (2026-11-15)

This runbook takes the production Neon database from alembic `0004_phase2a_foundation` to a trained, promoted Phase 3 model, then deploys it and relaunches the Railway services. It does not replace the plan runbooks. It puts them in one dependency order, records what production holds today, and lists the corrections each plan runbook needs before it runs against current `main`. Where a plan block is correct as written, this document points at it ("plan X Task N Step M") instead of copying it. Where a plan block is wrong for current `main`, this document gives the corrected block.

The owner approved writing and executing this rollout on 2026-11-15 ("get production up and running as much as possible"). Section 4 lists what an agent can run under that approval and what has to wait for the owner.

## How to use this runbook

- **Checkout.** Everything runs from the main checkout (`<repo-root>`, the one that holds the 0600 `backend/.env.<role>` files), from `backend/`, after `. scripts/runbook_helpers.sh`. The env files exist only there (plan 2a-2 Task 13 Step 0, C4). Agents never read, copy, print or link them into a worktree. The plans' Step 0 blocks that check out a feature branch (`feat/phase2a-conditions-refresh`, `feat/mp-catalog`, `feat/exposure-rock-ticks`) or remove a review worktree are obsolete: every plan has merged. Run from `main` instead (defect K5).
- **Helpers.** Only those in `backend/scripts/runbook_helpers.sh`: `ING`, `INGMOD`, `INGLLM`, `MIG`, `TRAIN`, `OWNER_PSQL`, `ANALYST_PSQL`, `VERIFY`, `save_once`, `gate_pass`, `gate_require`, `same_ids`, `bg_start`, `bg_wait`, `branch_host`. There is no `$OWNER_URL` or `$MIGRATOR_URL`.
- **Target.** `TARGET_HOST` is always explicit: a Neon branch host from `branch_host <name>`, or the literal `production`. The helpers refuse while it is unset.
- **Pattern for every stage.** Each stage first rehearses on a fresh Neon branch of `production`. The owner signs off on the evidence. The owner then gives a written go-ahead for prod. Before the first prod write, a snapshot branch `pre-<stage>` is created from `production`, and the prod blocks run behind `gate_pass`/`gate_require` markers. The prod gates compare prod's dry runs with the branch's byte for byte. So each rehearsal must branch from production as it stands after the previous stage's prod run. Stages are strictly sequential.
- **Long runs.** Anything that may run longer than about 9 minutes goes through `bg_start LOG CMD…` and `bg_wait LOG`, in separate blocks. `bg_wait` returns 0 when the run is done, 2 while it is still running (call it again; never start a second run), and 1 when the run failed (STOP and report). Run one data job at a time. `bg_wait` counts any `app.data.repair` or `app.pipelines` process on the machine as live, so two concurrent jobs confuse it.
- **STOP means STOP.** If a block does not end with its expected `GATE OK: …` or `finished ok` line, run nothing further and report to the owner.
- **Secrets.** Never echo a secret or print a connection string. Always run `psql -X` (`OWNER_PSQL` and `ANALYST_PSQL` already pass it). Never run `psql -L`.

**Who does what.** Each step is labelled with one of these:

| Label | Meaning |
|---|---|
| AGENT | An agent runs it under the owner's CLI authorization, including Neon branch and snapshot creation (the plans allow this). |
| OWNER-REVIEW | The owner hand-reviews a named file or printout and makes a named decision in writing. |
| OWNER-GO | The owner's written prod go-ahead for that stage ("go ahead with prod"). Every plan requires it in the executing session, separately from the sign-off. The 2026-11-15 approval covers a stage only if the owner says so in writing. |
| OWNER-SQL | SQL the owner runs as the owner role (`OWNER_PSQL` with the owner's own env), for example the trainer password. |
| OWNER-OPS | Dashboard or account actions only the owner can take: Railway, healthchecks.io, the Anthropic console, `gh` mutations. |
| SPEND | Money, with the amount or cap and what it buys. |

## 1. Current state of production (read 2026-11-16, 04:5x UTC)

All reads were made as `analyst` (read-only) through `ANALYST_PSQL` with `TARGET_HOST=production`, plus `neonctl branches list`. No write was made anywhere.

### 1.1 Schema and data

| Item | Value | How it was read |
|---|---|---|
| alembic revision | `0004_phase2a_foundation` | `SELECT version_num FROM alembic_version` |
| Code head on `main` | `0013_drop_legacy_weather` | `uv run alembic heads` |
| Schemas | `public`, `internal` (no `model`) | `pg_namespace` |
| Extensions | `plpgsql`, `postgis` 3.3.3 (no `ltree`) | `pg_extension` |
| `internal` tables | `accident_revisions`, `accidents_raw`, `ingest_quarantine`, `mp_tick_aggregates` | `information_schema.tables` |
| Later tables | none from 0005 to 0012 (no `gnis_summits`, `cell_daily_conditions`, `canonical_routes`, `feature_points`, `exposure_index` or `model.*`) | same |

Row counts:

| Table | Rows |
|---|---|
| `accidents` | 4,790 (1,183 carry an MP route id) |
| `internal.accidents_raw` | 4,790 |
| `internal.accident_revisions` | **0** |
| `internal.ingest_quarantine` | 677 (pre_1970 496, future_month 126, partial_month 55) |
| `internal.mp_tick_aggregates` | 71,354 |
| `mp_locations` | 61,017 |
| `mp_routes` | 168,055 |
| `mp_ticks` | 143,346 |
| `routes` / `mountains` (legacy) | 778 / 441 |
| `weather` (legacy) | 83,204 |
| `area_weekly_weather` | 50,281 |
| `historical_predictions` | 23,233,326, dated 2026-02-05 to 2026-07-02 (exactly the Phase 3 prospective backtest window) |
| `source_ingest_log` | 2 |

`source_ingest_log` holds two runs:
- `mp_ticks_quarantine`: `ok` on 2026-09-30. 143,346 rows in, 41,524 quarantined (R8).
- `mp_tick_aggregates`: `ok` on 2026-10-02. 72,031 rows in, 71,354 loaded, 677 quarantined (ice/mixed).

### 1.2 What has already run on prod

- **Done.** Foundations Tasks 8–10 are on prod: 0004 applied, Phase 2 roles created, R8 run, ice/mixed tick aggregates loaded.
- **2a-1 is not applied.** `internal.accident_revisions` is empty, and every `accidents.date_precision` and `severity_scale` is NULL. So r2r1, r9 and r1v2 have not run on prod.
- Nothing from 2a-2 onwards has run on prod.

### 1.3 Roles

Read from `pg_roles` and `pg_auth_members` only.

| Role | Can log in | Notes |
|---|---|---|
| `analyst`, `app`, `ingest`, `migrator` | yes | |
| `trainer` | **no** | NOLOGIN, also on `p3-3-rehearsal` |
| `neondb_owner` | yes | Holds `migrator` with INHERIT and SET, so the relaunch revocation (Phase 1b Task 26 Step 3) has not run. Holds ADMIN on `analyst`, `app`, `ingest` and `trainer`. |

Privileges that matter for the next stages, read with `has_table_privilege`:
- `migrator` has no `CREATE` on the database. So the owner creates `ltree` and schema `model`.
- `ingest` lacks the 2a-1 grants: no SELECT on `accidents`, no INSERT on `internal.accident_revisions`, no UPDATE on `accidents.date_precision`, no SELECT on `internal.accidents_raw`.
- `ingest` has no SELECT on `weather` (see defect K6).

### 1.4 Neon branches

| Branch | Id | Created (UTC) | State |
|---|---|---|---|
| `production` | `br-restless-bar-ajw5zy4b` | 2026-02-02 | default |
| `p3-3-rehearsal` | `br-fancy-star-ajxkvzmy` | 2026-11-16 04:41 | Untouched copy of prod: 0004, 0 revisions, 2 log rows, `trainer` NOLOGIN |
| `p2a-1-rehearsal` | `br-orange-art-aj6brc23` | 2026-10-01 | 0004. Revisions: `r1-v1` 1,978, `r1-v2` 1,042, `r2-v1` 4,368, `r9-v1` 4,790. Log rows for r1, r2, r9, r1v2 and the three hand checks. |
| `pre-2a` | `br-old-sky-ajw8afjf` | 2026-09-30 | Snapshot taken before 0004 |
| `rehearsal2-2026-09-28` | `br-soft-lake-aj3x8ulz` | 2026-09-28 | Phase 1 rehearsal |

### 1.5 Local evidence (`data/review/` in the main checkout; gitignored, counts only)

What exists:
- 2a-1 rehearsal evidence: `p2a1_step2_branch.txt`, the dry runs `p2a1_r2r1_dry_branch.json`, `p2a1_r9_dry_branch.json` and `p2a1_r1v2_dry_branch.json`, and `p2a1_r1v2_backtest_branch.json`.
- The imports `p2a1_r1_import_branch.json`, `p2a1_r9_import_branch.json` and `p2a1_r1v2_import_branch.json`. Their agreements: r1 1.0 on n=40, r9 1.0 on n=50, r1v2 1.0 on n=40.
- The filled hand checks `r1_year_check.csv`, `severity_check.csv` and `r1v2_year_check.csv`.
- `logs/r1v2_apply1_branch.log` and `logs/r1v2_apply2_branch.log`. Both have `RUNBOOK_EXIT=0`; they took 184 s and 39 s, and apply 2 applied 0.

The owner signed off the 2a-1 numbers in session on 2026-10-21 (PR #16 body: "Owner signed off on these numbers in session 2026-10-21"; Step 5e: 9 of 9 cells passed). The same body says prod still needs a separate written "go ahead with prod". So the 2a-1 rehearsal is complete through plan 2a-1 Task 12 Step 5e.

What is missing:
- Any `gates*` directory.
- All evidence for 2a-2, 2a-3, 2a-4, the ERA5 window, the catalog (mpc), exposure (xp-1, xp-2) and Phase 3 (`p3-0/`, `p3-3/`, `p3-4/`).
- The GNIS zip.

Other files:
- `data/golden/activity_v1.csv` and `data/golden/experience_v1.csv` hold only their header rows: the owner has not labelled them yet.
- In `~/Developer/safeascent-private/`, the exports `exports/rock_20261022.sqlite` and `exports/ice_mixed_20261022.sqlite` exist. There is no `manual/` folder for the R11 facts CSVs.
- `backend/.env.trainer` exists (file mtime 2026-11-15 23:37 local; contents not read). `trainer` still cannot log in anywhere, so the password in that file has not been applied to any database.

### 1.6 Code and services

- `origin/main` is `ff3d160` (PR #22, fetched during this inspection). It changes only doc path placeholders, frontend dev dependencies and tracked junk relative to `33820ef`, where the main checkout is detached. No backend code, migration, role script or helper changed between the two.
- `main` already contains everything through PR 3-4 (serving reads `model.registry` and `model.route_scores`), and its models map tables from 0005 to 0012. **Any deploy of `main` before Stage F has promoted a model breaks the API** (defect K16).
- Railway: the CLI is linked to project `safeascent`, environment `production`, with no service linked. Service states and auto-deploy settings were not inspected. Per the owner: the services were taken down on 2026-09-24, and auto-deploy is off.

## 2. Known defects: fix or decide before running

Each defect has an id. The stages refer to these ids.

**K1. The role scripts are cumulative and fail at every intermediate revision (blocks A, B, C and D).**
- What is wrong:
  - `db/roles/grants_phase2.sql` on `main` grants on tables from 0005 to 0011 without guards: `gnis_summits` at line 59, `cell_daily_conditions` at 69, `canonical_*` at 117, `feature_points` at 126, `internal.exposure_index` at 149, and more.
  - It runs as one transaction under `ON_ERROR_STOP`. At 0004 it stops at `gnis_summits` and grants nothing.
  - `verify_roles_phase2.sql` has one `to_regclass` guard and checks the full set.
  - So every plan's grants step fails on prod and on a fresh branch until 0011: 2a-1 6a, 2a-2 12c, 2a-3 6c, 2a-4 10c, and mpc Task 6 4b (at 0010, `exposure_index` is missing).
- It fails closed: a STOP, never a partial grant.
- **OWNER-REVIEW decision (security-relevant: grants).** Pick one:
  - **(a) Recommended.** A small reviewed PR that makes both scripts revision-aware: wrap each plan's block in a `to_regclass(...) IS NOT NULL` guard, and make the verify script skip a block whose tables do not exist yet. Add tests in `tests/test_roles_phase2.py` that run the scripts at 0004, 0006, 0007, 0010 and 0011.
  - **(b)** Run the historical script from the commit that matched each revision. This is clean only for Stage A: `6cdf317` (PR #8, the 2a-1 merge) references only 0004 objects. There is no first-parent commit whose scripts match 0006, 0007 or 0010, so (b) does not cover B–D.
  - **(c)** Reorder the migrations. This changes every gate and is not recommended.

**K2. `MIG upgrade head` now means 0013 (blocks B and C).**
- Where: plan 2a-2 Task 13 Step 1 and 12c, plan 2a-3 Task 12 Step 1 and 6c, and plan 2a-4 Task 13 Step 2 if it upgrades.
- Effect: on `main` these blocks would try to go to 0013. env.py runs the upgrade as one transaction, so it fails at 0008 (no `ltree`) and rolls back everything. That is safe, but it is a STOP.
- Fix: pin the revision, `MIG upgrade 0006_accidents_clean` and `MIG upgrade 0007_cell_daily_conditions`. The catalog, exposure and Phase 3 plans already pin theirs.

**K3. The `MIG current` gates expect exact `(head)` strings that `main` no longer prints.**
- Where:
  - plan 2a-1 Task 12 6a: `"0004_phase2a_foundation (head)"`;
  - plan 2a-2 12c: `"0006_accidents_clean (head)"`;
  - plan 2a-3 6c: `"0007_cell_daily_conditions (head)"`;
  - plan 2a-4 10a and plan 2a-3 Task 14 4a: `"0007_cell_daily_conditions (head)"`.
- On `main`, alembic prints these without ` (head)`. (Alembic appends ` (head)` only at a head; inferred from alembic's documented behaviour, since `MIG current` was not run in this inspection.)
- Fix: use the corrected gate idiom, which accepts both forms:

```bash
cur="$(MIG current)" || exit 1; printf '%s\n' "$cur"
case "$cur" in <rev> | "<rev> (head)") ;; *) echo "prod is not at <rev>: STOP" >&2; exit 1 ;; esac
```

**K4. `MIG check` is meaningful only at head.** It compares the database with `main`'s models, which map every table through 0013. At any earlier revision it reports pending operations and exits non-zero. Drop `MIG check` from Stages B–F (plan 2a-2 12c, 2a-3 6c, 2a-4 Step 2, mpc Task 6 Step 1 and 4b) and run it once, after 0013 in Stage G.

**K5. The Step 0 checkouts are obsolete.** Do not check out feature branches or remove worktrees. Before Stage A, advance the main checkout to the merged code. The tree is clean; this needs no `--force`:

```bash
git -C <repo-root> fetch origin && git -C <repo-root> checkout --detach origin/main
```

Re-run it after any PR this runbook needs (K1, K18) has merged, and record the commit in each stage's evidence.

**K6. R6 cannot run as written, and 0013 needs it (blocks C's verify and G).**
- What is wrong:
  - `weather` has 83,204 rows. So `0013` refuses unless `source_ingest_log` holds an `ok` `r6_verify` row meeting n > 100, r_tmax > 0.95 and p99_gust < 60.
  - `app/pipelines/r6_verify.py` was deleted with Phase 3 Task 24. It is restorable from `44ca2b8`.
  - On prod, `ingest` has no SELECT on `weather`, and `main`'s `grants_phase2.sql` revokes any such grant on every run.
  - Plan 2a-3 Task 14 Step 3 and 4g still call `INGMOD app.pipelines.r6_verify`, and the R6 verify cell is gone from `tests/verify`.
- **OWNER-REVIEW decision.** The proposed path:
  - after the Stage C3 window, in a scratch checkout outside the repo, restore the module and its test (`git show 44ca2b8:backend/app/pipelines/r6_verify.py`; DEPLOYMENT.md);
  - the owner grants `SELECT ON public.weather TO ingest` (OWNER-SQL; temporary);
  - the agent runs R6 as `ingest` against the target;
  - the agent re-runs `grants_phase2.sql`, which revokes the grant, then `verify_roles_phase2.sql`;
  - the agent saves `data/review/p3-4/r6.txt` per plan Phase 3 Task 26 Step 1.
- Never commit the restored module.

**K7. `branch_host` and `--no-secrets`.**
- Tested on 2026-11-16: `neonctl connection-string … --no-secrets` is accepted, but the URL it returns still holds a password. `branch_host` is safe because it pipes the URL into Python and prints only the host.
- Adding the flag is harmless but is not a fix. Keep the pipe, and never run `neonctl connection-string` unpiped.
- `branches create` does have `--secrets` (default true) / `--no-secrets`, and every plan already passes `--no-secrets` there.

**K8. neonctl 6.0.0 and `--org-id`.**
- Without `--org-id`, `neonctl projects list` opens an interactive organisation prompt, which hangs an agent.
- `branches list` and `connection-string` with `--project-id still-morning-74008008` ran without it.
- `branches create` was not tested (it is a write), and its help lists no `--org-id`.
- Rule: if any neonctl command prompts, kill it and re-run with `--org-id <owner's org id>`. Take the id from the owner's Neon console; do not commit it. Never pipe a command that may prompt.

**K9. DEPLOYMENT.md's "roles are shared across a project's branches" is unverified.**
- This inspection shows only that `analyst` logs into branches with prod's password. That is consistent with roles being copied when a branch is created; it does not prove a later change propagates.
- The question is moot for the password: `trainer_login_phase3.sql` sets the password from `.env.trainer` on whichever target it runs. It matters for the guard: if roles were shared, the prod run would refuse with "trainer can already log in".
- Check (read-only), right after the branch login in Stage F:

```bash
( . scripts/runbook_helpers.sh && export TARGET_HOST=production && ANALYST_PSQL -At -c "SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer'" )
```

  `f` means roles are per branch: run the login on prod too. `t` means they are shared: skip the prod login.
- In both cases skip `write_role_url` for `trainer`, because `.env.trainer` already exists and regenerating it would rotate the password.

**K10. Gates and evidence.**
- `runbook_helpers.sh` defaults `GATE_DIR` to `../data/review/gates`. That default is used by the DEPLOYMENT.md PR 3-0 runbook (`p3_0_branch`, `p3_0_prod`) and by Phase 3 Task 21 (`p3_3_branch`). Every Phase 2 plan sets its own `gates_<stage>_prod`.
- No gates directory exists yet. That is expected, since no prod stage has run.
- No exposure evidence (`xp1_*`, `xp2_*`) exists either: Stage E produces it from scratch.
- Keep the default `gates/` for the Phase 3 blocks, so branch and prod gates meet in one directory.

**K11. `p3-3-rehearsal` already exists.** It is at 0004, so it is useless for Task 21, which needs a branch of prod at 0012 and creates it under this name. Before Stage F's Task 21 Step 2, the owner consents to deleting it, and the agent recreates it from production. Until then it can serve as a read-only reference copy of prod at 0004.

**K12. The Stage A branch evidence predates current code.**
- The branch dry runs were saved in October; r1-v2 merged on 2026-11-16 03:02 UTC (PR #16).
- Gate 6b/6c tolerates only the two added keys, `proposed_total` and `proposed_by_field`. Any other output drift is a STOP.
- If it STOPs on drift and not on data, the owner decides whether to re-baseline the dry runs on a fresh branch of prod (`p3-3-rehearsal` is one) before prod.

**K13. The Phase 1b relaunch checklist (Task 26) conflicts with later steps.**
- Step 3 (`REVOKE migrator FROM CURRENT_USER`) removes the owner's SET on `migrator`. Every grants run (`grants_phase2.sql`, `grants_phase3.sql`) and the catalog `VACUUM` need that SET. So do it last, after 0013 and the final grants run, and re-grant the membership before any future migration.
- Step 5 checks `historical_predictions` rows from the nightly, which no longer exists. Replace it with the scores heartbeat check (DEPLOYMENT.md "Scores heartbeat").

**K14. Plan 2a-2 Task 13 Step 13 refers to `0011_objectives`.** That number is stale (0011 is now `exposure_index`), and the objectives plan is out of scope. Skip it.

**K15. Neon branch count.**
- The plans create 12 snapshot branches (`pre-2a-1` … `pre-p3-4`) and about 10 rehearsal branches.
- The plan's branch and storage limits were not checked.
- Delete each rehearsal branch at its plan's clean-up step (with the owner's consent, as the plans say). Before Stage B, the owner checks the Neon plan limits. A plan upgrade would be SPEND; the amount was not researched.

**K16. Deploy hazard.**
- `main` holds code that needs every migration through 0012 and an active `eb_v1` model.
- Before anything else, the owner confirms auto-deploy is off on every Railway service (OWNER-OPS).
- DEPLOYMENT.md's pre-merge gate for PR 3-4 is moot (it already merged). The equivalent rule now: no Railway deploy of any service until Stage F has promoted a model and Stage G's rehearsal has passed.

**K17. Timeline: the January blackout and the 35-day staleness gate.**
- After 1 January, exposure builds must wait until that January's ERA5 window completes (exposure plan Task 10, January blackout).
- Training refuses an exposure snapshot older than 35 days (F21).
- If Stages E or F slip past 2027-01-01, they wait for the January 2027 window. That window is another Professional month (SPEND, as in C3).

**K18. Golden sets.**
- `data/golden/activity_v1.csv` and `experience_v1.csv` must be labelled by the owner during Stage B. The rehearsal exports candidates, and the owner copies ids and labels into the files.
- They are committed files, and `VERIFY` reads them from the checkout it runs in. Merging them takes a PR, then K5 again.

**K19. Paid-window order (correction to the suggested shape).**
- `era5_window register` (2a-3) registers every `mp_routes` point, falling back to its location's point. `static_features points` (Stage D2) registers each catalog area's point as a new bucket, with normals `pending`.
- Phase 3 expects route-bucket normals filled in a Professional window.
- If the paid window runs before D2, buckets that only `points` adds stay `pending` until January 2027.
- This runbook therefore runs C3, the paid window, after D2. That matches DEPLOYMENT.md's rule for the yearly cycle ("so the window buys normals for the buckets the year's new routes added"). Plan 2a-3 Task 14 says "once Tasks 12 and 13 are on prod", and either order satisfies it.
- **OWNER-REVIEW decision:** confirm C3 after D2.

**K20. Stale note in plan 2a-3 Task 12.** Step 1 says `grants_phase2.sql` grants SELECT on `weather` (Task 8). On `main` it revokes it. This matters only for K6.

## 3. Stages in dependency order

| Stage | What | Migration | Snapshot | Who |
|---|---|---|---|---|
| 0 | Prep: K1/K2/K3 fixes, K5 checkout, deploy check, GNIS download | none | none | AGENT, OWNER-REVIEW, OWNER-OPS |
| A | Rest of 2a-1: R2/R1, R9, r1-v2 on prod | none | `pre-2a-1` | OWNER-GO, then AGENT |
| B | 2a-2: GNIS, R3a/R3, R4, R5, R12, clean views | 0005–0006 | `pre-2a-2` | AGENT, OWNER-REVIEW (many), OWNER-GO |
| C1 | 2a-3: conditions tables, register, plan, R7 | 0007 | `pre-2a-3` | AGENT, OWNER-REVIEW, OWNER-GO |
| C2 | 2a-4: R11 accident refresh | none | `pre-2a-4` | OWNER (CSV prep), AGENT, OWNER-REVIEW, OWNER-GO |
| D1 | MP catalog: `ltree`, refresh | 0008–0010 | `pre-mpc-1` | OWNER-SQL (`ltree`), AGENT, OWNER-REVIEW, OWNER-GO |
| D2 | Static features: points, elevation, EPQS gate, routes | none | `pre-mpc-2` | AGENT, OWNER-REVIEW, OWNER-GO |
| C3 | ERA5 Professional window: fill, normals; then R6 evidence | none | `pre-era5-w1` | SPEND, OWNER-OPS, AGENT, OWNER-SQL (K6) |
| D3 | R10 rule pass, optional review, LLM pass | none | `pre-mpc-3` | AGENT, OWNER-REVIEW, SPEND |
| E1 | Rock tick loads | none | `pre-xp-1` | OWNER (export), AGENT, OWNER-REVIEW |
| E2 | Exposure index, first snapshot, removal drill | 0011 | `pre-xp-2` | AGENT, OWNER-REVIEW, OWNER-GO |
| F | Phase 3: schema `model`, trainer login, registry; backtest snapshots; first training | 0012 | `pre-p3-0`, `pre-p3-3` | OWNER-SQL, AGENT, OWNER-REVIEW, OWNER-OPS |
| G | Deploy MVP-1, then drop `weather`; Railway relaunch | 0013 | `pre-p3-4` | OWNER-OPS, AGENT, OWNER-GO |

Dependencies:
- A comes before B: B's rehearsal must see 2a-1 applied, and R5 reads R1's dates.
- B before C1: 2a-3's gate requires 0006.
- C1 before C2: 2a-4 requires 2a-3's `verified_2a3` marker.
- C2 before D1: the catalog gate requires 0007.
- D1 before D2.
- D2 before C3 (K19).
- B and D1 before D3: R10 needs R3/R5 and the catalog.
- D1 before E1: the rock loader writes the 0008 tick seam.
- C3 and E1 before E2: exposure reads ERA5 conditions and the seam.
- D3 and E2 before F: training reads the R10 links, `scoring_unit_features` and an exposure snapshot.
- F before G.
- D3 and E1 may run in either order, but never concurrently with another job.

### Stage 0: Prep

Prerequisites: none.

Steps:
1. **OWNER-OPS.** Confirm auto-deploy is off on every Railway service: `backend`, `celery-worker`, `beat` if it exists, and `frontend` (K16). Record it.
2. **OWNER-REVIEW.** Decide K1 (role scripts), K6 (the R6 path), K19 (window order) and K15 (Neon limits). Also say whether the 2026-11-15 approval counts as the per-stage OWNER-GO, or whether each stage gets its own.
3. **AGENT.** If K1(a) is chosen: on a branch off `main`, write the revision-aware role scripts and tests. Also fix the plan text for K2, K3 and K4 in the same or a separate docs PR. Open the PR(s); the owner reviews and merges them (pushing and merging are the owner's call).
4. **AGENT.** Do K5: advance the main checkout to `origin/main` after the merge. Then run plan 2a-1 Task 12 Step 0: `uv sync --frozen`, psql 16+, neonctl resolves.
5. **AGENT.** Download the public GNIS file. It is open USGS data, not a scrape. The command is plan 2a-2 Task 13 Step 2's first two lines, from `backend/`:

```bash
GNIS_ZIP=../data/review/gnis_national.zip
[ -e "$GNIS_ZIP" ] || curl -fL -o "$GNIS_ZIP" https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames/DomesticNames_National_Text.zip
save_once ../data/review/gnis_national.sha256 shasum -a 256 "$GNIS_ZIP"
```

Gates: the PR's CI is green. `. scripts/runbook_helpers.sh && type same_ids gate_pass gate_require bg_start bg_wait save_once branch_host` succeeds.

Runtime: one to two hours of agent time for the PR; minutes for the rest.

Rollback: none (no database write).

### Stage A: the rest of 2a-1 on prod (R2/R1, R9, r1-v2)

Prerequisites:
- Stage 0, and the K1 decision.
- The rehearsal is complete: the evidence in 1.5 and the owner's 2026-10-21 sign-off.
- The owner's filled `r1_year_check.csv`, `severity_check.csv` and `r1v2_year_check.csv` are present (they are).

Rehearsal: already done on `p2a-1-rehearsal` (plan 2a-1 Task 12 Steps 1–5e). Do not repeat it. Under K12, the owner may ask for a re-baseline of the r9 and r2r1 dry runs on a fresh branch first.

Prod steps:
1. **OWNER-GO.** "Go ahead with prod" for 2a-1.
2. **AGENT: 6a, corrected for K3.** If K1(a) has merged, `grants_phase2.sql` and `verify_roles_phase2.sql` are the repo files. If the owner chose K1(b), the block first writes the `6cdf317` versions to a scratch file outside the repo and passes those paths to `OWNER_PSQL -q -f` instead. Use the plan's 6a block with the revision test replaced:

```bash
( cd <repo-root>/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_2a1_prod || exit 1
  # PRODUCTION
  for f in same_ids gate_pass gate_require bg_start bg_wait; do type "$f" >/dev/null 2>&1 || { echo "runbook helpers predate the Task 12 gates: STOP" >&2; exit 1; }; done
  [ ! -e "$GATE_DIR" ] || { echo "$GATE_DIR exists: an earlier prod attempt; STOP, ask the owner" >&2; exit 1; }
  cur="$(MIG current)" || exit 1
  printf '%s\n' "$cur"
  case "$cur" in 0004_phase2a_foundation | "0004_phase2a_foundation (head)") ;; *) echo "prod is not at 0004_phase2a_foundation: STOP" >&2; exit 1 ;; esac
  gate_pass prod_at_0004 )
```

   Then run the plan's 6a grants block as written. It runs `grants_phase2.sql` and `verify_roles_phase2.sql`, and needs K1 resolved.
3. **AGENT.** Steps 6b/6c (dry runs equal the branch's), 6d (snapshot `pre-2a-1`), 6e (r2r1 twice, then r9, all in the background), 6f/6g (sample ids match; import the owner's filled CSVs), 6i–6k (r1-v2 dry run, apply twice, sample, import) and 6l (clean-up), exactly as plan 2a-1 Task 12 Step 6 writes them.
   - If 6f or 6k reports a sample mismatch, the owner hand-checks the `_prod` files (**OWNER-REVIEW**: `r1_year_check_prod.csv`, `severity_check_prod.csv` or `r1v2_year_check_prod.csv`; the decision is the per-row `within_one_year` or `agree`).

Gates (in order): `prod_at_0004`, `grants`, `dry_runs`, `snapshot`, `r2r1_applied`, `r9_applied`, `sample_ids`, `imported`, `r1v2_dry`, `r1v2_applied`, `r1v2_sample_ids`, `r1v2_imported`. Then the background `VERIFY … -k "test_r1_ or test_r1v2_ or test_r2_ or test_r9_ or test_every_row_"` (the old `r1 or ...` substring filter also selected the r11 and r12 tests, which fail before their stages) passes (`logs/verify_prod.log`).

Runtime: the r2r1 and r9 applies are estimated at 10–25 minutes each (not measured). The r1-v2 applies took 3 minutes and 40 s on the branch. With the gates, about 1.5–2 hours of agent time, plus any owner hand-check.

Rollback: `pre-2a-1`. A failed or killed apply wrote nothing (one transaction each). Wrong data: the owner decides between a restore from `pre-2a-1` and a reviewed fix (plan 2a-1 Task 12 "Rollback"). A restore discards every later write, including grants.

### Stage B: 2a-2 (0005–0006, GNIS, R3a/R3, R4, R5, R12, clean views)

Prerequisites:
- Stage A on prod (`gates_2a1_prod/r1v2_imported.ok`).
- K1, K2, K3 and K4 resolved.
- The GNIS zip and its sha256 from Stage 0.

Rehearsal (plan 2a-2 Task 13 Steps 0–11; branch `p2a-2-rehearsal`). Agent steps:
- Step 1: create the branch from production. Then `MIG upgrade 0006_accidents_clean` (K2, not `head`), the grants, and the verify scripts. Skip `MIG check` (K4).
- Step 2: load GNIS with `bg_start`. The quarantine gate is 1%.
- Steps 4, 9a and 10: the dry runs and applies, as written.

Owner reviews, each a hard STOP:
- Step 3: the `foreign` and `unmapped` state lists. The owner confirms each value, or asks for a mapping. R3 decides foreign from the state field only; mountain text is never used.
- Step 4: the fallback cluster list. Each point should be a city or park centroid, never a crag. Two exemptions run first (rule r3-v3, 2026-11-16). Candidate points within about 250 m (from the 3-decimal rounded point) of a GNIS summit are excluded; then a point is excluded as an MP crag when an MP area within 1 km, holding at most 400 routes, shares a distinctive name key (in at most 25 MP area names) with at least 60% of the point's rows and at least 3 of them. The owner reviews both lists: `summit_excluded` (point, rows, nearest GNIS id, distance), so a centroid near a small hill is not quietly trusted, and `crag_excluded` (point, rows, MP area id and name, distance, `matched_key`, share of rows), so a town or park point next to a crag is not either; spot-check the Eldorado Canyon points, the least certain on the rehearsal. The rehearsal analysis expects 17 crag points (157 rows). R3 reads `mp_locations (name, parent_id)` through block 0005 of `grants_phase2.sql`, so the Step 1 grants run must be from a `main` that has it; R3 refuses if no MP area has coordinates and 1-400 routes (e.g. `mp_locations` or `mp_routes` empty). Then (rule r3-v4, 2026-11-16) on each remaining fallback point, rows whose state is a US state other than the point's (the nearest GNIS summit's within 25 km) lose their coordinates (`unknown`, `fallback_state_contradiction`), route-linked (`mp_route_id`) and avalanche rows included, since the rule is about the point; this outranks R3a's kept-point labels, while an R3a move to a summit in the row's own state still moves. `rows_dropped` counts every raw row on the point, so after Steps 5b/6b it can exceed what apply nulls (apply skips owner-decided and labelled-elsewhere rows). The owner reviews `state_contradictions` (point, `point_state`, `rows_dropped`, row state counts). The rehearsal expects 3 points (59 rows: 37.847,-121.927 CA with 34 AK and 1 OR; 47.664,-122.111 WA with 14 AK; 45.425,-110.953 MT with 10 CA) and `state_undetermined_points` 0.
- Step 5b: `data/review/r3a_moves.csv`. Fill `correct`.
- Step 6b: `data/review/r3_ambiguous.csv`. Fill `gnis_id` or `none`.
- Step 7b: `data/review/r3_spot_check.csv` with `r3_spot_check.geojson`, opened in local QGIS only. Fill `plausible`.
- Step 8b: `data/review/r4_activity_review.csv`, filling `activity_class`. Also label `activity_golden_candidates.csv` into `data/golden/activity_v1.csv` (K18).
- Step 9b: `data/review/duplicates.csv`, filling `decision`, plus any later rounds. Step 9d: spot-check 30 auto merges.
- Step 10: `experience_golden_candidates.csv` into `data/golden/experience_v1.csv` (K18).
- Step 11: sign off the GNIS counts, the review sizes, the `outside_us` count and the audit re-run.

Step 11's full `VERIFY tests/verify` runs at 0006 against `main`'s cells. Cells for later stages (catalog, features, exposure, model scores, conditions) can be expected to fail or error there. This was not run in this inspection. Narrow it to `VERIFY tests/verify/test_phase2a_repair.py tests/verify/test_phase2a_foundation.py -q -s`, and list any other failure for the owner.

Prod (Task 13 Step 12, after the OWNER-GO):
- 12a: gate on 0004. The plan's block already accepts the bare id.
- 12b: snapshot `pre-2a-2`.
- 12c: corrected per K2, K3 and K4. Use `MIG upgrade 0006_accidents_clean`, the K3 idiom for `0006_accidents_clean`, no `MIG check`, then the grants and verify scripts.
- 12d–12j as written. The prod imports use the owner's branch-filled files after the `same_ids` gates.
- Set `R5_ROUNDS` to the number of rounds the branch ran.

Gates: `prod_at_0004`, `snapshot`, `migrated`, `gnis_file`, `r3_dry`, `r3a_ids`, `r3a_imported`, `r3_review_ids`, `r3_review_imported`, `r3_plot_ids`, `r3_done`, `r4_dry`, `r4_review_ids`, `r4_done`, `r5_dry`, `r5_round1_pairs`, `r5_done`, `r12_dry`. Then the verify log `finished ok` (`gates_2a2_prod/`).

Runtime:
- Agent time: several hours each on the branch and on prod. The R3/R4/R5/R12 applies and the GNIS load each run longer than 10 minutes (plan I5), unmeasured.
- Owner review: the largest block of owner time in the rollout. The review sizes are not measured until the rehearsal runs.

Rollback: `pre-2a-2` (plan 2a-2 Task 13 "Rollback").

### Stage C1: 2a-3 (0007, register, plan, R7; no purchase)

Prerequisites: Stage B on prod; K1–K4 resolved.

Rehearsal (plan 2a-3 Task 12 Steps 0–5; branch `p2a-3-rehearsal`):
- Step 0: skip the checkout (K5). Keep the tool checks and the direct-host check.
- Step 1: `MIG upgrade 0007_cell_daily_conditions` (K2), no `MIG check` (K4).
- Steps 2–4: register, plan, then the R7 dry run, apply and re-run.

**OWNER-REVIEW, Step 3:** the plan numbers `total_units`, `fill_units`, `normals_units`, `incident_series` and `route_series`, and the time zone query result. The owner writes "plan ok", or picks the OQ4 split if `total_units` is over 4,500,000. Register covers all 168,055 MP route points, so the route share may be large. **Step 5:** sign-off.

Prod (Step 6, after the OWNER-GO):
- 6a as written: it accepts the bare `0006_accidents_clean`.
- 6b: snapshot `pre-2a-3`.
- 6c: corrected. Use `MIG upgrade 0007_cell_daily_conditions`, the K3 idiom, no `MIG check`, then the grants and both verify scripts.
- 6d–6h as written: register, plan, R7, acceptance.

Gates: `prod_at_0006`, `snapshot`, `migrated`, `registered`, `plan_ok`, `r7_dry`, `r7_applied`, `verified_2a3` (`gates_2a3_prod/`).

Runtime: register is one transaction, unmeasured. The rest is minutes. Allow about 2 hours of agent time across branch and prod.

Rollback: `pre-2a-3`.

### Stage C2: 2a-4 (R11 facts-only accident refresh)

Prerequisites:
- `gates_2a3_prod/verified_2a3.ok`.
- **The owner prepares the facts CSVs (plan 2a-4 Task 13 Step 1):** `aac_refresh.csv`, typed by hand from ANAC 2025/2026 and The Prescription (facts only, about 60–120 rows), and `caic_refresh.csv` and `nps_refresh.csv` from the private exporters. They go in `~/Developer/safeascent-private/manual/`, which does not exist yet.

Rehearsal (Task 13 Steps 2–9; branch `p2a-4-rehearsal`):
- **AGENT:** the dry runs until clean (Step 2, with no `MIG check`, per K4), the loads, the G20 check, the R5 re-dedupe, the register and the acceptance cells.
- **OWNER-REVIEW:**
  - fix the quarantined rows in the private CSVs (Step 2), then freeze them;
  - fill `mp_route_id` in the `*_routes.csv` copies from `data/review/r11_route_matches_branch.csv` (Step 5);
  - fill `duplicates_r11.csv` if R5 finds pairs (Step 6);
  - sign off (Step 9).

Prod (Step 10, after the OWNER-GO):
- 10a: replace the `"0007_cell_daily_conditions (head)"` test with the K3 idiom.
- 10b: snapshot `pre-2a-4`.
- 10c–10i as written.
- Keep `p2a-4-rehearsal`: C3's branch slice runs there.

Gates: `prod_ready`, `snapshot`, `grants`, `dry_runs`, `loaded`, `load_counts`, `g20_routes_dry`, `routes_applied`, `r5_dry`, `r5_pairs`, `r5_done`, `registered`, `verified_2a4` (`gates_2a4_prod/`).

Runtime: dominated by the owner's CSV preparation. The loads are minutes each.

Rollback: `pre-2a-4`.

### Stage D1: MP catalog (0008–0010, `ltree`, first refresh)

Prerequisites: prod at 0007 (C1 and C2 done); K1 and K4 resolved.

Rehearsal (mp-catalog plan Task 6 Steps 0–3; branch `mpc-1-rehearsal`):
- **OWNER-REVIEW, Step 0:** confirm in writing that the private MP scrape upserts `mp_routes`/`mp_locations` and never deletes or truncates them.
- **AGENT, Step 1:**
  - The plan writes `OWNER_PSQL -q -f db/owner/extensions_mp_catalog.sql`. It creates `ltree` and is idempotent. On the branch an agent may run it under the plan's owner/agent label; on prod it is the owner's step (OWNER-SQL).
  - Then `MIG upgrade 0010_accident_route_links`, then the grants and verify scripts. Skip `MIG check` (K4).
- **AGENT, Step 2:** the refresh dry run, the refresh, the idempotent re-run, then the `VACUUM ANALYZE` as `migrator`.
- **OWNER-REVIEW, Step 3:** the dry-run JSON, the unknown share, the inherited/none coordinate counts and the per-state counts (counts only). The decision is "catalog ok".

Prod (Step 4, after the OWNER-GO):
- 4a and 4a-snap (`pre-mpc-1`) as written.
- 4b without `MIG check` (K4).
- 4c and 4d as written, then `VERIFY tests/verify/test_mp_catalog.py`.
- The plan's "the code deploys after 4b" is superseded by K16: no deploy until Stage G.

Gates: `c1_migrate`, `snapshot`, `c1_migrated`, `c1_refresh` (`gates_mpc1_prod/`).

Runtime: the refresh is one transaction over 168,055 routes, unmeasured. About 1–2 hours of agent time across branch and prod.

Rollback: `pre-mpc-1`. 0008's downgrade refuses while the catalog holds rows.

### Stage D2: static features (points, 3DEP elevation, EPQS gate, route table)

Prerequisites: prod at 0010 with a refreshed catalog. This stage is free: 3DEP and EPQS are public services.

Rehearsal (mp-catalog plan Task 13 Steps 0–4; branch `mpc-2-rehearsal`):
- **AGENT:** check that `h3` and `rasterio` import, then run the refresh, `points`, `elevation --limit 5000` until `"sampled": 0, "retry": 0`, `epqs-check` (about 15 minutes) and `routes`.
- **OWNER-REVIEW, Step 4:** the `dem_source` counts, the EPQS class table and the normals line. The decision is go or no-go.

Prod (Step 5 blocks 5a–5f, after the OWNER-GO): snapshot `pre-mpc-2`, then the gates `c2_ready`, `snapshot`, `c2_points`, `c2_epqs` and the routes rebuild.

Step 6, creating the `catalog-cycle` Railway cron service (OWNER-OPS), is deferred to Stage G. It must not be deployed before then (K16).

Runtime: EPQS about 15 minutes per run. Elevation over tens of thousands of points: the catalog-cycle estimate is 30–60 minutes for a yearly increment and about 5 hours worst case (plan Task 12 cost note). Allow half a day.

Rollback: `pre-mpc-2`.

### Stage C3: the first ERA5 Professional window, then R6 evidence

Prerequisites:
- D2 on prod (K19).
- `gates_2a3_prod/verified_2a3.ok` and `gates_2a4_prod/verified_2a4.ok`.
- The K6 decision.

Steps (plan 2a-3 Task 14):
1. **SPEND, OWNER-OPS (Step 1).**
   - Buy one month of Open-Meteo Professional: $99/month for 5,000,000 units (price read 2026-09-28; re-check on open-meteo.com/en/pricing before buying).
   - Put the key in `backend/.env.openmeteo` (0600) with an editor.
   - Record the purchase with the plan's owner block (`owner_purchase_confirmed`), with the window start date.
   - The run's cap keeps the total at or below 4,500,000 units.
2. **AGENT (Step 2).** The key and window checks. They make no network call.
3. **AGENT, then OWNER-REVIEW (Step 3)** on `p2a-4-rehearsal`:
   - the plan;
   - one paid slice capped at 20,000 units;
   - then the owner compares the Open-Meteo dashboard with `era5w1_branch_spent.txt`;
   - then the query plan, which must show an index scan on the conditions primary key.
   - Replace the plan's `INGMOD app.pipelines.r6_verify` line with the K6 path, or postpone R6 to after step 4.
4. **OWNER-GO, then AGENT (Step 4).**
   - 4a (with the K3 idiom for `0007_cell_daily_conditions`; prod is at 0010 by now). Change this gate deliberately to accept `0010_accident_route_links`: the plan assumed the window runs before D1. That is a K19 consequence; the owner confirms the change.
   - 4b: snapshot `pre-era5-w1`.
   - 4c: the plan gate. The owner writes "plan ok".
   - 4d: slice 1, capped at 1,000,000 units. The owner checks the dashboard.
   - 4e: slice 2, capped at 2,500,000.
   - 4f: the final slice, capped at 4,500,000 minus the branch spend.
   - 4g: verify. It no longer has an R6 cell.
5. **K6 R6 evidence.** OWNER-SQL: the temporary grant. AGENT: the scratch-checkout run, then `grants_phase2.sql`, then `verify_roles_phase2.sql`.
6. **SPEND decision (Step 5): downgrade to Standard ($29/month, read 2026-09-28) or cancel.** The plan's reason for Standard is the nightly forecast. That nightly is gone, but `weather_service.py` and `mp_routes.py` still make live calls (DEPLOYMENT.md relaunch caveat). The owner decides whether the relaunched app needs a key at all.

Gates: `owner_purchase_confirmed`, `owner_units_confirmed_branch`, `owner_units_confirmed_prod` and the plan's 4a/4c markers (`gates_era5w1_prod/`). A run is done when the last run's JSON shows `fill.chunks == fill.pending_lag_edge + fill.edge_trimmed` and `normals.due: 0`.

Runtime: multi-hour slices. `bg_wait` returns 2 many times; call it again each time. Expect one to two days of wall time inside the paid month.

Rollback: `pre-era5-w1`. The paid rows are additive; units already bought are not refunded.

### Stage D3: R10 accident route links (rule pass, optional review, LLM pass)

Prerequisites: B and D1 on prod. Run it after C3, or between D2 and C3, but never during a window run. The LLM pass runs on prod only (plan ruling 2026-10-15).

Rehearsal (mp-catalog plan Task 16 Steps 1–3-plan; branch `mpc-3-rehearsal`): **AGENT.** The dry run, the apply, the idempotent re-run, and the free `INGLLM app.data.repair.r10_llm plan` sizing.

Optional **OWNER-REVIEW, Step 3:** `data/review/r10_unresolved.csv` (`owner_decision`, `decided_kind`, `decided_id`). The decision is whether the owner wants the residue linked by hand.

Prod rule pass (Step 4, after the OWNER-GO): 4a, 4b (snapshot `pre-mpc-3`), 4c (dry run within 1% of the branch), 4d (apply), and 4e if the owner reviewed.

Prod LLM pass (Step 5, owner go; **SPEND**):
- Caps are cumulative per model and prompt version: **$12 Haiku and $22 Sonnet** (plan defaults). The worst case, if both models are sampled and one is run to its cap, is $34.
- The owner exports `ANTHROPIC_API_KEY` in the shell only, never in a file.
- **OWNER-REVIEW, 5a:**
  - the verify record `gates_mpc3_prod/r10_llm_verify.md`: prices against `r10_llm.PRICES_USD_PER_MTOK`, the pinned model ids, the thinking-plus-structured-output probe, and the org's data-retention setting (OWNER-OPS, read from the Anthropic console);
  - the owner adds the line `Owner sign-off:`.
- **AGENT:** 5b, the paid sample (150 per compared model).
- **OWNER-REVIEW, 5c:** fill `owner_answer` in `r10_llm_sample_<model>.csv`. Every row needs an offered id or `none`, and `none` is final.
- **AGENT:** `gate`, then 5d (`run`), then 5e (`apply`, then the review-band round trip).

Gates: `c3_ready`, `snapshot`, `c3_apply`, `llm_verify`, and the r10_llm gate rows (`gates_mpc3_prod/`).

Runtime: the rule pass takes minutes. The LLM run is unmeasured; it is bounded by the cap and by 2 attempts per accident.

Rollback: `pre-mpc-3`. Owner decisions are protected by the `r10_unresolved_keep_decisions` trigger.

### Stage E1: rock tick loads

Prerequisites: prod at 0010. Exposure plan Task 3.

Rehearsal (branch `xp-1-rehearsal`):
- **OWNER, Step 0:** confirm that `~/Developer/safeascent-private/exports/rock_20261022.sqlite` is the export to load, or produce a fresh one with the private `export_tick_aggregates.py --scope rock`. Give the agent the path as `EXPORT`.
- **AGENT:** the Step 0 checks (no WAL sidecar, outside the repo), then Step 2: the dry run, the load and the reload.
- **OWNER-REVIEW, Step 3:** the branch counts. The decision is "rock load ok".

Prod (Step 4, after the OWNER-GO): 4a (prod at 0010, ingest on the direct host), 4a-snap (`pre-xp-1`), 4b (the dry run matches the branch's) and 4c (the load).

Gates: `xp1_load`, `xp1_snapshot`, `xp1_apply` (`gates_xp1_prod/`).

Runtime: unmeasured. One transaction over the export's aggregate tables.

Rollback: `pre-xp-1`.

### Stage E2: 0011 exposure index, first snapshot, removal drill

Prerequisites:
- C3 complete (season share reads ERA5 conditions).
- E1 on prod.
- Not inside the January blackout (K17).

Rehearsal (exposure plan Task 10 Steps 0–3; branch `xp-2-rehearsal`):
- **AGENT, Steps 0–1:** `uv run alembic heads` prints `0013_drop_legacy_weather (head)` on `main`, not the plan's `0011_exposure_index (head)`; accept that. Then `MIG upgrade 0011_exposure_index`, then the grants. K1's `exposure_index` block now applies.
- **AGENT, Step 2:** the dry run, the removal drill (`EXPOSURE_ENABLE_MP_TICKS=false`) and the build. Paste the EXPLAIN; a Seq Scan on `cell_daily_conditions` is a finding for the owner.
- **OWNER-REVIEW, Step 3:** the coverage counts. The decision is "exposure ok".

Prod (Step 4, after the OWNER-GO):
- 4a: prod at exactly 0010.
- 4a-snap: snapshot `pre-xp-2`.
- 4b: migrate. Drop its `MIG check` if present (K4).
- 4c: the dry run, within catalog drift.
- 4d: the build.

Gates: `xp2_migrate`, `xp2_snapshot`, `xp2_migrated`, `xp2_build` (`gates_xp2_prod/`).

Runtime: unmeasured. The build is one REPEATABLE READ snapshot.

Rollback: `pre-xp-2`. The table is fully derived, so a wrong build can be dropped with `exposure drop`.

### Stage F: Phase 3 registry, trainer, and the first training run

Prerequisites: prod at 0011 with one production exposure snapshot; D3 applied; C3's normals filled.

**F1. PR 3-0 (DEPLOYMENT.md "Phase 3 roles" and "PR 3-0 runbook"; plan Task 4 Steps 6–7).**
- Rehearsal on a fresh branch `p3-0-rehearsal`:
  - **AGENT:** save `p3-0/heads.txt` (on `main` it reads `0013_drop_legacy_weather (head)`, not the documented `0012`; accept that) and `p3-0/current-before.txt` (expect `0011_exposure_index`).
  - **OWNER-SQL:** owner step 1, `schema_model_phase3.sql`. Skip owner step 2: `.env.trainer` already exists (K9). Owner step 3: `trainer_login_phase3.sql`, with `.env.trainer` sourced in the owner's own subshell.
  - **AGENT:** the K9 read-only check on prod. Then the migration-and-grants block as written in DEPLOYMENT.md (`MIG upgrade 0012_model_registry`, `grants_phase3.sql`, `verify_roles_phase3.sql`, `verify_roles_phase2.sql`, `gate_pass p3_0_branch`).
- Prod, after the OWNER-GO:
  - **AGENT:** snapshot `pre-p3-0` (the plan names the owner; the agent may do it under the CLI authorization, as for every other snapshot).
  - **OWNER-SQL:** owner step 1 on prod, and owner step 3 only if K9 printed `f`.
  - **AGENT:** the prod block in DEPLOYMENT.md, ending in `gate_pass p3_0_prod`.
  - **OWNER-OPS:** create the healthchecks.io check `train` (period 35 days, grace 3 days) and put its URL into `.env.trainer` as `HEALTHCHECKS_TRAIN_URL`, with an editor.

**F2. Backtest exposure snapshots (Task 21 Step 1).**
- **AGENT:** one gated rebuild block per cutoff: 2004-01-01, 2008-01-01, 2012-01-01, 2016-01-01 and 2026-02-05. Use the exposure plan's Task 10 Step 5 pattern with `--cutoff`; rehearse on the branch first.
- Then `save_once ../data/review/p3-3/snapshots.txt ANALYST_PSQL -c "SELECT data_cutoff, as_of, count(*) FROM internal.exposure_index GROUP BY 1, 2 ORDER BY 1"`. Expect one row per cutoff plus the production snapshot.

**F3. First candidate (Task 21 Steps 2–4).**
- **OWNER-REVIEW (consent):** delete the stale `p3-3-rehearsal` (K11).
- **AGENT:** recreate it from production. Then, in order:
  - `save_once ../data/review/p3-3/exposure.txt INGMOD app.pipelines.exposure build`;
  - `save_once ../data/review/p3-3/kernel.txt TRAIN kernel-baseline`;
  - `save_once ../data/review/p3-3/run1.txt TRAIN run --benchmark`, then `save_once ../data/review/p3-3/run1.json cat out/status.json`.
- **AGENT:** the Step 2.4 checks:
  - the registry holds `kernel` retired, plus an `eb-…` version active or rejected;
  - `grep -E '[0-9]{9}' out/issue.md` prints nothing;
  - `metrics.capacity` is within budget;
  - `scores_rows` = 12 × the scorable non-unknown routes.
  - Then `gate_pass p3_3_branch`.
  - If the gate rejects, STOP. **OWNER-REVIEW:** `out/issue.md`, and decide.
- Prod:
  - **OWNER-GO.**
  - **AGENT:** snapshot `pre-p3-3`.
  - **OWNER-OPS:** `gh label create model-promotion`, `model-rejected` and `model-capacity`.
  - **AGENT, on the owner's Mac only (F48):** `gate_require p3_3_branch`, then the exposure build, `TRAIN kernel-baseline` and `TRAIN run --benchmark`, each under `save_once ../data/review/p3-3/prod-*`. Then `p3-3/prod-registry.txt`.
  - **OWNER-OPS:** `gh issue create … --body-file out/issue.md` (aggregate only, never raw output).
- Rollback drill (Task 21 Step 4, OWNER on the branch): `TRAIN rollback` on a second candidate.

Gates: `p3_0_branch`, `p3_0_prod` and `p3_3_branch` (default `../data/review/gates/`, K10). An active `eb_v1` version on prod is the prerequisite for G.

Runtime: unmeasured. The `--benchmark` run reports `metrics.capacity`. Allow half a day per training run until it is measured.

Rollback:
- 0012 refuses its downgrade while rows exist. Restore from `pre-p3-0`.
- A bad promotion: `TRAIN rollback` (plan Task 20/21), or `pre-p3-3`.

### Stage G: deploy MVP-1, then 0013, then the Railway relaunch

Prerequisites:
- Prod at 0012 with an active `eb_v1` (`SELECT version, family FROM model.registry WHERE status = 'active'`).
- The R6 evidence from K6, unless `weather` is empty.
- K13 understood.

**G1. Rehearsal (plan Phase 3 Task 26 Step 1; branch `p3-4-rehearsal`).**
- **AGENT:** save `p3-4/weather-nonempty.txt`, `p3-4/r6.txt` (the `QUALIFYING_R6` query; at least one row is required, since `weather` is non-empty) and `p3-4/active.txt`.
- **AGENT:** run a local API and worker from `main` against the branch at 0012: the 3-route parity `curl`, and one `scores_heartbeat.run()`.
- **AGENT:** `MIG upgrade 0013_drop_legacy_weather`, then `grants_phase2.sql`, `verify_roles_phase2.sql`, `grants_phase3.sql` and `verify_roles_phase3.sql`.
- **AGENT:** now `MIG check` (K4: first meaningful here; expect "No new upgrade operations detected").
- **AGENT:** `VERIFY tests/verify/test_model_scores.py` into `p3-4/verify-branch.txt`, the spot check again, then `gate_pass p3_4_branch`.

**G2. Prod deploy, 0013 not yet run (Task 26 Step 2).**
- **OWNER-GO.**
- **AGENT:** snapshot `pre-p3-4` (keep it 30 days, DP21).
- **AGENT:** `gate_require p3_4_branch`, then the grants and verify scripts on prod.
- **OWNER-OPS:** deploy by hand, with auto-deploy still off.
  - Prerequisites from DEPLOYMENT.md: the `beat` service exists (owner creates it; DEPLOYMENT.md says "the worker change and the beat service ship together"), and `SKIP_WEATHER_STATISTICS` is unset or false on the worker.
  - Phase 1b Task 26 Step 1 first: set `DATABASE_URL` on `backend`, `celery-worker` and `beat` to the `app` role URL.
  - Deploy order: **the frontend first** (with `MAINTENANCE_MODE` still true if preferred), then `backend`, `celery-worker` and `beat`.
- **AGENT:** confirm the beat schedule holds `scores-heartbeat`, the first heartbeat pings success, and save `p3-4/prod-spot.txt`. Then `gate_pass p3_4_deployed`.

**G3. 0013 on prod (Task 26 Step 2b).**
- **OWNER-GO.** Only after `p3_4_deployed` and at least one successful heartbeat.
- **AGENT:** `gate_require p3_4_deployed`, `MIG upgrade 0013_drop_legacy_weather`, the two verify scripts, `p3-4/verify-prod.txt`, then `gate_pass p3_4_0013_prod`.

**G4. Relaunch (Phase 1b Task 26, corrected by K13; plan Phase 3 Task 26 Step 3; mp-catalog Task 13 Step 6). All OWNER-OPS unless marked.**
- Healthchecks:
  - add check `scores` (period 1 day, grace 2 h) as `HEALTHCHECKS_SCORES_URL` on the worker;
  - keep `HEALTHCHECKS_NIGHTLY_URL` and `HEALTHCHECKS_BEAT_URL` set.
- Redis: `redis-cli --scan --pattern 'safety:*' | xargs redis-cli del` against the Railway Redis, from the owner's machine.
- `catalog-cycle`: create the cron service (6a), set its variables via stdin with `--skip-deploys` (6b), run the `INGEST_DATABASE_URL`-isolation check (6c), deploy it by hand (6d), and run it once on demand (6e).
- Phase 1b Task 26 Step 2, rotating the `neondb_owner` password: probably already done. `.env.owner.pre-rotation-2026-10-21` exists, and PR #16 says "rotated 2026-10-21". The owner confirms.
- Step 3, `REVOKE migrator FROM CURRENT_USER`: last of all, after every grants run (K13). Re-grant it before any future migration.
- Step 4: re-enable auto-deploy only once "Wait for CI" is on (DEPLOYMENT.md: not yet enabled). Enabling `main` branch protection is recommended at the same time.
- Step 5, replaced: one clean scores heartbeat, and `/health/worker` 200 with `"expired_tasks_7d": {}`.
- Step 6: `MAINTENANCE_MODE=false` on `frontend`, redeploy, and check.
- **AGENT:** Phase 3 Task 26 Step 4 (the CHANGELOG relaunch note) and Step 5 (renumber the Phase 4 plan's migrations to 0014–0016), as a docs PR.

**SPEND.** Railway runtime for `backend`, `celery-worker`, `beat`, `frontend` and Redis. The amount is not in the repo docs; the owner checks the Railway plan. `catalog-cycle` is estimated under $1 a year (DEPLOYMENT.md, 2026-10-13, not re-checked).

Runtime: a day of mixed owner and agent time. The heartbeat runs at 02:00 UTC, so G3 waits up to a day after G2.

Rollback:
- Before 0013: redeploy the previous services, or set `MAINTENANCE_MODE=true`.
- After 0013: `pre-p3-4`. 0013 has no downgrade.

## 4. What can run without the owner, and what blocks on the owner

### Can run without the owner (AGENT)

- Stage 0:
  - the defect-fix PR(s) for K1(a) and K2–K4 (the owner still reviews and merges);
  - the K5 checkout once merged;
  - the tool checks;
  - the GNIS download and its sha256.
- Read-only inspection with `ANALYST_PSQL` against prod or any branch, at any time.
- Every rehearsal-branch step marked AGENT, once its prerequisites are on prod: creating the branch, migrating to the pinned revision, the grants (after K1), dry runs, applies on the branch, exports, `VERIFY`, and the `plan` commands that make no API call (`era5_window plan`, `r10_llm plan`).
- The snapshot branches (`pre-…`), and every prod block after an OWNER-GO, as long as no OWNER-SQL, SPEND or OWNER-REVIEW item sits inside it.
- Evidence bookkeeping: `save_once` files, gate markers, and the PR text with the counts.

### Blocks on the owner

- **Decisions:** K1 (role scripts), K6 (the R6 path), K19 (window order), K15 (Neon limits), K12 (re-baseline, if drift appears), and whether the 2026-11-15 approval counts as the per-stage OWNER-GO.
- **OWNER-OPS:**
  - Railway: confirm auto-deploy is off now, create `beat` and `catalog-cycle`, set variables, deploy, Redis, maintenance mode;
  - healthchecks.io: the checks `train` and `scores`;
  - `gh label create` and `gh issue create`;
  - Anthropic console: data retention;
  - Neon: the org id, if a prompt appears (K8).
- **OWNER-SQL:**
  - `extensions_mp_catalog.sql` on prod (D1);
  - `schema_model_phase3.sql` and `trainer_login_phase3.sql` (F1);
  - the temporary `weather` grant for R6 (K6);
  - the final `REVOKE migrator FROM CURRENT_USER` (G4).
- **OWNER-REVIEW files:**

| Stage | Files and decisions |
|---|---|
| A | Only if the samples mismatch: `*_check_prod.csv` |
| B | The state lists; the cluster list; `r3a_moves.csv`; `r3_ambiguous.csv`; `r3_spot_check.csv` with its GeoJSON; `r4_activity_review.csv`; the golden labels in `data/golden/activity_v1.csv` and `experience_v1.csv`; `duplicates.csv` and later rounds; the 30 auto-merge spot check; the sign-off |
| C1 | "Plan ok" |
| C2 | The R11 facts CSVs (prepared by the owner); the `*_routes.csv` copies; `duplicates_r11.csv`; the sign-off |
| D1 | The loader confirmation; "catalog ok" |
| D2 | The feature sign-off |
| C3 | The dashboard checks after each slice; "plan ok" |
| D3 | `r10_unresolved.csv` (optional); `r10_llm_verify.md` with its `Owner sign-off:` line; `r10_llm_sample_<model>.csv` `owner_answer` |
| E1 | Which export to load; "rock load ok" |
| E2 | "Exposure ok" |
| F3 | Consent to delete `p3-3-rehearsal`; `out/issue.md` if the gate rejects |

- **OWNER-GO:** the prod go-ahead for each stage, A through G3.
- **SPEND:**
  - Open-Meteo Professional, $99 for one month (5M units; the run stays at or below 4.5M). Read 2026-09-28; re-check before buying.
  - Optionally Standard afterwards, $29/month.
  - Anthropic R10 caps: $12 Haiku and $22 Sonnet, cumulative.
  - Railway runtime: amount unknown.
  - A possible Neon plan change (K15): amount unknown.
  - If K17 bites, a January 2027 Professional month.

### First stage an agent can start right now

Stage 0. The K1 decision blocks every grants step from Stage A on, so the fix PR comes first. In parallel, an agent can do the GNIS download and the read-only checks. If the owner picks K1(b) for Stage A and gives the go-ahead, the agent can start Stage A's corrected 6a at once, since the 2a-1 rehearsal and the sign-off are already done.

## Appendix: the read-only queries behind section 1

All were run from `backend/`, in a subshell, with `. scripts/runbook_helpers.sh && export TARGET_HOST=production` (or `TARGET_HOST="$(branch_host <branch>)"`), through `ANALYST_PSQL -X`:

```sql
SELECT version_num FROM alembic_version;
SELECT rule_version, field, count(*) FROM internal.accident_revisions GROUP BY 1, 2;
SELECT source, status, started_at::date, rows_in, rows_upserted, rows_quarantined FROM source_ingest_log ORDER BY started_at;
SELECT rolname, rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname !~ '^pg_';
SELECT r.rolname, m.rolname, a.admin_option, a.inherit_option, a.set_option
  FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.roleid JOIN pg_roles m ON m.oid = a.member;
SELECT has_table_privilege('ingest', 'public.weather', 'SELECT'),
       has_table_privilege('ingest', 'internal.accident_revisions', 'INSERT'),
       has_database_privilege('migrator', current_database(), 'CREATE');
SELECT extname, extversion FROM pg_extension;
SELECT min(prediction_date), max(prediction_date) FROM historical_predictions;
-- plus count(*) per table in section 1.1
```

## Owner decisions (2026-11-16)

- K1: (a). The role scripts become revision-aware in a reviewed PR before Stage A.
- K6: the temporary `SELECT ON public.weather TO ingest` path, as proposed.
- C3 / SPEND: the owner already pays for Open-Meteo, and the current API token is the paid one. No purchase step: Stage C3 uses that key, exported in the shell only and never in a file. The runbook's order stands (C3 after D2).
- OWNER-GO: the 2026-11-15 approval is a standing go-ahead for each prod stage after its branch rehearsal passes. Stops remain for OWNER-REVIEW files, OWNER-SQL, any new spend, and any failed gate.
