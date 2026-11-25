import { defineRailway, github, preserve, project, redis, service, volume } from "railway/iac";

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
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "backend/Dockerfile.jobs" },
    start: "python -m app.pipelines.catalog_cycle run",
    replicas: { "us-east4-eqdc4a": 1 },
    deploy: { cronSchedule: "0 9 2 1 *", restartPolicyType: "NEVER" },
    env: { DATABASE_URL: preserve(), INGEST_DATABASE_URL: preserve(), RAILWAY_DOCKERFILE_PATH: preserve() },
  });
  const frontend = service("frontend", {
    source: github("SebastianFrazier26/SafeAscent", { rootDirectory: "/frontend" }),
    replicas: { "us-east4-eqdc4a": 1 },
    domains: [{ domain: "safeascent.us", port: 80 }],
    env: { MAINTENANCE_MODE: preserve(), PORT: preserve(), VITE_API_BASE_URL: preserve(), VITE_MAPBOX_TOKEN: preserve() },
  });
  const celeryWorker = service("celery-worker", {
    source: SafeAscent,
    start: "celery -A app.celery_app worker --beat --loglevel=info",
    replicas: { "us-east4-eqdc4a": 1 },
    env: { CACHE_REDIS_URL: preserve(), CELERY_BROKER_URL: preserve(), CELERY_RESULT_BACKEND: preserve(), CORS_ORIGINS: preserve(), DATABASE_URL: preserve(), DEBUG: preserve(), ENABLE_ADMIN_ROUTES: preserve(), ENVIRONMENT: preserve(), OPEN_METEO_API_KEY: preserve(), REDIS_URL: preserve(), SKIP_WEATHER_STATISTICS: preserve(), USE_VECTORIZED_ALGORITHM: preserve() },
  });
  const backend = service("backend", {
    source: SafeAscent,
    healthcheck: "/health",
    healthcheckTimeout: 30,
    replicas: { "us-east4-eqdc4a": 1 },
    domains: [{ domain: "api.safeascent.us", port: 8000 }],
    env: { CACHE_REDIS_URL: preserve(), CELERY_BROKER_URL: preserve(), CELERY_RESULT_BACKEND: preserve(), CORS_ORIGINS: preserve(), DATABASE_URL: preserve(), DEBUG: preserve(), ENABLE_ADMIN_ROUTES: preserve(), ENVIRONMENT: preserve(), OPEN_METEO_API_KEY: preserve(), PORT: preserve(), REDIS_URL: preserve(), USE_VECTORIZED_ALGORITHM: preserve() },
  });

  return project("safeascent", {
    resources: [catalogCycle, RedisMxVE, frontend, celeryWorker, Redis, backend, redisMxveVolume, redisVolume],
  });
});
