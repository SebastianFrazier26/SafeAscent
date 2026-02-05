"""
Database models export.
Import all models here to make them available for Alembic migrations.
"""
from app.models.accident import Accident
from app.models.weather import Weather
from app.models.climber import Climber
from app.models.ascent import Ascent
from app.models.mp_location import MpLocation
from app.models.mp_route import MpRoute

__all__ = [
    "Accident",
    "Weather",
    "Climber",
    "Ascent",
    "MpLocation",
    "MpRoute",
]
