"""
Accident model with foreign keys and PostGIS geography.
"""
from sqlalchemy import BigInteger, Column, Integer, String, Float, Date, Text, ForeignKey, Index
from geoalchemy2 import Geography

from app.db.session import Base


class Accident(Base):
    """Climbing accidents with location and details."""

    __tablename__ = "accidents"
    __table_args__ = (
        Index("idx_accidents_coords", "coordinates", postgresql_using="gist"),
        Index("idx_accidents_date", "date"),
        Index("idx_accidents_mountain", "mountain_id"),
        Index("idx_accidents_mp_route_id", "mp_route_id"),
        Index("idx_accidents_route", "route_id"),
        Index("idx_accidents_severity", "injury_severity"),
        Index("idx_accidents_state", "state"),
        Index("idx_accidents_type", "accident_type"),
    )

    accident_id = Column(Integer, primary_key=True)
    source = Column(String(50), nullable=True)
    source_id = Column(String(100), nullable=True)
    date = Column(Date, nullable=True)
    year = Column(Float, nullable=True)
    state = Column(String(100), nullable=True)
    location = Column(Text, nullable=True)
    mountain = Column(String(255), nullable=True)
    route = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    elevation_meters = Column(Float, nullable=True)  # Elevation in meters above sea level
    accident_type = Column(String(100), nullable=True)
    activity = Column(String(100), nullable=True)
    injury_severity = Column(String(50), nullable=True)
    age_range = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)

    # Foreign keys
    mountain_id = Column(Integer, ForeignKey("mountains.mountain_id"), nullable=True)
    route_id = Column(Integer, ForeignKey("routes.route_id"), nullable=True)
    mp_route_id = Column(BigInteger, ForeignKey("mp_routes.mp_route_id"), nullable=True)

    # PostGIS geography column (automatically populated by database trigger)
    coordinates = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True)

    def __repr__(self):
        return f"<Accident(id={self.accident_id}, date={self.date}, severity='{self.injury_severity}')>"
