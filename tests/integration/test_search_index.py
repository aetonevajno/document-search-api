"""Integration tests for the real Elasticsearch document index"""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from elasticsearch import BadRequestError

from app.core.config import Settings
from app.search.client import ElasticsearchConnection
from app.search.index import DocumentSearchIndex, SearchDocument

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def search_index() -> AsyncIterator[DocumentSearchIndex]:
    """Create an isolated index whose name explicitly marks it as test-only"""
    elasticsearch_url = os.getenv("TEST_ELASTICSEARCH_URL")
    index_name = os.getenv("TEST_ELASTICSEARCH_INDEX")
    if elasticsearch_url is None or index_name is None:
        pytest.skip(
            "TEST_ELASTICSEARCH_URL and TEST_ELASTICSEARCH_INDEX are required "
            "for Elasticsearch integration tests"
        )
    if not index_name.endswith("-test"):
        pytest.fail("integration tests require an Elasticsearch index ending in '-test'")

    settings = Settings()
    connection = ElasticsearchConnection(
        elasticsearch_url,
        request_timeout_seconds=settings.elasticsearch_request_timeout_seconds,
        max_retries=settings.elasticsearch_max_retries,
        retry_on_timeout=settings.elasticsearch_retry_on_timeout,
    )
    index = DocumentSearchIndex(connection.client, index_name, result_limit=20)
    if await connection.client.indices.exists(index=index_name):
        await connection.client.indices.delete(index=index_name)
    await index.ensure_exists()

    try:
        yield index
    finally:
        await connection.client.indices.delete(
            index=index_name,
            ignore_unavailable=True,
        )
        await connection.close()


async def test_index_is_idempotent_and_rejects_unknown_fields(
    search_index: DocumentSearchIndex,
) -> None:
    """The required mapping survives repeated setup and remains strict"""
    await search_index.ensure_exists()

    with pytest.raises(BadRequestError):
        await search_index._client.index(  # noqa: SLF001 - verifies the real mapping boundary
            index=search_index.index_name,
            id=str(uuid4()),
            document={"id": str(uuid4()), "text": "text", "unexpected": "value"},
        )


async def test_upsert_search_update_and_delete(search_index: DocumentSearchIndex) -> None:
    """Russian text is searchable and all write operations are idempotent"""
    document_id = uuid4()
    other_id = uuid4()
    await search_index.upsert_many(
        [
            SearchDocument(
                id=document_id,
                text="Космонавтика развивается.\nТекст остаётся многострочным.",
            ),
            SearchDocument(id=other_id, text="Совсем другой документ"),
        ]
    )
    await search_index.refresh()

    assert await search_index.search_ids("космонавтикой") == [document_id]

    await search_index.upsert_many(
        [SearchDocument(id=document_id, text="Теперь документ только про океанографию")]
    )
    await search_index.refresh()

    assert await search_index.search_ids("космонавтика") == []
    assert await search_index.search_ids("океанография") == [document_id]
    assert await search_index.delete(document_id) is True
    assert await search_index.delete(document_id) is False
