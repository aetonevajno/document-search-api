"""Unit tests for document use cases across both storage backends"""

import logging
from datetime import datetime
from types import TracebackType
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.search.index import DocumentSearchIndex, SearchIndexError
from app.services.documents import DocumentService, DocumentStorageUnavailableError


def make_document(
    document_id: UUID,
    *,
    created_date: datetime | None = None,
) -> Document:
    """Build a complete document without touching a database"""
    return Document(
        id=document_id,
        rubrics=["рубрика"],
        text=f"Документ {document_id}",
        created_date=created_date or datetime(2020, 1, 1, 0, 0, 0),
    )


def make_dependencies() -> tuple[AsyncSession, MagicMock, DocumentSearchIndex, MagicMock]:
    """Create typed session and Elasticsearch adapter doubles"""
    session_mock = MagicMock(spec=AsyncSession)
    session_mock.get = AsyncMock()
    session_mock.scalars = AsyncMock()
    session_mock.delete = AsyncMock()
    session_mock.flush = AsyncMock()

    index_mock = MagicMock(spec=DocumentSearchIndex)
    index_mock.search_ids = AsyncMock()
    index_mock.delete = AsyncMock()
    return (
        cast(AsyncSession, session_mock),
        session_mock,
        cast(DocumentSearchIndex, index_mock),
        index_mock,
    )


def configure_transaction(session_mock: MagicMock) -> MagicMock:
    """Make AsyncSession.begin() behave as an async context manager"""
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    session_mock.begin.return_value = transaction
    return transaction


async def test_search_returns_postgresql_order_for_elasticsearch_hits() -> None:
    """PostgreSQL's date/id ordering is preserved instead of relevance order"""
    session, session_mock, index, index_mock = make_dependencies()
    older_id = uuid4()
    newer_id = uuid4()
    newer = make_document(newer_id, created_date=datetime(2021, 1, 1, 0, 0, 0))
    older = make_document(older_id, created_date=datetime(2020, 1, 1, 0, 0, 0))
    scalar_result = MagicMock()
    scalar_result.all.return_value = [newer, older]
    session_mock.scalars.return_value = scalar_result
    index_mock.search_ids.return_value = [older_id, newer_id]

    result = await DocumentService(session, index).search("космос")

    assert result == [newer, older]
    index_mock.search_ids.assert_awaited_once_with("космос")
    session_mock.scalars.assert_awaited_once()


async def test_search_empty_hits_does_not_query_postgresql() -> None:
    """A no-hit Elasticsearch response avoids a redundant database query"""
    session, session_mock, index, index_mock = make_dependencies()
    index_mock.search_ids.return_value = []

    assert await DocumentService(session, index).search("нет совпадений") == []

    session_mock.scalars.assert_not_awaited()


async def test_search_filters_and_logs_stale_index_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Index-only IDs are omitted and reported without leaking query text"""
    session, session_mock, index, index_mock = make_dependencies()
    present_id = uuid4()
    stale_id = uuid4()
    present = make_document(present_id)
    scalar_result = MagicMock()
    scalar_result.all.return_value = [present]
    session_mock.scalars.return_value = scalar_result
    index_mock.search_ids.return_value = [present_id, stale_id]

    with caplog.at_level(logging.WARNING, logger="app.services.documents"):
        result = await DocumentService(session, index).search("секретный запрос")

    assert result == [present]
    assert str(stale_id) in caplog.text
    assert "секретный запрос" not in caplog.text


@pytest.mark.parametrize("backend", ["search", "database"])
async def test_search_maps_expected_backend_failures(backend: str) -> None:
    """Known storage errors become one domain-level availability failure"""
    session, session_mock, index, index_mock = make_dependencies()
    if backend == "search":
        index_mock.search_ids.side_effect = SearchIndexError("invalid response")
    else:
        index_mock.search_ids.return_value = [uuid4()]
        session_mock.scalars.side_effect = SQLAlchemyError("database unavailable")

    with pytest.raises(DocumentStorageUnavailableError):
        await DocumentService(session, index).search("query")


@pytest.mark.parametrize(
    ("database_exists", "index_exists", "expected"),
    [
        (True, True, True),
        (True, False, True),
        (False, True, True),
        (False, False, False),
    ],
)
async def test_delete_truth_table(
    database_exists: bool,
    index_exists: bool,
    expected: bool,
) -> None:
    """A document counts as deleted when it existed in either backend"""
    session, session_mock, index, index_mock = make_dependencies()
    configure_transaction(session_mock)
    document_id = uuid4()
    session_mock.get.return_value = make_document(document_id) if database_exists else None
    index_mock.delete.return_value = index_exists

    result = await DocumentService(session, index).delete(document_id)

    assert result is expected
    session_mock.begin.assert_called_once_with()
    index_mock.delete.assert_awaited_once_with(document_id)


async def test_delete_commits_database_before_deleting_from_index() -> None:
    """The source-of-truth transaction completes before Elasticsearch changes"""
    session, session_mock, index, index_mock = make_dependencies()
    transaction = configure_transaction(session_mock)
    document_id = uuid4()
    session_mock.get.return_value = make_document(document_id)
    events: list[str] = []

    async def finish_transaction(
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exception_type, exception, traceback
        events.append("database_commit")
        return False

    async def delete_index(_: UUID) -> bool:
        events.append("index_delete")
        return True

    transaction.__aexit__.side_effect = finish_transaction
    index_mock.delete.side_effect = delete_index

    assert await DocumentService(session, index).delete(document_id) is True
    assert events == ["database_commit", "index_delete"]


async def test_delete_database_failure_skips_index() -> None:
    """A failed source-of-truth transaction does not worsen consistency"""
    session, session_mock, index, index_mock = make_dependencies()
    configure_transaction(session_mock)
    session_mock.get.side_effect = SQLAlchemyError("database unavailable")

    with pytest.raises(DocumentStorageUnavailableError):
        await DocumentService(session, index).delete(uuid4())

    index_mock.delete.assert_not_awaited()


async def test_delete_commit_failure_skips_index() -> None:
    """Elasticsearch is untouched when PostgreSQL cannot confirm its commit"""
    session, session_mock, index, index_mock = make_dependencies()
    transaction = configure_transaction(session_mock)
    document_id = uuid4()
    session_mock.get.return_value = make_document(document_id)
    transaction.__aexit__.side_effect = SQLAlchemyError("commit failed")

    with pytest.raises(DocumentStorageUnavailableError):
        await DocumentService(session, index).delete(document_id)

    index_mock.delete.assert_not_awaited()


async def test_delete_index_failure_after_commit_is_retryable() -> None:
    """A second call can remove a stale index entry after PostgreSQL committed"""
    session, session_mock, index, index_mock = make_dependencies()
    configure_transaction(session_mock)
    document_id = uuid4()
    session_mock.get.side_effect = [make_document(document_id), None]
    index_mock.delete.side_effect = [SearchIndexError("temporary failure"), True]
    service = DocumentService(session, index)

    with pytest.raises(DocumentStorageUnavailableError):
        await service.delete(document_id)

    assert await service.delete(document_id) is True
    assert index_mock.delete.await_count == 2
