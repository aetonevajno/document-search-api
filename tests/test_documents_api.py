from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.documents import get_document_service
from app.db.models import Document
from app.main import create_app
from app.services.documents import DocumentService, DocumentStorageUnavailableError


class StubDocumentService:
    def __init__(
        self,
        *,
        documents: list[Document] | None = None,
        delete_results: list[bool] | None = None,
        search_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.documents = documents or []
        self.delete_results = delete_results or []
        self.search_error = search_error
        self.delete_error = delete_error
        self.search_queries: list[str] = []
        self.deleted_ids: list[UUID] = []

    async def search(self, query: str) -> list[Document]:
        self.search_queries.append(query)
        if self.search_error is not None:
            raise self.search_error
        return self.documents

    async def delete(self, document_id: UUID) -> bool:
        self.deleted_ids.append(document_id)
        if self.delete_error is not None:
            raise self.delete_error
        return self.delete_results.pop(0)


@asynccontextmanager
async def make_client(
    service: StubDocumentService,
) -> AsyncIterator[AsyncClient]:
    application = create_app()
    application.dependency_overrides[get_document_service] = lambda: cast(DocumentService, service)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_search_serializes_complete_documents_and_preserves_order() -> None:
    first_id = UUID("00000000-0000-0000-0000-000000000001")
    second_id = UUID("00000000-0000-0000-0000-000000000002")
    service = StubDocumentService(
        documents=[
            Document(
                id=first_id,
                rubrics=["новости", "космос"],
                text="Первая строка\nВторая строка",
                created_date=datetime(2021, 2, 3, 4, 5, 6),
            ),
            Document(
                id=second_id,
                rubrics=[],
                text="Другой документ",
                created_date=datetime(2020, 1, 2, 3, 4, 5),
            ),
        ]
    )

    async with make_client(service) as client:
        response = await client.get("/documents/search", params={"q": "  космос  "})

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(first_id),
            "rubrics": ["новости", "космос"],
            "text": "Первая строка\nВторая строка",
            "created_date": "2021-02-03T04:05:06",
        },
        {
            "id": str(second_id),
            "rubrics": [],
            "text": "Другой документ",
            "created_date": "2020-01-02T03:04:05",
        },
    ]
    assert service.search_queries == ["космос"]


@pytest.mark.parametrize("params", [None, {"q": ""}, {"q": "   "}])
async def test_search_rejects_missing_empty_and_blank_queries(
    params: dict[str, str] | None,
) -> None:
    service = StubDocumentService()

    async with make_client(service) as client:
        response = await client.get("/documents/search", params=params)

    assert response.status_code == 422
    assert service.search_queries == []


async def test_search_returns_neutral_503_for_storage_failure() -> None:
    service = StubDocumentService(
        search_error=DocumentStorageUnavailableError(
            "postgresql://user:secret@internal-host/database"
        )
    )

    async with make_client(service) as client:
        response = await client.get("/documents/search", params={"q": "текст"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Document storage is temporarily unavailable"}
    assert "secret" not in response.text


async def test_delete_returns_empty_204_then_404() -> None:
    document_id = uuid4()
    service = StubDocumentService(delete_results=[True, False])

    async with make_client(service) as client:
        first = await client.delete(f"/documents/{document_id}")
        second = await client.delete(f"/documents/{document_id}")

    assert first.status_code == 204
    assert first.content == b""
    assert second.status_code == 404
    assert second.json() == {"detail": "Document not found"}
    assert service.deleted_ids == [document_id, document_id]


async def test_delete_rejects_invalid_uuid_before_service_call() -> None:
    service = StubDocumentService(delete_results=[True])

    async with make_client(service) as client:
        response = await client.delete("/documents/not-a-uuid")

    assert response.status_code == 422
    assert service.deleted_ids == []


async def test_delete_returns_neutral_503_for_storage_failure() -> None:
    document_id = uuid4()
    service = StubDocumentService(
        delete_error=DocumentStorageUnavailableError("elasticsearch.internal:9200")
    )

    async with make_client(service) as client:
        response = await client.delete(f"/documents/{document_id}")

    assert response.status_code == 503
    assert response.json() == {"detail": "Document storage is temporarily unavailable"}
    assert "elasticsearch.internal" not in response.text


async def test_document_routes_are_fully_described_in_openapi() -> None:
    service = StubDocumentService()

    async with make_client(service) as client:
        schema = (await client.get("/openapi.json")).json()

    search = schema["paths"]["/documents/search"]["get"]
    delete = schema["paths"]["/documents/{document_id}"]["delete"]
    assert set(search["responses"]) >= {"200", "422", "503"}
    assert set(delete["responses"]) >= {"204", "404", "422", "503"}
    assert {parameter["name"] for parameter in search["parameters"]} == {"q"}
    assert {parameter["name"] for parameter in delete["parameters"]} == {"document_id"}
