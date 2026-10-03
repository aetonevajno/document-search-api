"""PostgreSQL integration tests for document persistence."""

import os
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.db.models import Document
from app.db.repositories import DocumentRepository
from app.db.session import Database

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    """Create a clean schema in an explicitly dedicated test database."""
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("integration tests require a database name ending in '_test'")

    database = Database(database_url)
    async with database.session_factory.begin() as session:
        await session.execute(delete(Document))

    try:
        yield database
    finally:
        async with database.session_factory.begin() as session:
            await session.execute(delete(Document))
        await database.dispose()


@pytest.mark.asyncio
async def test_document_round_trip_and_delete(database: Database) -> None:
    """PostgreSQL preserves UUID, arrays, Unicode text, and naive timestamps."""
    document = Document(
        id=uuid4(),
        rubrics=["VK-1", "кириллица", "with space"],
        text="Первая строка\nВторая строка",
        created_date=datetime(2019, 12, 31, 18, 0, 8),
    )

    async with database.session_factory.begin() as session:
        await DocumentRepository(session).add(document)

    async with database.session_factory() as session:
        stored = await DocumentRepository(session).get(document.id)

    assert stored is not None
    assert stored.id == document.id
    assert stored.rubrics == document.rubrics
    assert stored.text == document.text
    assert stored.created_date == document.created_date
    assert stored.created_date.tzinfo is None

    async with database.session_factory.begin() as session:
        deleted = await DocumentRepository(session).delete(document.id)

    assert deleted is True

    async with database.session_factory() as session:
        assert await DocumentRepository(session).get(document.id) is None


@pytest.mark.asyncio
async def test_caller_transaction_rolls_back_repository_write(database: Database) -> None:
    """An exception rolls back repository changes because it never commits itself."""
    document = Document(
        id=uuid4(),
        rubrics=[],
        text="Эта запись должна быть отменена",
        created_date=datetime(2019, 1, 1, 0, 0, 0),
    )

    with pytest.raises(RuntimeError, match="force rollback"):
        async with database.session_factory.begin() as session:
            await DocumentRepository(session).add(document)
            raise RuntimeError("force rollback")

    async with database.session_factory() as session:
        assert await DocumentRepository(session).get(document.id) is None


@pytest.mark.asyncio
async def test_upsert_many_updates_existing_document(database: Database) -> None:
    """Bulk upsert is idempotent and updates mutable document fields."""
    document_id = uuid4()
    original = Document(
        id=document_id,
        rubrics=["VK-old"],
        text="Старый текст",
        created_date=datetime(2019, 1, 1, 0, 0, 0),
    )
    updated = Document(
        id=document_id,
        rubrics=["VK-new"],
        text="Новый текст",
        created_date=datetime(2019, 2, 1, 0, 0, 0),
    )

    async with database.session_factory.begin() as session:
        repository = DocumentRepository(session)
        await repository.upsert_many([original])
        await repository.upsert_many([updated])

    async with database.session_factory() as session:
        stored = await DocumentRepository(session).get(document_id)

    assert stored is not None
    assert stored.rubrics == ["VK-new"]
    assert stored.text == "Новый текст"
    assert stored.created_date == datetime(2019, 2, 1, 0, 0, 0)


@pytest.mark.asyncio
async def test_upsert_many_uses_last_duplicate_in_same_batch(
    database: Database,
) -> None:
    """Duplicate deterministic IDs are collapsed before PostgreSQL sees them."""
    document_id = uuid4()
    first = Document(
        id=document_id,
        rubrics=["VK-first"],
        text="Первая версия",
        created_date=datetime(2019, 1, 1, 0, 0, 0),
    )
    last = Document(
        id=document_id,
        rubrics=["VK-last"],
        text="Последняя версия",
        created_date=datetime(2019, 3, 1, 0, 0, 0),
    )

    async with database.session_factory.begin() as session:
        await DocumentRepository(session).upsert_many([first, last])

    async with database.session_factory() as session:
        stored = await DocumentRepository(session).get(document_id)

    assert stored is not None
    assert stored.rubrics == ["VK-last"]
    assert stored.text == "Последняя версия"
    assert stored.created_date == datetime(2019, 3, 1, 0, 0, 0)
