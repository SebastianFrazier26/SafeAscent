"""
SafeAscent FastAPI Application
Main entry point for the backend API.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.v1 import mountains, routes, accidents, predict

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


# Include API routers
app.include_router(mountains.router, prefix=settings.API_V1_PREFIX, tags=["mountains"])
app.include_router(routes.router, prefix=settings.API_V1_PREFIX, tags=["routes"])
app.include_router(accidents.router, prefix=settings.API_V1_PREFIX, tags=["accidents"])
app.include_router(predict.router, prefix=settings.API_V1_PREFIX, tags=["predictions"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Auto-reload on code changes
    )
