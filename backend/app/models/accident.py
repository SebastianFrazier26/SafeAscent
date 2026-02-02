"""
Accident model with foreign keys and PostGIS geography.
"""
from sqlalchemy import Column, Integer, String, Float, Date, Text, ForeignKey
from geoalchemy2 import Geography

from app.db.session import Base


class Accident(Base):
    """Climbing accidents with location and details."""

    __tablename__ = "accidents"

    accident_id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=True)
    source_id = Column(String(100), nullable=True)
    date = Column(Date, nullable=True, index=True)
    year = Column(Float, nullable=True)
    state = Column(String(100), nullable=True, index=True)
    location = Column(Text, nullable=True)
    mountain = Column(String(255), nullable=True)
    route = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    elevation_meters = Column(Float, nullable=True)  # Elevation in meters above sea level
    accident_type = Column(String(100), nullable=True, index=True)
    activity = Column(String(100), nullable=True)
    injury_severity = Column(String(50), nullable=True, index=True)
    age_range = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)

    # Foreign keys
    mountain_id = Column(Integer, ForeignKey("mountains.mountain_id"), nullable=True, index=True)
    route_id = Column(Integer, ForeignKey("routes.route_id"), nullable=True, index=True)

    # PostGIS geography column (automatically populated by database trigger)
    coordinates = Column(Geography(geometry_type="POINT", srid=4326), nullable=True)

    def __repr__(self):
        return f"<Accident(id={self.accident_id}, date={self.date}, severity='{self.injury_severity}')>"
