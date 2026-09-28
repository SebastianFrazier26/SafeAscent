# SafeAscent frontend

The map and route-analytics UI for SafeAscent. See the [root README](../README.md) for what the project is and how the pieces fit.

## Stack

- **React 19** + **Vite 7**
- **TypeScript, incrementally:** `tsconfig.json` has `allowJs` with strict checking for `.ts`/`.tsx`. New modules are TypeScript; a `.js`/`.jsx` file is converted when it is substantially changed. Already TS: the API client, risk utilities, colour helpers, and the `useRouteSafety` hook.
- **MUI 7** (dark theme in `src/theme.js`), **Mapbox GL JS** via `react-map-gl`, **Recharts**, **axios**, **date-fns**
- **Vitest** + Testing Library; ESLint (with `typescript-eslint` for TS files)

## Setup

Requires Node 22 and the backend API running (see the root README's quickstart).

```bash
grep '^VITE_' ../.env.example > .env   # then set a real Mapbox *public* token (pk.…)
npm ci
npm run dev                            # http://localhost:5173
```

Build-time variables (read via `import.meta.env`, baked into the bundle):

| Variable | Purpose | Local default |
|---|---|---|
| `VITE_API_BASE_URL` | Backend base URL including `/api/v1` | `http://localhost:8000/api/v1` |
| `VITE_MAPBOX_TOKEN` | Mapbox public access token | none; the map does not load without it |

The backend only allows the origins in its `CORS_ORIGINS` setting; for local dev that must include `http://localhost:5173`.

## Commands

```bash
npm run dev          # dev server with HMR
npm test             # Vitest, watch mode
npm run test:run     # Vitest, single run (what CI runs)
npm run lint         # ESLint
npm run typecheck    # tsc --noEmit
npm run build        # production build into dist/
npm run preview      # serve the production build
```

`docker-tests/test_images.sh all` builds the Docker image in both `MAINTENANCE_MODE` variants and checks their HTTP behaviour (CI runs it too).

## Layout

```
src/
├── App.jsx, main.jsx, theme.js
├── components/
│   ├── MapView.jsx               # map, clusters, heatmap layers, season filter
│   ├── RiskLegend.jsx            # band and "insufficient data" legend
│   ├── RouteAnalyticsModal.jsx   # per-route tabs: forecast, details, accidents, breakdown, trends, time of day, ascents
│   ├── PredictionForm.jsx
│   └── PredictionResult.jsx
├── hooks/useRouteSafety.ts       # per-route score state: loading | error | insufficient | ok
├── services/api.ts               # typed, validated API client
└── utils/
    ├── riskUtils.ts              # risk bands, palette, formatting
    └── color.ts                  # contrast helpers
maintenance/                      # static 503 page and nginx config for maintenance mode
nginx.conf                        # production nginx (serves dist/, /health, www → apex redirect)
```

## Risk display rules

- One band definition: `RISK_BAND_THRESHOLDS = [25, 50, 75]` in `src/utils/riskUtils.ts`, lower-inclusive (green below 25, yellow from 25, orange from 50, red from 75). It mirrors `backend/app/services/risk_bands.py`, and a backend test fails if they drift, so change both together.
- A score is never fabricated. A missing, malformed, or failed score renders "Unavailable" (or an em-dash in compact chips) in neutral gray. No `|| 0` / `?? 0` on a risk value; `riskUtils.test.ts` and `riskPalette.test.ts` guard this.
- When the backend reports `data_status: "insufficient_data"` (no contributing accidents, or a raw score below 0.05), the UI shows "Too little evidence to estimate risk yet" in gray instead of a number. This is interim until the Phase 3 model.
- Map colours come from the nightly precomputed scores; the route modal fetches a live score for the selected route and date.

## Deployment

The Dockerfile builds with Node 22 and serves the bundle with nginx on Railway. The `MAINTENANCE_MODE` build arg switches to a static 503 page (with `/health` still 200). See [`DEPLOYMENT.md`](../DEPLOYMENT.md).

## License

Code: Apache-2.0, see the root [`LICENSE`](../LICENSE). Data is not covered by the code license; see [`DATA_LICENSE.md`](../DATA_LICENSE.md).
