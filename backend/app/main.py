"""
SafeAscent FastAPI Application
Main entry point for the backend API.
"""
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.v1 import accidents, predict, locations, mp_routes, admin
from app.celery_signals import WorkerHealth, read_worker_health
from app.utils.cache import get_redis_client

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Climbing safety forecast API with real-time weather and historical accident data",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "SafeAscent API",
        "version": "1.0.0",
        "docs": f"{settings.API_V1_PREFIX}/docs",
    }


@app.get("/health")
async def health_check_root():
    """Root health check endpoint for Docker/load balancers"""
    return {"status": "healthy"}


@app.get(f"{settings.API_V1_PREFIX}/health")
async def health_check():
    """API health check endpoint"""
    return {"status": "healthy"}


@app.get("/health/worker")
def worker_health(response: Response) -> WorkerHealth:
    """503 when no worker heartbeat is in Redis; reports 7-day expired-task counts.

    Public, unauthenticated: only coarse status/age/counts, never Redis host or
    exception detail. Kept off the Railway API healthcheck path on purpose -
    a worker outage must not take the API down with it.
    """
    health = read_worker_health(
        get_redis_client(), now=time.time(), today=datetime.now(timezone.utc).date()
    )
    if health["status"] != "ok":
        response.status_code = 503
    return health


# Include API routers
# MP-based endpoints (273K routes, 61K locations)
app.include_router(locations.router, prefix=settings.API_V1_PREFIX, tags=["locations"])
app.include_router(mp_routes.router, prefix=settings.API_V1_PREFIX, tags=["routes"])

# Core endpoints
app.include_router(accidents.router, prefix=settings.API_V1_PREFIX, tags=["accidents"])
app.include_router(predict.router, prefix=settings.API_V1_PREFIX, tags=["predictions"])

# Admin endpoints (queue/cache management) - not web-exposed unless explicitly enabled
if settings.ENABLE_ADMIN_ROUTES:
    app.include_router(admin.router, prefix=settings.API_V1_PREFIX, tags=["admin"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Auto-reload on code changes
    )
