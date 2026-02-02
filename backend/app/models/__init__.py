"""
Database models export.
Import all models here to make them available for Alembic migrations.
"""
from app.models.mountain import Mountain
from app.models.route import Route
from app.models.accident import Accident
from app.models.weather import Weather
from app.models.climber import Climber
from app.models.ascent import Ascent

__all__ = [
    "Mountain",
    "Route",
    "Accident",
    "Weather",
    "Climber",
    "Ascent",
]
