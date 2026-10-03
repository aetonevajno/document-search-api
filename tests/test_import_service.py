"""Tests for cross-storage document import orchestration"""

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

import pytest

from app.importers.csv_parser import ImportDocument, ImportPlan
from app.importers.service import DocumentImportService
from app.search.index import SearchDocument


def make_documents(count: int) -> tuple[ImportDocument, ...]:
    """Build stable import documents without parsing CSV in service tests"""
    return tuple(
        ImportDocument(
            id=UUID(int=index + 1),
            rubrics=(f"R-{index}",),
            text=f"text-{index}",
            created_date=datetime(2019, 1, 1, 0, 0, index),
        )
        for index in range(count)
    )


class RecordingDocumentSink:
    """Record committed database batches and optionally fail"""

    def __init__(self, events: list[tuple[str, tuple[UUID, ...]]], fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def upsert_many(self, documents: Collection[ImportDocument]) -> None:
        ids = tuple(document.id for document in documents)
        self.events.append(("database", ids))
        if self.fail:
            raise RuntimeError("database failed")


class RecordingSearchIndex:
    """Record index operations and optionally fail a bulk request"""

    def __init__(self, events: list[tuple[str, tuple[UUID, ...]]], fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def ensure_exists(self) -> None:
        self.events.append(("ensure", ()))

    async def upsert_many(self, documents: Collection[SearchDocument]) -> None:
        ids = tuple(document.id for document in documents)
        self.events.append(("search", ids))
        if self.fail:
            raise RuntimeError("search failed")

    async def refresh(self) -> None:
        self.events.append(("refresh", ()))


async def test_import_batches_database_before_search_and_refreshes_once() -> None:
    """Each committed DB batch precedes its matching bulk index operation"""
    events: list[tuple[str, tuple[UUID, ...]]] = []
    documents = make_documents(5)
    service = DocumentImportService(
        RecordingDocumentSink(events),
        RecordingSearchIndex(events),
        batch_size=2,
    )

    result = await service.import_plan(ImportPlan(rows_read=6, documents=documents))

    assert events == [
        ("ensure", ()),
        ("database", (documents[0].id, documents[1].id)),
        ("search", (documents[0].id, documents[1].id)),
        ("database", (documents[2].id, documents[3].id)),
        ("search", (documents[2].id, documents[3].id)),
        ("database", (documents[4].id,)),
        ("search", (documents[4].id,)),
        ("refresh", ()),
    ]
    assert result.rows_read == 6
    assert result.unique_documents == 5
    assert result.duplicate_rows == 1
    assert result.batches_processed == 3


async def test_empty_plan_prepares_index_without_writes_or_refresh() -> None:
    """A header-only source succeeds without pointless storage operations"""
    events: list[tuple[str, tuple[UUID, ...]]] = []
    service = DocumentImportService(
        RecordingDocumentSink(events),
        RecordingSearchIndex(events),
        batch_size=500,
    )

    result = await service.import_plan(ImportPlan(rows_read=0, documents=()))

    assert events == [("ensure", ())]
    assert result.batches_processed == 0


async def test_database_failure_skips_search_and_refresh() -> None:
    """An uncommitted database batch is never sent to Elasticsearch"""
    events: list[tuple[str, tuple[UUID, ...]]] = []
    service = DocumentImportService(
        RecordingDocumentSink(events, fail=True),
        RecordingSearchIndex(events),
        batch_size=10,
    )

    with pytest.raises(RuntimeError, match="database failed"):
        await service.import_plan(ImportPlan(rows_read=1, documents=make_documents(1)))

    assert [name for name, _ in events] == ["ensure", "database"]


async def test_search_failure_keeps_database_first_and_skips_refresh() -> None:
    """A failed bulk leaves committed DB data recoverable by a repeat import"""
    events: list[tuple[str, tuple[UUID, ...]]] = []
    service = DocumentImportService(
        RecordingDocumentSink(events),
        RecordingSearchIndex(events, fail=True),
        batch_size=10,
    )

    with pytest.raises(RuntimeError, match="search failed"):
        await service.import_plan(ImportPlan(rows_read=1, documents=make_documents(1)))

    assert [name for name, _ in events] == ["ensure", "database", "search"]


def test_import_service_rejects_non_positive_batch_size() -> None:
    """Direct callers receive the same safety guarantee as Settings users"""
    events: list[tuple[str, tuple[UUID, ...]]] = []

    with pytest.raises(ValueError, match="batch_size must be positive"):
        DocumentImportService(
            RecordingDocumentSink(events),
            RecordingSearchIndex(events),
            batch_size=0,
        )
