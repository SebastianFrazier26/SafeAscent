# Railway IaC migration runbook: Config as Code to `.railway/railway.ts` (2026-11-25)

**Status (2026-11-25, end of day): applied; the toml files are deleted, so the 12-01 cutoff no longer matters (section 4). The open item is the owner's builder proof deploy in section 4.**

Railway stops reading `railway.toml`/`railway.json` on **2026-12-01** (hard cutoff, docs.railway.com/infrastructure-as-code, read 2026-11-25). After that date, any setting that lives only in `backend/railway.toml`, `backend/railway-worker.toml` or `frontend/railway.toml` falls back to the dashboard value, and several dashboard values are wrong (section 1). This runbook moves the effective settings into `.railway/railway.ts` and applies them before the cutoff. The owner decided on the migration on 2026-11-25. **The owner runs every apply step; agents only plan.**

## 1. What the plan changes, and why

The saved plan is `docs/runbooks/2026-11-25-railway-iac-plan.txt` (`railway config plan --verbose`, production, 2026-11-25; variables redacted, none printed). Its baseline is `docs/runbooks/2026-11-25-railway-iac-baseline.ts`, the raw `railway config pull` output. Planned on its own, that file reads "already up to date", so it equals the dashboard.

`backend`, `celery-worker` and `frontend` have had **no running deployment since 2026-09-28T01:53Z** (all three `REMOVED` at once). So "no behaviour change" here means that their next deploy builds and runs exactly what the current `main` toml files would give it. Every line for those three services copies a value that the toml file already forces over the dashboard today, so their behaviour does not change:

| Plan line | Plain meaning |
|---|---|
| `frontend`/`celery-worker`/`backend` `build.builder (null → "DOCKERFILE")` | The dashboard builder is Railpack. The toml files force the Dockerfile. Without this line, the first deploy after 12-01 would try to build with Railpack. |
| `frontend` healthcheck `/health`, timeout 10, `ON_FAILURE` ×3 | Copies `frontend/railway.toml`. The dashboard has no healthcheck. |
| `celery-worker` restart `ON_FAILURE` ×10 | Copies `backend/railway-worker.toml`. |
| `celery-worker` `startCommand ("… worker --beat --loglevel=info" → "… worker --loglevel=info --concurrency=2 -E")` | The dashboard still holds the old command with **embedded beat**. The toml has overridden it since beat was split out (`a1ff4c8`). Without this line, after 12-01 the worker would run its own scheduler, and the separate `beat` service would double-schedule every task once it exists. |
| `backend` restart `ON_FAILURE` ×3 | Copies `backend/railway.toml`. The healthcheck `/health` (30 s) is already on the dashboard, so it shows no diff. |

The only behaviour change is `catalog-cycle`:

| Plan line | Plain meaning |
|---|---|
| `catalog-cycle build.dockerfilePath ("backend/Dockerfile.jobs" → "Dockerfile.jobs")` | The path is relative to the service root directory (`backend`), the same form as backend's `Dockerfile`, which builds `backend/Dockerfile`. The old value would point at `backend/backend/Dockerfile.jobs`. This is inferred from that working backend form; Railway's docs do not spell out the dashboard field. Section 4's check confirms it. |
| `- Delete variable catalog-cycle.RAILWAY_DOCKERFILE_PATH` (the one destructive line) | It held the same repo-relative path, which also resolves wrong, and it would compete with the field. The value was not a secret: `backend/Dockerfile.jobs`. |
| (no line) API healthcheck | `catalog-cycle` has root `backend`, so Railway auto-reads `backend/railway.toml`, the API's file. That is why it built `backend/Dockerfile` with `--no-group pipelines` and inherited `/health`. The dashboard holds no healthcheck for it, so there is nothing to delete. The inheritance stops when Railway stops reading that file (section 4). |

Not in the plan, and why:

- **Redis and Redis-mxVE** are declared unchanged, with their volumes. This is a whole-project file with no named partials (`railway config partials list`: "No named partial ownership"), so **any resource missing from the file is deleted on apply**. Never apply a version of the file that drops them.
- **`beat`** did not exist on Railway on 2026-11-25. `backend/railway-beat.toml` was not dead config but a missing service (DEPLOYMENT.md, "owner creates it"). The owner asked for it to be created now: section 2b.

## 2. Apply: done 2026-11-25 (the record, and what to do next time)

The owner approved, and the main session ran this section on 2026-11-25. This record comes from that session's report, not from this document's author. It differed from the plan in four ways. **Every future `railway config apply` should follow the corrected steps.**

