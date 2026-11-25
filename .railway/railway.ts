import { defineRailway, github, preserve, project, redis, service, volume } from "railway/iac";

// The only Railway definition (the railway*.toml files were removed 2026-11-25; Railway auto-read
// backend/railway.toml into every /backend service, and scripts/check_compose_matches_railway.py
// refuses any that come back). Whole-project file: a resource missing here is deleted on apply,
// so both Redis services and their volumes stay declared even though nothing here builds them.
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
    deploy: { cronSchedule: "0 9 2 1 *", restartPolicyType: "NEVER", healthcheckPath: null, healthcheckTimeout: null },
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
    // Never `--beat`: beat is its own service, and a second scheduler double-schedules every task.
    start: "celery -A app.celery_app worker --loglevel=info --concurrency=2 -E",
    replicas: { "us-east4-eqdc4a": 1 },
    // No HTTP server, so no healthcheck; liveness is /health/worker on the API.
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 10, healthcheckPath: null, healthcheckTimeout: null },
    env: { CACHE_REDIS_URL: preserve(), CELERY_BROKER_URL: preserve(), CELERY_RESULT_BACKEND: preserve(), CORS_ORIGINS: preserve(), DATABASE_URL: preserve(), DEBUG: preserve(), ENABLE_ADMIN_ROUTES: preserve(), ENVIRONMENT: preserve(), OPEN_METEO_API_KEY: preserve(), REDIS_URL: preserve(), SKIP_WEATHER_STATISTICS: preserve(), USE_VECTORIZED_ALGORITHM: preserve() },
  });
  // Exactly one replica: a second scheduler double-schedules every task. Broker and result backend reference the worker's own variables so the two can never
  // point at different Redis instances; DATABASE_URL only because Settings requires it.
  const beat = service("beat", {
    source: SafeAscent,
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "celery -A app.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule",
    replicas: { "us-east4-eqdc4a": 1 },
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 10, healthcheckPath: null, healthcheckTimeout: null },
    env: {
      DATABASE_URL: celeryWorker.env.DATABASE_URL,
      REDIS_URL: celeryWorker.env.REDIS_URL,
      CELERY_BROKER_URL: celeryWorker.env.CELERY_BROKER_URL,
      CELERY_RESULT_BACKEND: celeryWorker.env.CELERY_RESULT_BACKEND,
      ENVIRONMENT: celeryWorker.env.ENVIRONMENT,
    },
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
    resources: [catalogCycle, RedisMxVE, frontend, celeryWorker, beat, Redis, backend, redisMxveVolume, redisVolume],
  });
});
