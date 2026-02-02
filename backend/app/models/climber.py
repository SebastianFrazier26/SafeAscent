"""
Climber model for Mountain Project users.
"""
from sqlalchemy import Column, Integer, String

from app.db.session import Base


class Climber(Base):
    """Climber profiles from Mountain Project."""

    __tablename__ = "climbers"

    climber_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), nullable=False, unique=True, index=True)
    mp_user_id = Column(String(50), nullable=True, index=True)

    def __repr__(self):
        return f"<Climber(id={self.climber_id}, username='{self.username}')>"
