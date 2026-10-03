"""Public document API schemas"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    """A complete document hydrated from PostgreSQL"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    rubrics: list[str]
    text: str
    created_date: datetime


class ErrorResponse(BaseModel):
    """Stable error envelope used by explicit API failures"""

    model_config = ConfigDict(extra="forbid")

    detail: str
