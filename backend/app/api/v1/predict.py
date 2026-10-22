"""
Safety Prediction API Endpoint

POST /api/v1/predict - 410 Gone since the stored-score model (P3 Task 22, DP23)
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

PREDICT_GONE_DETAIL = (
    "Arbitrary-point prediction was retired with the kernel model; select a route on the map."
)


# DP23: 410, not 404, so a stale client learns the endpoint is gone for good. It answers before
# body validation, so any old request shape gets the same message.
@router.post("/predict", status_code=410, responses={410: {"description": "Gone"}})
async def predict_route_safety_endpoint() -> JSONResponse:
    return JSONResponse(status_code=410, content={"detail": PREDICT_GONE_DETAIL})
