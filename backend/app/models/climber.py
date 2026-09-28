"""
Climber model for Mountain Project users.
"""
from sqlalchemy import Column, Integer, String, Index, UniqueConstraint

from app.db.session import Base


class Climber(Base):
    """Climber profiles from Mountain Project."""

    __tablename__ = "climbers"
    __table_args__ = (
        UniqueConstraint("username", name="climbers_username_key"),
        Index("idx_climbers_mp_id", "mp_user_id"),
        Index("idx_climbers_username", "username"),
    )

    climber_id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=False)
    mp_user_id = Column(String(50), nullable=True)

    def __repr__(self):
        return f"<Climber(id={self.climber_id}, username='{self.username}')>"
