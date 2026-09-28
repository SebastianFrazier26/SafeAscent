"""
MpRoute model for Mountain Project climbing routes.
"""
from sqlalchemy import Column, BigInteger, String, Float, DateTime, ForeignKey, Index
from sqlalchemy.sql import func

from app.db.session import Base


class MpRoute(Base):
    """Mountain Project climbing routes with location reference."""

    __tablename__ = "mp_routes"
    __table_args__ = (
        Index("idx_mp_routes_coords", "latitude", "longitude"),
        Index("idx_mp_routes_grade", "grade"),
        Index("idx_mp_routes_location", "location_id"),
        Index("idx_mp_routes_name", "name"),
        Index("idx_mp_routes_type", "type"),
    )

    mp_route_id = Column(BigInteger, primary_key=True)
    name = Column(String(500), nullable=False)
    url = Column(String(500), nullable=True)
    location_id = Column(BigInteger, ForeignKey("mp_locations.mp_id"), nullable=True)
    grade = Column(String(100), nullable=True)
    type = Column(String(100), nullable=True)
    length_ft = Column(Float, nullable=True)
    pitches = Column(Float, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<MpRoute(mp_route_id={self.mp_route_id}, name='{self.name}', grade='{self.grade}')>"
