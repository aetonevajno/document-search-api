"""Create the documents table.

Revision ID: 20261003_0001
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261003_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create PostgreSQL storage for complete documents."""
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "rubrics",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "text",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_date",
            sa.DateTime(timezone=False),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_documents",
        ),
    )


def downgrade() -> None:
    """Drop PostgreSQL document storage."""
    op.drop_table("documents")
