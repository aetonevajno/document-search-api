import os
from datetime import datetime
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db.models import Document
from app.db.repositories import DocumentRepository
from app.db.session import Database
from app.main import create_app
from app.search.client import ElasticsearchConnection
from app.search.index import DocumentSearchIndex, SearchDocument

pytestmark = pytest.mark.integration


def _unique_russian_word() -> str:
    alphabet = "абвгдежзийклмнопр"
    return "".join(alphabet[int(digit, 16)] for digit in uuid4().hex)


async def test_search_and_delete_use_both_real_backends() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    elasticsearch_url = os.getenv("TEST_ELASTICSEARCH_URL")
    index_name = os.getenv("TEST_ELASTICSEARCH_INDEX")
    if database_url is None or elasticsearch_url is None or index_name is None:
        pytest.skip(
            "TEST_DATABASE_URL, TEST_ELASTICSEARCH_URL, and TEST_ELASTICSEARCH_INDEX "
            "are required for API integration tests"
        )

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("integration tests require a database name ending in '_test'")
    if not index_name.endswith("-test"):
        pytest.fail("integration tests require an Elasticsearch index ending in '-test'")

    settings = Settings(
        database_url=database_url,
        elasticsearch_url=elasticsearch_url,
        elasticsearch_index=index_name,
        search_result_limit=20,
    )
    application = create_app(settings)
    database = cast(Database, application.state.database)
    elasticsearch = cast(ElasticsearchConnection, application.state.elasticsearch)
    search_index = cast(
        DocumentSearchIndex,
        application.state.document_search_index,
    )

    older_id = uuid4()
    first_tied_id, second_tied_id = sorted((uuid4(), uuid4()))
    document_ids = (older_id, first_tied_id, second_tied_id)
    query = _unique_russian_word()
    documents = [
        Document(
            id=older_id,
            rubrics=["История", "Архив"],
            text=f"Старый документ про {query}",
            created_date=datetime(2020, 1, 1, 9, 0, 0),
        ),
        Document(
            id=second_tied_id,
            rubrics=["Наука", "Второй"],
            text=f"Второй новый документ про {query}",
            created_date=datetime(2021, 2, 3, 10, 11, 12),
        ),
        Document(
            id=first_tied_id,
            rubrics=["Наука", "Первый"],
            text=f"Первый новый документ про {query}",
            created_date=datetime(2021, 2, 3, 10, 11, 12),
        ),
    ]
    documents_by_id = {document.id: document for document in documents}
    expected_order = (first_tied_id, second_tied_id, older_id)
    deleted_id = first_tied_id

    async with application.router.lifespan_context(application):
        try:
            async with database.session_factory.begin() as session:
                await session.execute(delete(Document).where(Document.id.in_(document_ids)))
            for document_id in document_ids:
                await search_index.delete(document_id)

            async with database.session_factory.begin() as session:
                await DocumentRepository(session).upsert_many(documents)
            await search_index.upsert_many(
                [SearchDocument(id=document.id, text=document.text) for document in documents]
            )
            await search_index.refresh()

            transport = ASGITransport(app=application)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                search_response = await client.get("/documents/search", params={"q": query})

                assert search_response.status_code == 200
                assert search_response.json() == [
                    {
                        "id": str(document_id),
                        "rubrics": documents_by_id[document_id].rubrics,
                        "text": documents_by_id[document_id].text,
                        "created_date": documents_by_id[document_id].created_date.isoformat(),
                    }
                    for document_id in expected_order
                ]

                delete_response = await client.delete(f"/documents/{deleted_id}")
                assert delete_response.status_code == 204
                assert delete_response.content == b""

                async with database.session_factory() as session:
                    assert await DocumentRepository(session).get(deleted_id) is None
                    for document_id in (second_tied_id, older_id):
                        assert await DocumentRepository(session).get(document_id) is not None
                assert not bool(
                    await elasticsearch.client.exists(
                        index=index_name,
                        id=str(deleted_id),
                    )
                )
                for document_id in (second_tied_id, older_id):
                    assert bool(
                        await elasticsearch.client.exists(
                            index=index_name,
                            id=str(document_id),
                        )
                    )

                repeated_response = await client.delete(f"/documents/{deleted_id}")
                assert repeated_response.status_code == 404
                assert repeated_response.json() == {"detail": "Document not found"}
        finally:
            try:
                async with database.session_factory.begin() as session:
                    await session.execute(delete(Document).where(Document.id.in_(document_ids)))
            finally:
                for document_id in document_ids:
                    await search_index.delete(document_id)
                await search_index.refresh()
