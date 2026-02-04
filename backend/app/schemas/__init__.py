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
from app.schemas.mp_location import (
    MpLocationBase,
    MpLocationResponse,
    MpLocationListResponse,
    MpLocationDetail,
)
from app.schemas.mp_route import (
    MpRouteBase,
    MpRouteResponse,
    MpRouteListResponse,
    MpRouteDetail,
    MpRouteMapMarker,
    MpRouteMapResponse,
    MpRouteSafetyResponse,
)

__all__ = [
    # Mountains (legacy)
    "MountainBase",
    "MountainResponse",
    "MountainListResponse",
    "MountainDetail",
    # Routes (legacy)
    "RouteBase",
    "RouteResponse",
    "RouteListResponse",
    "RouteDetail",
    # Accidents
    "AccidentBase",
    "AccidentResponse",
    "AccidentListResponse",
    "AccidentDetail",
    # MP Locations
    "MpLocationBase",
    "MpLocationResponse",
    "MpLocationListResponse",
    "MpLocationDetail",
    # MP Routes
    "MpRouteBase",
    "MpRouteResponse",
    "MpRouteListResponse",
    "MpRouteDetail",
    "MpRouteMapMarker",
    "MpRouteMapResponse",
    "MpRouteSafetyResponse",
]
