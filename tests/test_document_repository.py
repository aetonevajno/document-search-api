"""Unit tests for the document repository transaction contract."""

from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.db.repositories import DocumentRepository


def make_document() -> Document:
    """Build a document without touching a database."""
    return Document(
        id=uuid4(),
        rubrics=["VK-1", "VK-2"],
        text="Тестовый документ",
        created_date=datetime(2019, 1, 2, 3, 4, 5),
    )


def make_session_mock() -> tuple[AsyncSession, AsyncMock]:
    """Build a typed AsyncSession mock."""
    session_mock = AsyncMock(spec=AsyncSession)
    return cast(AsyncSession, session_mock), session_mock


@pytest.mark.asyncio
async def test_add_flushes_without_committing() -> None:
    """Repository writes remain inside the caller-owned transaction."""
    session, session_mock = make_session_mock()
    document = make_document()

    result = await DocumentRepository(session).add(document)

    assert result is document
    session_mock.add.assert_called_once_with(document)
    session_mock.flush.assert_awaited_once_with()
    session_mock.commit.assert_not_awaited()
    session_mock.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_many_returns_scalar_documents() -> None:
    """Repository converts SQLAlchemy scalar results to a plain list."""
    session, session_mock = make_session_mock()
    document = make_document()
    scalar_result = MagicMock()
    scalar_result.all.return_value = [document]
    session_mock.scalars.return_value = scalar_result

    result = await DocumentRepository(session).get_many([document.id])

    assert result == [document]
    session_mock.scalars.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_many_short_circuits_for_empty_ids() -> None:
    """An empty ID collection does not execute a database query."""
    session, session_mock = make_session_mock()

    result = await DocumentRepository(session).get_many([])

    assert result == []
    session_mock.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_many_short_circuits_for_empty_documents() -> None:
    """An empty batch does not execute a database statement."""
    session, session_mock = make_session_mock()

    await DocumentRepository(session).upsert_many([])

    session_mock.execute.assert_not_awaited()
    session_mock.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_reports_missing_document() -> None:
    """Deleting an unknown ID is a no-op inside the transaction."""
    session, session_mock = make_session_mock()
    session_mock.get.return_value = None

    deleted = await DocumentRepository(session).delete(uuid4())

    assert deleted is False
    session_mock.delete.assert_not_awaited()
    session_mock.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_flushes_existing_document_without_committing() -> None:
    """Deleting an existing row is flushed but never committed by the repository."""
    session, session_mock = make_session_mock()
    document = make_document()
    session_mock.get.return_value = document

    deleted = await DocumentRepository(session).delete(document.id)

    assert deleted is True
    session_mock.delete.assert_awaited_once_with(document)
    session_mock.flush.assert_awaited_once_with()
    session_mock.commit.assert_not_awaited()
    session_mock.rollback.assert_not_awaited()
