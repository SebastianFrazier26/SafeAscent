"""
Pydantic schemas for safety prediction API endpoints.

Defines request and response models for the /api/v1/predict endpoint.
"""
from datetime import date
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, validator


class PredictionRequest(BaseModel):
    """
    Request schema for safety prediction.

    Example:
        {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 150.0
        }
    """

    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Latitude of planned route in degrees",
        example=40.0150,
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Longitude of planned route in degrees",
        example=-105.2705,
    )
    route_type: str = Field(
        ...,
        description="Type of climbing route: alpine, ice, mixed, trad, sport, aid, boulder",
        example="alpine",
    )
    planned_date: date = Field(
        ...,
        description="Date of planned climb (ISO 8601 format: YYYY-MM-DD)",
        example="2024-07-15",
    )
    elevation_meters: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=9000.0,
        description="Route elevation in meters above sea level (auto-detected from coordinates if omitted)",
        example=4345.0,
    )
    search_radius_km: Optional[float] = Field(
        default=None,
        ge=10.0,
        le=500.0,
        description="Maximum search radius for accidents in kilometers (default: route-type-specific)",
        example=150.0,
    )
    route_grade: Optional[str] = Field(
        default=None,
        description="Climbing grade of route (e.g., '5.10a', 'V5', 'WI4') for grade similarity matching",
        example="5.10a",
    )

    @validator("route_type")
    def validate_route_type(cls, v):
        """Validate route type is one of the known types."""
        valid_types = ["alpine", "ice", "mixed", "trad", "sport", "aid", "boulder"]
        if v.lower() not in valid_types:
            raise ValueError(
                f"route_type must be one of: {', '.join(valid_types)}. Got: {v}"
            )
        return v.lower()

    class Config:
        json_schema_extra = {
            "example": {
                "latitude": 40.0150,
                "longitude": -105.2705,
                "route_type": "alpine",
                "planned_date": "2024-07-15",
                "search_radius_km": 150.0,
            }
        }


class ContributingAccident(BaseModel):
    """
    Summary of an accident that contributed to the risk score.

    Simplified for UI display.
    """

    accident_id: int = Field(..., description="Unique accident identifier")
    total_influence: float = Field(
        ..., description="Combined influence weight (0.0 to ~2.0)"
    )
    distance_km: float = Field(..., description="Distance from route in kilometers")
    days_ago: int = Field(..., description="Days since accident occurred")
    spatial_weight: float = Field(..., description="Spatial decay weight (0-1)")
    temporal_weight: float = Field(..., description="Temporal decay weight (0-1.5)")
    elevation_weight: float = Field(..., description="Elevation asymmetric weight (0-1)")
    weather_weight: float = Field(..., description="Weather similarity weight (0-2+)")
    route_type_weight: float = Field(..., description="Route type similarity (0-1)")
    severity_weight: float = Field(..., description="Severity boost (1.0-1.3)")
    grade_weight: float = Field(
        default=1.0, description="Grade similarity weight (0.25-1.0)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "accident_id": 1234,
                "total_influence": 0.847,
                "distance_km": 23.4,
                "days_ago": 547,
                "spatial_weight": 0.891,
                "temporal_weight": 1.287,
                "weather_weight": 0.823,
                "route_type_weight": 1.0,
                "severity_weight": 1.1,
            }
        }


class PredictionResponse(BaseModel):
    """
    Complete safety prediction response.

    Example:
        {
            "risk_score": 68.4,
            "num_contributing_accidents": 47,
            "top_contributing_accidents": [...],
            "metadata": {...}
        }
    """

    risk_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Risk score from 0 (safe) to 100 (very dangerous)",
    )
    num_contributing_accidents: int = Field(
        ..., description="Total number of accidents that influenced this prediction"
    )
    top_contributing_accidents: List[ContributingAccident] = Field(
        ..., description="Top accidents by influence (max 50)"
    )
    metadata: Dict = Field(..., description="Additional metadata about the calculation")

    class Config:
        json_schema_extra = {
            "example": {
                "risk_score": 68.4,
                "num_contributing_accidents": 47,
                "top_contributing_accidents": [
                    {
                        "accident_id": 1234,
                        "total_influence": 0.847,
                        "distance_km": 23.4,
                        "days_ago": 547,
                        "spatial_weight": 0.891,
                        "temporal_weight": 1.287,
                        "weather_weight": 0.823,
                        "route_type_weight": 1.0,
                        "severity_weight": 1.1,
                    }
                ],
                "metadata": {
                    "route_type": "alpine",
                    "search_date": "2024-07-15",
                    "total_influence_sum": 6.84,
                    "normalization_factor": 10.0,
                },
            }
        }
