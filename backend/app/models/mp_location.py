"""
MpLocation model for Mountain Project climbing areas.
"""
from sqlalchemy import Column, BigInteger, String, Float, DateTime, Index
from sqlalchemy.sql import func

from app.db.session import Base


class MpLocation(Base):
    """Mountain Project climbing areas/locations hierarchy."""

    __tablename__ = "mp_locations"
    __table_args__ = (
        Index("idx_mp_locations_coords", "latitude", "longitude"),
        Index("idx_mp_locations_name", "name"),
        Index("idx_mp_locations_parent", "parent_id"),
    )

    mp_id = Column(BigInteger, primary_key=True)
    name = Column(String(500), nullable=False)
    parent_id = Column(BigInteger, nullable=True)
    url = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<MpLocation(mp_id={self.mp_id}, name='{self.name}')>"
