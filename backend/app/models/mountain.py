"""
Mountain model with PostGIS geography support.
"""
from sqlalchemy import Column, Integer, String, Float, Text
from geoalchemy2 import Geography

from app.db.session import Base


class Mountain(Base):
    """Mountains and climbing areas with geographic data."""

    __tablename__ = "mountains"

    mountain_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    alt_names = Column(Text, nullable=True)
    elevation_ft = Column(Float, nullable=True)
    prominence_ft = Column(Float, nullable=True)
    type = Column(String(50), nullable=True)
    range = Column(String(255), nullable=True)
    state = Column(String(100), nullable=True, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    location = Column(Text, nullable=True)
    accident_count = Column(Integer, default=0)

    # PostGIS geography column (automatically populated by database trigger)
    coordinates = Column(Geography(geometry_type="POINT", srid=4326), nullable=True)

    def __repr__(self):
        return f"<Mountain(id={self.mountain_id}, name='{self.name}', state='{self.state}')>"
