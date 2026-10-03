from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from app.db.models import Document


def test_document_mapping_matches_storage_contract() -> None:
    columns = Document.__table__.columns

    assert set(columns.keys()) == {"id", "rubrics", "text", "created_date"}
    assert columns.id.primary_key is True
    assert isinstance(columns.id.type, UUID)
    assert columns.id.type.as_uuid is True
    assert isinstance(columns.rubrics.type, ARRAY)
    assert isinstance(columns.rubrics.type.item_type, Text)
    assert isinstance(columns.text.type, Text)
    assert isinstance(columns.created_date.type, DateTime)
    assert columns.created_date.type.timezone is False
    assert all(column.nullable is False for column in columns)