1. **CLI.** 5.62.1 lacks `config migrate cutover`/`undo`, so the owner upgraded to 5.75.0 (`brew upgrade railway`). The 5.75.0 plan matched `…-iac-plan.txt` except that it dropped the `restartPolicyType (null → "ON_FAILURE")` lines, which it treats as the default. The same 7 changes and 1 destroy remained. So the byte `diff` against a plan saved by an older CLI fails on that alone: compare the change list instead.
2. **`cutover` does not clear the path.** `railway config migrate cutover --yes` printed "Nothing to do", although celery-worker's `railwayConfigFile` was still `/backend/railway-worker.toml`. It was cleared through the API with an **empty string** (`null` is a silent no-op):
   ```bash
   railway api 'mutation { serviceInstanceUpdate(serviceId:"8659519d-3fe3-4c1b-a3cb-0f16740cfba1", environmentId:"78e7c28e-e2f7-4994-a59e-7ed561e400ce", input:{railwayConfigFile:""}) }'
   ```
3. `railway config apply --yes --confirm-destructive` applied 5 changes. `railway config plan` then read "already up to date".
4. **Apply starts deployments.** It deployed `backend`, `celery-worker`, `frontend` and `catalog-cycle`. These services stay down until the relaunch, so **right after any apply, remove every new app deployment**:
   ```bash
   railway down --service backend      # likewise frontend and celery-worker, plus beat once it exists
   ```
   On 2026-11-25, backend and frontend were removed this way; celery-worker's build failed on its own (cause not investigated here). A new `catalog-cycle` deployment can stay: a cron deploy does not run the job.

Then merge the PR. Railway does not read `.railway/` during deploys, so the merge only records the file.

## 2b. Create `beat` (OWNER, after section 2)

`.railway/railway.ts` now declares `beat` as a copy of `backend/railway-beat.toml`:
- `backend/Dockerfile`, root `/backend`, `celery -A app.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule`;
- `ON_FAILURE` ×10 restarts and exactly one replica.

Its variables are references to celery-worker's own (`${{celery-worker.NAME}}`): `DATABASE_URL` (required by `Settings`), `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` and `ENVIRONMENT`. That way beat always publishes to the broker the worker consumes. Beat gets no `INGEST_DATABASE_URL`, `OPEN_METEO_API_KEY`, healthcheck URLs (the worker pings those) or API flags. The saved plan is `docs/runbooks/2026-11-25-railway-iac-plan-beat.txt`: `1 to add, 0 to change, 0 to destroy` / `+ Create service beat`.

```bash
railway config plan --verbose     # expect exactly the saved beat plan
railway config apply --yes        # nothing destructive, so no --confirm-destructive
railway down --service beat       # apply deploys it; beat stays down with the worker until the relaunch
```

**Auto-deploy.** IaC has no deploy-trigger field, and the plan fills beat's source branch with `main`. So creating the service may add an auto-deploy trigger, which the other services do not have (0 triggers each, read 2026-11-25). Check it, and delete any trigger in the dashboard (beat → Settings → Source → disable automatic deploys):

```bash
railway api 'query { deploymentTriggers(projectId:"8350c50d-4233-45fd-adce-f970c935d404", environmentId:"78e7c28e-e2f7-4994-a59e-7ed561e400ce", serviceId:"<beat service id>", first:5) { edges { node { id branch } } } }'   # expect no edges
```

At the relaunch, beat starts together with the worker (DEPLOYMENT.md: they ship together). Expect beat's log to show `beat: Starting...`. Within 15 minutes, the `safeascent-beat` healthchecks.io check should get its first ping, sent by the worker.

## 3. Verify after apply (OWNER or AGENT, read-only)

```bash
railway config plan --detailed-exit-code; echo "exit=$?"   # expect 0 and "already up to date"
```

Deployments since the apply. Expect none for the three app services:

```bash
for s in backend celery-worker frontend catalog-cycle; do echo "== $s"; railway deployment list --service "$s" 2>&1 | head -3; done
```

There is no running app service to health-check until the relaunch. At the relaunch, check each service:

- `backend`: the build log shows `load build definition from backend/Dockerfile`, and `curl -fsS https://api.safeascent.us/health` returns 200.
- `frontend`: the build log shows `frontend/Dockerfile`, and `curl -fsS https://safeascent.us/health` returns 200.
- `celery-worker`: the deploy log shows the worker `ready` line and **no** `beat: Starting...` line, and `GET /health/worker` returns 200 once beat is up.

## 4. No toml files: the 2026-12-01 step is no longer needed

**Update 2026-11-25, later the same day.** A read-only debugger found three problems:
- With no Config File path, Railway auto-read `backend/railway.toml` into **every** `/backend` service. It did this to celery-worker after its path was cleared, and to beat and catalog-cycle.
- That file's `/health` check failed the worker at 16:48Z, even though Celery logged `ready`.
- The instance `builder` reads `RAILPACK` on every service even after the IaC `builder: "DOCKERFILE"` applied. The API's `Builder` enum has no DOCKERFILE value.

