"""Document use cases spanning PostgreSQL and Elasticsearch"""

import logging
from uuid import UUID

from elastic_transport import TransportError
from elasticsearch import ApiError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.db.repositories import DocumentRepository
from app.search.index import DocumentSearchIndex, SearchIndexError

logger = logging.getLogger(__name__)


class DocumentStorageUnavailableError(RuntimeError):
    """A required storage operation could not be confirmed"""


class DocumentService:
    """Coordinate document search and idempotent cross-storage deletion"""

    def __init__(
        self,
        session: AsyncSession,
        search_index: DocumentSearchIndex,
    ) -> None:
        self._session = session
        self._repository = DocumentRepository(session)
        self._search_index = search_index

    async def search(self, query: str) -> list[Document]:
        """Hydrate Elasticsearch hits from PostgreSQL in creation-date order"""
        try:
            document_ids = await self._search_index.search_ids(query)
        except (ApiError, TransportError, SearchIndexError) as error:
            raise DocumentStorageUnavailableError(
                "document search storage is unavailable"
            ) from error

        if not document_ids:
            return []

        try:
            documents = await self._repository.get_many(document_ids)
        except SQLAlchemyError as error:
            raise DocumentStorageUnavailableError(
                "document search storage is unavailable"
            ) from error

        stored_ids = {document.id for document in documents}
        missing_ids = [document_id for document_id in document_ids if document_id not in stored_ids]
        if missing_ids:
            logger.warning(
                "Search index returned %d document(s) absent from PostgreSQL: %s",
                len(missing_ids),
                ", ".join(str(document_id) for document_id in missing_ids),
            )
        return documents

    async def delete(self, document_id: UUID) -> bool:
        """Delete from PostgreSQL first, then converge Elasticsearch"""
        try:
            async with self._session.begin():
                database_deleted = await self._repository.delete(document_id)
        except SQLAlchemyError as error:
            raise DocumentStorageUnavailableError(
                "document deletion storage is unavailable"
            ) from error

        try:
            index_deleted = await self._search_index.delete(document_id)
        except (ApiError, TransportError, SearchIndexError) as error:
            raise DocumentStorageUnavailableError(
                "document deletion storage is unavailable"
            ) from error

        return database_deleted or index_deleted
