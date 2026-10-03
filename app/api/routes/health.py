from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """API health response"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Check API health",
)
async def health_check() -> HealthResponse:
    """Report whether the API process is running"""
    return HealthResponse()
