import { defineRailway, github, preserve, project, redis, service, volume } from "railway/iac";

// Whole-project file: a resource missing here is deleted on apply, so both Redis services and
// their volumes stay declared even though nothing in this repo builds them.
// backend, celery-worker and frontend copy their backend/*.toml and frontend/railway.toml values
// (which overrode the dashboard until Config as Code's 2026-12-01 cutoff); keep the two in step
// until those files are retired. The `beat` service (backend/railway-beat.toml) does not exist
// on Railway yet; it is the owner's relaunch step (DEPLOYMENT.md) and gets declared here then.
export default defineRailway(() => {
  const SafeAscent = github("SebastianFrazier26/SafeAscent", { rootDirectory: "/backend" });

  const RedisMxVE = redis("Redis-mxVE", { region: "us-east4-eqdc4a" });
  RedisMxVE.deploy = { startCommand: "/bin/sh -c \"rm -rf $RAILWAY_VOLUME_MOUNT_PATH/lost+found/ && exec docker-entrypoint.sh redis-server --requirepass $REDIS_PASSWORD --save 60 1 --dir $RAILWAY_VOLUME_MOUNT_PATH\"" };
  RedisMxVE.networking = { privateNetworkEndpoint: "redis-mxve" };
  const Redis = redis("Redis", { region: "us-east4-eqdc4a" });
  Redis.deploy = { startCommand: "/bin/sh -c \"rm -rf $RAILWAY_VOLUME_MOUNT_PATH/lost+found/ && exec docker-entrypoint.sh redis-server --requirepass $REDIS_PASSWORD --save 60 1 --dir $RAILWAY_VOLUME_MOUNT_PATH\"" };
  Redis.networking = { privateNetworkEndpoint: "redis" };
  const redisMxveVolume = volume("redis-mxve-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-east4-eqdc4a", sizeMB: 5000 });
  const redisVolume = volume("redis-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-east4-eqdc4a", sizeMB: 500 });

  const catalogCycle = service("catalog-cycle", {
    source: github("SebastianFrazier26/SafeAscent", { rootDirectory: "backend" }),
    // Relative to the root directory, the same form as backend's "Dockerfile" (which builds
    // backend/Dockerfile); "backend/Dockerfile.jobs" would point at backend/backend/. The
    // RAILWAY_DOCKERFILE_PATH variable is left out on purpose so it is deleted: it held the
    // repo-relative path and would compete with this field.
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile.jobs" },
    start: "python -m app.pipelines.catalog_cycle run",
    replicas: { "us-east4-eqdc4a": 1 },
    deploy: { cronSchedule: "0 9 2 1 *", restartPolicyType: "NEVER" },
    env: { DATABASE_URL: preserve(), INGEST_DATABASE_URL: preserve() },
  });
  const frontend = service("frontend", {
    source: github("SebastianFrazier26/SafeAscent", { rootDirectory: "/frontend" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/health",
    healthcheckTimeout: 10,
    replicas: { "us-east4-eqdc4a": 1 },
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 3 },
    domains: [{ domain: "safeascent.us", port: 80 }],
    env: { MAINTENANCE_MODE: preserve(), PORT: preserve(), VITE_API_BASE_URL: preserve(), VITE_MAPBOX_TOKEN: preserve() },
  });
  const celeryWorker = service("celery-worker", {
    source: SafeAscent,
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    // The dashboard still holds the old `worker --beat` command; railway-worker.toml overrode it.
    // Beat is its own service by design (a second scheduler double-schedules every task).
    start: "celery -A app.celery_app worker --loglevel=info --concurrency=2 -E",
    replicas: { "us-east4-eqdc4a": 1 },
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 10 },
    env: { CACHE_REDIS_URL: preserve(), CELERY_BROKER_URL: preserve(), CELERY_RESULT_BACKEND: preserve(), CORS_ORIGINS: preserve(), DATABASE_URL: preserve(), DEBUG: preserve(), ENABLE_ADMIN_ROUTES: preserve(), ENVIRONMENT: preserve(), OPEN_METEO_API_KEY: preserve(), REDIS_URL: preserve(), SKIP_WEATHER_STATISTICS: preserve(), USE_VECTORIZED_ALGORITHM: preserve() },
  });
  const backend = service("backend", {
    source: SafeAscent,
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/health",
    healthcheckTimeout: 30,
    replicas: { "us-east4-eqdc4a": 1 },
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 3 },
    domains: [{ domain: "api.safeascent.us", port: 8000 }],
    env: { CACHE_REDIS_URL: preserve(), CELERY_BROKER_URL: preserve(), CELERY_RESULT_BACKEND: preserve(), CORS_ORIGINS: preserve(), DATABASE_URL: preserve(), DEBUG: preserve(), ENABLE_ADMIN_ROUTES: preserve(), ENVIRONMENT: preserve(), OPEN_METEO_API_KEY: preserve(), PORT: preserve(), REDIS_URL: preserve(), USE_VECTORIZED_ALGORITHM: preserve() },
  });

  return project("safeascent", {
    resources: [catalogCycle, RedisMxVE, frontend, celeryWorker, Redis, backend, redisMxveVolume, redisVolume],
  });
});
