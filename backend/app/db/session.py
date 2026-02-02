"""
Database session management with SQLAlchemy async engine.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",  # Log SQL queries in dev
    pool_size=10,  # Connection pool size
    max_overflow=20,  # Max connections beyond pool_size
    pool_pre_ping=True,  # Check connection health before using
    pool_recycle=3600,  # Recycle connections after 1 hour
)

# Session factory
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit
)

# Base class for models
Base = declarative_base()


async def get_db() -> AsyncSession:
    """
    Dependency for getting database sessions.
    Usage:
        @router.get("/items")
        async def read_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
