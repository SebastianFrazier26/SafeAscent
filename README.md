# SafeAscent

Route-level risk forecasts for climbers, built from historical accident records and the weather forecast.

> **Status (September 2026): offline for a rebuild.** safeascent.us shows a maintenance page while the data pipeline, model, and operations are rebuilt. The code here is the current state of that work, not a running service.

## What it does

For a climbing route and a date in the next few days, SafeAscent estimates risk on a 0–100 scale. Historical accidents near the route are weighted by distance, how recent they were, elevation, route type, severity, and how closely the weather in the days before each accident matches the coming forecast. Scores for every route are precomputed nightly for today and the next two days. See [`ALGORITHM_DESIGN.md`](./ALGORITHM_DESIGN.md) for the method.

How a result is shown:

- **One set of bands everywhere:** green below 25, yellow 25–50, orange 50–75, red 75 and up. Each band includes its lower edge, so 25.0 is yellow.
- **Never a made-up number.** A score that is missing, malformed, or failed to load reads "Unavailable" in neutral gray. It never falls back to 0.
- **"Too little evidence to estimate risk yet"** (gray) when no historical accident contributes to a route, or its raw score is below 0.05. This is an interim rule. The current model can only use accidents near a route; the planned Phase 3 model will borrow evidence from routes with similar characteristics anywhere.

## Architecture

![System design](system_design.png)

Diagram sources are in [`docs/diagrams/`](./docs/diagrams/) (Mermaid; regenerate the PNGs with `npx -y @mermaid-js/mermaid-cli@11 -i docs/diagrams/<name>.mmd -o <name>.png -b white -w 1600`).

- **API:** FastAPI (async SQLAlchemy + asyncpg) serving `/api/v1`, plus `/health` and `/health/worker`.
- **Jobs:** a Celery worker and a separate Celery beat service (never combined). At 02:00 UTC beat enqueues the nightly job, which writes scores to Redis and to the `historical_predictions` table.
- **Data:** Postgres + PostGIS on Neon. Alembic owns the schema (`backend/alembic/`). The app connects as a least-privilege role that can write only `historical_predictions`. See [`data/DATABASE_STRUCTURE.md`](./data/DATABASE_STRUCTURE.md) and the [data model diagram](./data_model.png).
- **Frontend:** React 19 + Vite + MUI + Mapbox GL, moving to TypeScript incrementally.
- **Hosting:** Railway (frontend, api, worker, beat, Redis). healthchecks.io alerts when the nightly job or the beat heartbeat goes quiet. See [`DEPLOYMENT.md`](./DEPLOYMENT.md).

## Local quickstart

Requires Docker, [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you), and Node 22.

```bash
docker compose up -d db redis             # local PostGIS + Redis on 127.0.0.1

cd backend
uv sync
export DATABASE_URL=postgresql+asyncpg://safeascent:safeascent_dev@localhost:5432/safeascent
export ENVIRONMENT=development CORS_ORIGINS=http://localhost:5173
MIGRATOR_DATABASE_URL="$DATABASE_URL" uv run alembic upgrade head
uv run uvicorn app.main:app --reload      # http://localhost:8000

# in another terminal
cd frontend
grep '^VITE_' ../.env.example > .env      # then put a real Mapbox public token in VITE_MAPBOX_TOKEN
npm ci
npm run dev                               # http://localhost:5173
```

A fresh database has the schema but no rows, so the map is empty and routes have no scores. The accident, route, and weather data are not distributed with this repository (see below).

The whole stack (db, redis, api, worker, beat, frontend, with the same start commands as Railway) also runs with `docker compose up --build`; apply migrations from `backend/` with the same `MIGRATOR_DATABASE_URL` as above. `.env.example` lists every setting the backend reads.

Tests and checks:

- Backend (from `backend/`): `uv run pytest`, `uv run ruff check app/ ../scripts/`, `uv run mypy`
- Frontend (from `frontend/`): `npm run test:run`, `npm run lint`, `npm run typecheck`, `npm run build`

Tests that need a populated database or live services are marked `needs_data` and skipped by default. [`CLAUDE.md`](./CLAUDE.md) has the full command list.

## Data sources and licenses

| Source | Used for | Terms |
|---|---|---|
| [OpenBeta](https://openbeta.io) | Rock route catalog (planned, Phase 2). Climb and area data only; no OpenBeta photos | CC0 1.0 |
| Mountain Project | Ice and mixed routes: name, grade, location, and type are displayed as facts. Everything else is internal only | No license claimed; see `DATA_LICENSE.md` |
| American Alpine Club, Avalanche.org/CAIC, NPS | Historical accidents | Each source's own terms; not redistributed |
| [Open-Meteo](https://open-meteo.com) | Forecasts and historical weather | Open-Meteo terms |

Only clients for open APIs live in this repo. It never contains scraper code; CI enforces that.

## Repository guide

- [`CLAUDE.md`](./CLAUDE.md): commands, conventions, and data and safety rules (for contributors and coding agents)
- [`DEPLOYMENT.md`](./DEPLOYMENT.md): Railway services, Neon, migrations, alerts, the CI/deploy pipeline, maintenance mode
- [`ALGORITHM_DESIGN.md`](./ALGORITHM_DESIGN.md): scoring method
- [`data/`](./data/): database structure and data sources
- [`CHANGELOG.md`](./CHANGELOG.md): dated changes

## License

Code: Apache-2.0 ([`LICENSE`](./LICENSE)). The code license does not cover data; see [`DATA_LICENSE.md`](./DATA_LICENSE.md).
