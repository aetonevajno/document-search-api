from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    """Document returned by the API"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    rubrics: list[str]
    text: str
    created_date: datetime


class ErrorResponse(BaseModel):
    """API error response"""

    model_config = ConfigDict(extra="forbid")

    detail: str
