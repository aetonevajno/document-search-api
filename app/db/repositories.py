from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, document_id: UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def get_many(self, document_ids: Collection[UUID]) -> list[Document]:
        if not document_ids:
            return []

        result = await self._session.scalars(
            select(Document)
            .where(Document.id.in_(document_ids))
            .order_by(Document.created_date.desc(), Document.id.asc())
        )
        return list(result.all())

    async def upsert_many(self, documents: Collection[Document]) -> None:
        unique_documents = {document.id: document for document in documents}
        values = [
            {
                "id": document.id,
                "rubrics": document.rubrics,
                "text": document.text,
                "created_date": document.created_date,
            }
            for document in unique_documents.values()
        ]
        if not values:
            return

        statement = insert(Document).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[Document.id],
            set_={
                "rubrics": statement.excluded.rubrics,
                "text": statement.excluded.text,
                "created_date": statement.excluded.created_date,
            },
        )
        await self._session.execute(statement)

    async def delete(self, document_id: UUID) -> bool:
        document = await self.get(document_id)
        if document is None:
            return False

        await self._session.delete(document)
        await self._session.flush()
        return True
