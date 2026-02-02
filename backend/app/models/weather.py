"""
Weather model linked to accidents and baseline data.
"""
from sqlalchemy import Column, Integer, Float, Date, ForeignKey
from geoalchemy2 import Geography

from app.db.session import Base


class Weather(Base):
    """Weather observations linked to accidents and baseline data."""

    __tablename__ = "weather"

    weather_id = Column(Integer, primary_key=True, index=True)

    # Foreign key (NULL indicates baseline weather, not during accident)
    accident_id = Column(Integer, ForeignKey("accidents.accident_id"), nullable=True, index=True)

    date = Column(Date, nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    temperature_avg = Column(Float, nullable=True)
    temperature_min = Column(Float, nullable=True)
    temperature_max = Column(Float, nullable=True)
    wind_speed_avg = Column(Float, nullable=True)
    wind_speed_max = Column(Float, nullable=True)
    precipitation_total = Column(Float, nullable=True)
    visibility_avg = Column(Float, nullable=True)
    cloud_cover_avg = Column(Float, nullable=True)

    # PostGIS geography column (rounded coordinates from collection)
    coordinates = Column(Geography(geometry_type="POINT", srid=4326), nullable=True)

    def __repr__(self):
        return f"<Weather(id={self.weather_id}, date={self.date}, temp_avg={self.temperature_avg})>"
