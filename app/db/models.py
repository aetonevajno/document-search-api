"""Database models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Document(Base):
    """A complete document stored in PostgreSQL."""

    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    rubrics: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
    )
