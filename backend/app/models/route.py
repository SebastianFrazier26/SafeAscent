"""
Route model with foreign key to mountains.
"""
from sqlalchemy import Column, Integer, String, Float, ForeignKey
from geoalchemy2 import Geography

from app.db.session import Base


class Route(Base):
    """Climbing routes linked to mountains."""

    __tablename__ = "routes"

    route_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    mountain_id = Column(Integer, ForeignKey("mountains.mountain_id"), nullable=True, index=True)
    mountain_name = Column(String(255), nullable=True)
    grade = Column(String(50), nullable=True)
    grade_yds = Column(String(50), nullable=True)
    length_ft = Column(Float, nullable=True)
    pitches = Column(Integer, nullable=True)
    type = Column(String(100), nullable=True)
    first_ascent_year = Column(Integer, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    accident_count = Column(Integer, default=0)
    mp_route_id = Column(String(50), nullable=True, index=True)

    # PostGIS geography column (automatically populated by database trigger)
    coordinates = Column(Geography(geometry_type="POINT", srid=4326), nullable=True)

    def __repr__(self):
        return f"<Route(id={self.route_id}, name='{self.name}', grade='{self.grade_yds}')>"
