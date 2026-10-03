"""Unit tests for the Elasticsearch document index adapter"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig, ObjectApiResponse
from elasticsearch import AsyncElasticsearch, BadRequestError, NotFoundError

from app.search.index import (
    INDEX_MAPPINGS,
    BulkFailure,
    BulkIndexError,
    DocumentSearchIndex,
    IncompatibleIndexError,
    InvalidSearchResponseError,
    SearchDocument,
)

TEST_INDEX = "documents-test"
TEST_RESULT_LIMIT = 7


def make_client() -> tuple[AsyncElasticsearch, MagicMock, MagicMock]:
    """Create a typed client double with awaitable API methods"""
    client_mock = MagicMock()
    indices_mock = MagicMock()
    client_mock.indices = indices_mock
    client_mock.bulk = AsyncMock()
    client_mock.search = AsyncMock()
    client_mock.delete = AsyncMock()
    indices_mock.exists = AsyncMock()
    indices_mock.create = AsyncMock()
    indices_mock.get_mapping = AsyncMock()
    indices_mock.refresh = AsyncMock()
    return cast(AsyncElasticsearch, client_mock), client_mock, indices_mock


def make_index(client: AsyncElasticsearch) -> DocumentSearchIndex:
    """Create an adapter with a deliberately non-default result limit"""
    return DocumentSearchIndex(client, TEST_INDEX, TEST_RESULT_LIMIT)


def response(body: object) -> ObjectApiResponse[Any]:
    """Wrap a response body in the minimal object exposed by the client"""
    return cast(ObjectApiResponse[Any], SimpleNamespace(body=body))


def compatible_mapping() -> dict[str, object]:
    """Return the mapping shape Elasticsearch exposes for the test index"""
    return {
        TEST_INDEX: {
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "id": {"type": "keyword"},
                    "text": {"type": "text", "analyzer": "russian"},
                },
            }
        }
    }


def api_meta(status: int) -> ApiResponseMeta:
    """Build response metadata for client exception doubles"""
    return ApiResponseMeta(
        status=status,
        http_version="1.1",
        headers=HttpHeaders(),
        duration=0.0,
        node=NodeConfig("http", "localhost", 9200),
    )


async def test_ensure_exists_creates_and_validates_index() -> None:
    """A missing index is created with the exact required mapping"""
    client, _, indices = make_client()
    indices.exists.return_value = False
    indices.get_mapping.return_value = response(compatible_mapping())

    await make_index(client).ensure_exists()

    indices.create.assert_awaited_once_with(
        index=TEST_INDEX,
        mappings=INDEX_MAPPINGS,
    )
    indices.get_mapping.assert_awaited_once_with(index=TEST_INDEX)


async def test_ensure_exists_tolerates_concurrent_creation() -> None:
    """Another process winning the create race does not break startup"""
    client, _, indices = make_client()
    indices.exists.return_value = False
    indices.create.side_effect = BadRequestError(
        "resource_already_exists_exception",
        api_meta(400),
        {
            "error": {
                "type": "resource_already_exists_exception",
                "reason": "already exists",
            }
        },
    )
    indices.get_mapping.return_value = response(compatible_mapping())

    await make_index(client).ensure_exists()

    indices.get_mapping.assert_awaited_once_with(index=TEST_INDEX)


async def test_ensure_exists_rejects_incompatible_mapping() -> None:
    """An existing index cannot silently use a different analyzer"""
    client, _, indices = make_client()
    indices.exists.return_value = True
    mapping = compatible_mapping()
    mappings = cast(dict[str, Any], cast(dict[str, Any], mapping[TEST_INDEX])["mappings"])
    properties = cast(dict[str, Any], mappings["properties"])
    properties["text"]["analyzer"] = "standard"
    indices.get_mapping.return_value = response(mapping)

    with pytest.raises(IncompatibleIndexError):
        await make_index(client).ensure_exists()

    indices.create.assert_not_awaited()


async def test_ensure_exists_rejects_extra_mapped_fields() -> None:
    """The search index cannot silently retain fields outside its contract"""
    client, _, indices = make_client()
    indices.exists.return_value = True
    mapping = compatible_mapping()
    mappings = cast(dict[str, Any], cast(dict[str, Any], mapping[TEST_INDEX])["mappings"])
    properties = cast(dict[str, Any], mappings["properties"])
    properties["rubrics"] = {"type": "keyword"}
    indices.get_mapping.return_value = response(mapping)

    with pytest.raises(IncompatibleIndexError):
        await make_index(client).ensure_exists()


@pytest.mark.parametrize(
    "mapping_override",
    [
        {"index": False},
        {"search_analyzer": "standard"},
    ],
)
async def test_ensure_exists_rejects_unsearchable_text_mapping(
    mapping_override: dict[str, object],
) -> None:
    """Startup rejects mappings that would silently break Russian search"""
    client, _, indices = make_client()
    indices.exists.return_value = True
    mapping = compatible_mapping()
    mappings = cast(dict[str, Any], cast(dict[str, Any], mapping[TEST_INDEX])["mappings"])
    properties = cast(dict[str, Any], mappings["properties"])
    properties["text"].update(mapping_override)
    indices.get_mapping.return_value = response(mapping)

    with pytest.raises(IncompatibleIndexError):
        await make_index(client).ensure_exists()


async def test_upsert_many_short_circuits_empty_batch() -> None:
    """An empty batch does not issue a bulk request"""
    client, client_mock, _ = make_client()

    await make_index(client).upsert_many([])

    client_mock.bulk.assert_not_awaited()


async def test_upsert_many_deduplicates_and_uses_last_document() -> None:
    """Duplicate UUIDs become one index operation with last-write-wins semantics"""
    client, client_mock, _ = make_client()
    document_id = uuid4()
    client_mock.bulk.return_value = response({"errors": False, "items": []})

    await make_index(client).upsert_many(
        [
            SearchDocument(document_id, "old text"),
            SearchDocument(document_id, "new text"),
        ]
    )

    client_mock.bulk.assert_awaited_once_with(
        index=TEST_INDEX,
        operations=[
            {"index": {"_id": str(document_id)}},
            {"id": str(document_id), "text": "new text"},
        ],
    )


async def test_upsert_many_exposes_partial_bulk_failures() -> None:
    """A successful HTTP response cannot hide a failed bulk item"""
    client, client_mock, _ = make_client()
    document_id = uuid4()
    client_mock.bulk.return_value = response(
        {
            "errors": True,
            "items": [
                {
                    "index": {
                        "_id": str(document_id),
                        "status": 400,
                        "error": {
                            "type": "strict_dynamic_mapping_exception",
                            "reason": "mapping rejected the document",
                        },
                    }
                }
            ],
        }
    )

    with pytest.raises(BulkIndexError) as captured:
        await make_index(client).upsert_many([SearchDocument(document_id, "text")])

    assert captured.value.failures == (
        BulkFailure(
            document_id=str(document_id),
            status=400,
            reason="mapping rejected the document",
        ),
    )


async def test_search_ids_preserves_relevance_order_and_query_contract() -> None:
    """Search returns UUIDs in hit order and uses the agreed match query"""
    client, client_mock, _ = make_client()
    first_id = uuid4()
    second_id = uuid4()
    client_mock.search.return_value = response(
        {
            "hits": {
                "hits": [
                    {"_id": str(first_id), "_score": 2.0},
                    {"_id": str(second_id), "_score": 1.0},
                ]
            }
        }
    )

    result = await make_index(client).search_ids("тестовый запрос")

    assert result == [first_id, second_id]
    client_mock.search.assert_awaited_once_with(
        index=TEST_INDEX,
        query={
            "match": {
                "text": {
                    "query": "тестовый запрос",
                    "operator": "or",
                }
            }
        },
        size=TEST_RESULT_LIMIT,
        source=False,
    )


async def test_search_ids_rejects_non_uuid_hit() -> None:
    """Index corruption is surfaced instead of leaking an invalid identifier"""
    client, client_mock, _ = make_client()
    client_mock.search.return_value = response({"hits": {"hits": [{"_id": "not-a-uuid"}]}})

    with pytest.raises(InvalidSearchResponseError, match="invalid UUID"):
        await make_index(client).search_ids("query")


@pytest.mark.parametrize(
    ("result", "expected"),
    [("deleted", True), ("not_found", False)],
)
async def test_delete_interprets_supported_results(result: str, expected: bool) -> None:
    """Delete distinguishes an indexed document from an already absent one"""
    client, client_mock, _ = make_client()
    document_id = uuid4()
    client_mock.delete.return_value = response({"result": result})

    deleted = await make_index(client).delete(document_id)

    assert deleted is expected
    client_mock.delete.assert_awaited_once_with(index=TEST_INDEX, id=str(document_id))


async def test_delete_translates_elasticsearch_not_found() -> None:
    """The client's HTTP 404 exception is the normal absent-document result"""
    client, client_mock, _ = make_client()
    client_mock.delete.side_effect = NotFoundError(
        "not_found",
        api_meta(404),
        {"result": "not_found"},
    )

    assert await make_index(client).delete(uuid4()) is False


async def test_delete_does_not_hide_missing_index() -> None:
    """An unavailable index remains an infrastructure error for the API layer"""
    client, client_mock, _ = make_client()
    error = NotFoundError(
        "index_not_found_exception",
        api_meta(404),
        {
            "error": {
                "type": "index_not_found_exception",
                "reason": "no such index",
            },
            "status": 404,
        },
    )
    client_mock.delete.side_effect = error

    with pytest.raises(NotFoundError) as captured:
        await make_index(client).delete(uuid4())

    assert captured.value is error


async def test_refresh_targets_only_configured_index() -> None:
    """A caller can make a completed import visible with one explicit refresh"""
    client, _, indices = make_client()

    await make_index(client).refresh()

    indices.refresh.assert_awaited_once_with(index=TEST_INDEX)
