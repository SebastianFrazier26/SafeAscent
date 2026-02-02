"""
Pydantic schemas export.
"""
from app.schemas.mountain import (
    MountainBase,
    MountainResponse,
    MountainListResponse,
    MountainDetail,
)
from app.schemas.route import (
    RouteBase,
    RouteResponse,
    RouteListResponse,
    RouteDetail,
)
from app.schemas.accident import (
    AccidentBase,
    AccidentResponse,
    AccidentListResponse,
    AccidentDetail,
)

__all__ = [
    # Mountains
    "MountainBase",
    "MountainResponse",
    "MountainListResponse",
    "MountainDetail",
    # Routes
    "RouteBase",
    "RouteResponse",
    "RouteListResponse",
    "RouteDetail",
    # Accidents
    "AccidentBase",
    "AccidentResponse",
    "AccidentListResponse",
    "AccidentDetail",
]
