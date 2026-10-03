"""Pydantic schemas exposed by the HTTP API"""

from app.api.schemas.documents import DocumentResponse, ErrorResponse

__all__ = ["DocumentResponse", "ErrorResponse"]
