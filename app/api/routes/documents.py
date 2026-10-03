import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import DocumentResponse, ErrorResponse
from app.db.session import get_session
from app.search.index import DocumentSearchIndex, get_document_search_index
from app.services.documents import DocumentService, DocumentStorageUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


def get_document_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    search_index: Annotated[
        DocumentSearchIndex,
        Depends(get_document_search_index),
    ],
) -> DocumentService:
    return DocumentService(session, search_index)


@router.get(
    "/search",
    response_model=list[DocumentResponse],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "PostgreSQL or Elasticsearch is unavailable",
        }
    },
    summary="Search documents by text",
)
async def search_documents(
    service: Annotated[DocumentService, Depends(get_document_service)],
    q: Annotated[
        str,
        Query(
            min_length=1,
            pattern=r"\S",
            description="Non-blank text searched with the Russian analyzer",
        ),
    ],
) -> list[DocumentResponse]:
    """Return up to 20 matching documents, newest first"""
    try:
        documents = await service.search(q.strip())
    except DocumentStorageUnavailableError as error:
        logger.exception("Document search failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document storage is temporarily unavailable",
        ) from error
    return [DocumentResponse.model_validate(document) for document in documents]


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Document is absent from both storages",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "PostgreSQL or Elasticsearch is unavailable",
        },
    },
    summary="Delete a document",
)
async def delete_document(
    document_id: UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> Response:
    """Delete a document from PostgreSQL and Elasticsearch"""
    try:
        deleted = await service.delete(document_id)
    except DocumentStorageUnavailableError as error:
        logger.exception("Document deletion failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document storage is temporarily unavailable",
        ) from error

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
