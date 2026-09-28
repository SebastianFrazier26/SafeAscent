"""
Ascent model for successful climb records.
"""
from sqlalchemy import Column, Integer, String, Date, Text, ForeignKey, Index

from app.db.session import Base


class Ascent(Base):
    """Successful ascent records from Mountain Project."""

    __tablename__ = "ascents"
    __table_args__ = (
        Index("idx_ascents_climber", "climber_id"),
        Index("idx_ascents_date", "date"),
        Index("idx_ascents_route", "route_id"),
    )

    ascent_id = Column(Integer, primary_key=True)

    # Foreign keys
    route_id = Column(Integer, ForeignKey("routes.route_id"), nullable=True)
    climber_id = Column(Integer, ForeignKey("climbers.climber_id"), nullable=True)

    date = Column(Date, nullable=True)
    style = Column(String(100), nullable=True)
    lead_style = Column(String(100), nullable=True)
    pitches = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    mp_tick_id = Column(String(50), nullable=True)

    def __repr__(self):
        return f"<Ascent(id={self.ascent_id}, route_id={self.route_id}, date={self.date})>"