So all five `railway*.toml` files are deleted (PR #46), and `.railway/railway.ts` is the only definition. Worker, beat and catalog-cycle declare `healthcheckPath: null`. `scripts/check_compose_matches_railway.py` refuses any `railway.toml`, `railway.json` or `railway-*.toml`. The plan for the updated `railway.ts` read "already up to date": the null healthchecks were already the live values. So once `main` has no toml, the cutoff changes nothing, and there is no 12-01 step.

**Builder, still to prove.** Railway's docs say a Dockerfile in the source is used automatically, and each service has a `dockerfilePath`. Docker should therefore be picked at build time whatever the instance field says. That is inferred, not yet seen. The proof is one deploy per service from a commit with no toml (`main` after the merge, or `ceddcf0` on the branch). The agent's deploy was refused by the permission system, so this is an **OWNER** step:

```bash
for s in backend celery-worker beat frontend catalog-cycle; do railway service redeploy --service "$s" --from-source --yes; done
```

For each new deployment, once it is terminal:

```bash
railway logs --build <id> --lines 400 | grep -E "build definition|uv sync|Using detected Dockerfile|railpack"
railway api 'query { deployment(id:"<id>") { status meta } }'   # read only meta.serviceManifest.build.builder, meta.configFile, meta.propertyFileMapping
```

Expect:

| Service | Build log | Manifest / health |
|---|---|---|
| backend | `load build definition from backend/Dockerfile` (no Railpack) | builder DOCKERFILE; `/health` passes |
| celery-worker | `backend/Dockerfile` | no healthcheck; deploy log `celery@… ready` |
| beat | `backend/Dockerfile` | no healthcheck; `beat: Starting...` |
| frontend | `frontend/Dockerfile` | `/health` passes |
| catalog-cycle | `load build definition from backend/Dockerfile.jobs`, `RUN uv sync --frozen --no-dev --no-install-project` (no `--no-group pipelines`) | no healthcheck. A cron deploy does not run the job; never `railway cron run` |

For every service, `configFile` must be null and `propertyFileMapping` empty. Then run `railway down --service <s> --yes` for backend, celery-worker, beat and frontend. catalog-cycle's deployment stays: it sleeps until its cron.

If a build log shows Railpack, the builder inference was wrong. Set `RAILWAY_DOCKERFILE_PATH` (the documented Dockerfile selector, docs.railway.com/builds/dockerfiles) in `railway.ts` for that service, re-plan and apply. If `Dockerfile.jobs` is not found, use `/backend/Dockerfile.jobs`.

## 5. Rollback

1. Put celery-worker's Config File path back with the section 2 mutation and `railwayConfigFile:"/backend/railway-worker.toml"`. (`migrate undo` has nothing to restore: `cutover` made no snapshot.)
2. Restore the pre-migration settings from `docs/runbooks/2026-11-25-railway-iac-baseline.ts`, the raw `railway config pull` of 2026-11-25. Before the apply, its plan reads "already up to date":
   ```bash
   railway config plan --file docs/runbooks/2026-11-25-railway-iac-baseline.ts    # the saved plan, reversed, minus the variable
   railway config apply --file docs/runbooks/2026-11-25-railway-iac-baseline.ts
   railway variable set RAILWAY_DOCKERFILE_PATH=backend/Dockerfile.jobs --service catalog-cycle --skip-deploys
   ```
   `preserve()` cannot recreate a deleted variable, hence the last line. The baseline has no `beat`, so this apply **deletes `beat`**. To keep it, add the `beat` block from `.railway/railway.ts` to a copy of the baseline first.
   The baseline predates the toml removal. A rollback that also wants the old file behaviour must restore the five `railway*.toml` files (`git revert` of the removal commit), and the guard then fails CI until it is reverted too.
   Like any apply, this one starts deployments: run `railway down` for the app services right after (section 2).
3. Rollback before 12-01 is fully equivalent, because the toml files still override. After 12-01, the rollback brings back the wrong dashboard values from section 1 (Railpack builder, worker with embedded beat).

## 6. Follow-ups (owner decisions, not in this PR)

- **Retire the toml files** after the apply and the cutoff. `scripts/check_compose_matches_railway.py` (CI `guards`) reads `backend/railway*.toml`, so it would first need to compare against `.railway/railway.ts`. Until then, change a setting in both places.
- **Drift gate in CI**: the `railwayapp/config` action, or `railway config plan --detailed-exit-code`. It needs a production-scoped `RAILWAY_TOKEN` repository secret.
- **Guard**: `scripts/check_compose_matches_railway.py` now also requires each toml `startCommand` (worker, beat, catalog-cycle) to equal that service's `start:` in `.railway/railway.ts`, so compose, the toml files and IaC cannot drift apart while all three exist.
- **celery-worker's failed build** from the 2026-11-25 apply: look at it before the relaunch.
