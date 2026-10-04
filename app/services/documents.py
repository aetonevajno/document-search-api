import logging
from uuid import UUID

from elasticsearch import ApiError, TransportError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.db.repositories import DocumentRepository
from app.search.index import DocumentSearchIndex, SearchIndexError

logger = logging.getLogger(__name__)


class DocumentStorageUnavailableError(RuntimeError):
    pass


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        search_index: DocumentSearchIndex,
    ) -> None:
        self._session = session
        self._repository = DocumentRepository(session)
        self._search_index = search_index

    async def search(self, query: str) -> list[Document]:
        try:
            document_ids = await self._search_index.search_ids(query)
            if not document_ids:
                return []
            documents = await self._repository.get_many(document_ids)
        except (ApiError, TransportError, SearchIndexError, SQLAlchemyError, OSError) as error:
            raise DocumentStorageUnavailableError("document storage is unavailable") from error

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
        try:
            async with self._session.begin():
                database_deleted = await self._repository.delete(document_id)
            index_deleted = await self._search_index.delete(document_id)
        except (ApiError, TransportError, SearchIndexError, SQLAlchemyError, OSError) as error:
            raise DocumentStorageUnavailableError("document storage is unavailable") from error

        return database_deleted or index_deleted
