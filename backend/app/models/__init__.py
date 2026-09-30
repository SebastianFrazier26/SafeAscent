"""
Database models export.
Import all models here to make them available for Alembic migrations.
"""
from app.models import legacy  # noqa: F401  (registers FK targets on Base.metadata)
from app.models import pipeline  # noqa: F401  (Phase 2 bookkeeping tables)
from app.models.accident import Accident
from app.models.mp_location import MpLocation
from app.models.mp_route import MpRoute
from app.models.weather import Weather

__all__ = [
    "Accident",
    "Weather",
    "MpLocation",
    "MpRoute",
]
