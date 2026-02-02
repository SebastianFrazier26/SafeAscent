"""
Ascent model for successful climb records.
"""
from sqlalchemy import Column, Integer, String, Date, Text, ForeignKey

from app.db.session import Base


class Ascent(Base):
    """Successful ascent records from Mountain Project."""

    __tablename__ = "ascents"

    ascent_id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    route_id = Column(Integer, ForeignKey("routes.route_id"), nullable=True, index=True)
    climber_id = Column(Integer, ForeignKey("climbers.climber_id"), nullable=True, index=True)

    date = Column(Date, nullable=True, index=True)
    style = Column(String(100), nullable=True)
    lead_style = Column(String(100), nullable=True)
    pitches = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    mp_tick_id = Column(String(50), nullable=True)

    def __repr__(self):
        return f"<Ascent(id={self.ascent_id}, route_id={self.route_id}, date={self.date})>"
