"""
Accident model with foreign keys and PostGIS geography.
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from geoalchemy2 import Geography

from app.db.session import Base


class Accident(Base):
    """Climbing accidents with location and details."""

    __tablename__ = "accidents"
    __table_args__ = (
        Index("idx_accidents_coords", "coordinates", postgresql_using="gist"),
        Index("idx_accidents_date", "date"),
        Index("idx_accidents_incident_group", "incident_group_id"),
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

    # Phase 2a repair columns (migration 0004). NULL means "not yet classified", never a default guess.
    date_precision = Column(Text, nullable=True)
    year_source = Column(Text, nullable=True)
    year_lo = Column(SmallInteger, nullable=True)
    year_hi = Column(SmallInteger, nullable=True)
    geocode_precision = Column(Text, nullable=True)
    geocode_method = Column(Text, nullable=True)
    country = Column(Text, nullable=True)
    activity_class = Column(Text, nullable=True)
    activity_rule_version = Column(Text, nullable=True)
    inclusion_flag = Column(Text, nullable=True)
    incident_group_id = Column(Integer, nullable=True)
    is_canonical = Column(Boolean, nullable=False, server_default=text("true"))
    severity_scale = Column(Text, nullable=True)
    excluded_reason = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    exp_years_climbing = Column(SmallInteger, nullable=True)
    exp_stated_level = Column(Text, nullable=False, server_default=text("'unknown'"))
    exp_first_season = Column(Boolean, nullable=True)
    guided = Column(Text, nullable=False, server_default=text("'unknown'"))
    exp_rule_version = Column(Text, nullable=True)

    # PostGIS geography column (automatically populated by database trigger)
    coordinates = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True)

    def __repr__(self):
        return f"<Accident(id={self.accident_id}, date={self.date}, severity='{self.injury_severity}')>"
