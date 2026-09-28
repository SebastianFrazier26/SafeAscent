"""Metadata-only stand-ins for legacy tables that Phase 2a drops.

accidents.route_id and accidents.mountain_id carry real FKs to these tables, so the
targets must exist in Base.metadata for FK resolution. alembic/env.py excludes them
from autogenerate, so their full live shape never has to be modelled.
"""

from sqlalchemy import Column, Integer, Table

from app.db.session import Base

UNMANAGED_LEGACY_TABLES = frozenset({"routes", "mountains"})

routes_table = Table("routes", Base.metadata, Column("route_id", Integer, primary_key=True))
mountains_table = Table("mountains", Base.metadata, Column("mountain_id", Integer, primary_key=True))
