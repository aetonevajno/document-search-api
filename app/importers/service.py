"""Idempotent orchestration of PostgreSQL and Elasticsearch imports"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from app.db.models import Document
from app.db.repositories import DocumentRepository
from app.db.session import Database
from app.importers.csv_parser import ImportDocument, ImportPlan
from app.search.index import SearchDocument


class DocumentBatchSink(Protocol):
    """Persist one committed batch of complete documents"""

    async def upsert_many(self, documents: Collection[ImportDocument]) -> None: ...


class SearchIndexSink(Protocol):
    """Maintain the searchable representation of imported documents"""

    async def ensure_exists(self) -> None: ...

    async def upsert_many(self, documents: Collection[SearchDocument]) -> None: ...

    async def refresh(self) -> None: ...


class PostgreSQLDocumentSink:
    """Commit each import batch in its own PostgreSQL transaction"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_many(self, documents: Collection[ImportDocument]) -> None:
        """Upsert a complete batch and commit before returning"""
        models = [
            Document(
                id=document.id,
                rubrics=list(document.rubrics),
                text=document.text,
                created_date=document.created_date,
            )
            for document in documents
        ]
        async with self._database.session_factory.begin() as session:
            await DocumentRepository(session).upsert_many(models)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Stable summary printed by the CLI and asserted by tests"""

    rows_read: int
    unique_documents: int
    duplicate_rows: int
    batches_processed: int


class DocumentImportService:
    """Write PostgreSQL first, then Elasticsearch, one batch at a time"""

    def __init__(
        self,
        document_sink: DocumentBatchSink,
        search_index: SearchIndexSink,
        *,
        batch_size: int,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._document_sink = document_sink
        self._search_index = search_index
        self._batch_size = batch_size

    async def import_plan(self, plan: ImportPlan) -> ImportResult:
        """Execute a fully validated plan without hiding partial failures"""
        await self._search_index.ensure_exists()
        batches_processed = 0
        for offset in range(0, len(plan.documents), self._batch_size):
            batch = plan.documents[offset : offset + self._batch_size]
            await self._document_sink.upsert_many(batch)
            await self._search_index.upsert_many(
                [SearchDocument(id=document.id, text=document.text) for document in batch]
            )
            batches_processed += 1

        if plan.documents:
            await self._search_index.refresh()

        return ImportResult(
            rows_read=plan.rows_read,
            unique_documents=len(plan.documents),
            duplicate_rows=plan.duplicate_rows,
            batches_processed=batches_processed,
        )
