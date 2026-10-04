from collections.abc import Collection
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from elasticsearch import AsyncElasticsearch, BadRequestError, NotFoundError
from fastapi import Request

INDEX_MAPPINGS: dict[str, object] = {
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "text": {"type": "text", "analyzer": "russian"},
    },
}


class SearchIndexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SearchDocument:
    id: UUID
    text: str


@dataclass(frozen=True, slots=True)
class BulkFailure:
    document_id: str
    status: int
    reason: str


class BulkIndexError(SearchIndexError):
    def __init__(self, failures: Collection[BulkFailure]) -> None:
        self.failures = tuple(failures)
        summary = "; ".join(
            f"id={failure.document_id} status={failure.status}: {failure.reason}"
            for failure in self.failures
        )
        super().__init__(f"Elasticsearch bulk indexing failed: {summary}")


class DocumentSearchIndex:
    def __init__(
        self,
        client: AsyncElasticsearch,
        index_name: str,
        result_limit: int,
    ) -> None:
        self._client = client
        self.index_name = index_name
        self.result_limit = result_limit

    async def ensure_exists(self) -> None:
        if not await self._client.indices.exists(index=self.index_name):
            try:
                await self._client.indices.create(
                    index=self.index_name,
                    mappings=INDEX_MAPPINGS,
                )
            except BadRequestError as error:
                if error.error != "resource_already_exists_exception":
                    raise

        await self._validate_mapping()

    async def upsert_many(self, documents: Collection[SearchDocument]) -> None:
        unique_documents = {document.id: document for document in documents}
        if not unique_documents:
            return

        operations: list[dict[str, object]] = []
        for document in unique_documents.values():
            operations.append({"index": {"_id": str(document.id)}})
            operations.append({"id": str(document.id), "text": document.text})

        response = await self._client.bulk(
            index=self.index_name,
            operations=operations,
        )
        body = _require_object(response.body, "bulk response")
        errors = body.get("errors")
        if not isinstance(errors, bool):
            raise SearchIndexError(
                "invalid Elasticsearch bulk response: 'errors' must be a boolean"
            )

        raw_items = body.get("items")
        if not isinstance(raw_items, list):
            raise SearchIndexError("invalid Elasticsearch bulk response: 'items' must be an array")
        failures = _extract_bulk_failures(raw_items)
        if not errors:
            if failures:
                raise SearchIndexError(
                    "invalid Elasticsearch bulk response: 'errors' contradicts item failures"
                )
            return
        if not failures:
            raise SearchIndexError(
                "invalid Elasticsearch bulk response: no failed items were returned"
            )

        raise BulkIndexError(failures)

    async def search_ids(self, query: str) -> list[UUID]:
        response = await self._client.search(
            index=self.index_name,
            query={
                "match": {
                    "text": {
                        "query": query,
                        "operator": "or",
                    }
                }
            },
            size=self.result_limit,
            source=False,
        )
        body = _require_object(response.body, "search response")
        hits_section = _require_object(body.get("hits"), "search response field 'hits'")
        raw_hits = hits_section.get("hits")
        if not isinstance(raw_hits, list):
            raise SearchIndexError(
                "invalid Elasticsearch search response: 'hits.hits' must be an array"
            )

        document_ids: list[UUID] = []
        for raw_hit in raw_hits:
            hit = _require_object(raw_hit, "search response hit")
            raw_id = hit.get("_id")
            if not isinstance(raw_id, str):
                raise SearchIndexError(
                    "invalid Elasticsearch search response: hit '_id' must be a string"
                )
            try:
                document_ids.append(UUID(raw_id))
            except ValueError as error:
                raise SearchIndexError(
                    f"search hit contains an invalid UUID: {raw_id!r}"
                ) from error
        return document_ids

    async def delete(self, document_id: UUID) -> bool:
        try:
            await self._client.delete(
                index=self.index_name,
                id=str(document_id),
            )
        except NotFoundError as error:
            if error.body.get("result") == "not_found":
                return False
            raise
        return True

    async def refresh(self) -> None:
        await self._client.indices.refresh(index=self.index_name)

    async def _validate_mapping(self) -> None:
        response = await self._client.indices.get_mapping(index=self.index_name)
        body = _require_object(response.body, "mapping response")
        index_definition = _require_object(
            body.get(self.index_name),
            f"mapping response for index {self.index_name!r}",
        )
        mappings = _require_object(
            index_definition.get("mappings"),
            f"mapping response for index {self.index_name!r} field 'mappings'",
        )
        properties = mappings.get("properties")
        id_field = properties.get("id") if isinstance(properties, dict) else None
        text_field = properties.get("text") if isinstance(properties, dict) else None

        compatible = (
            isinstance(properties, dict)
            and isinstance(id_field, dict)
            and isinstance(text_field, dict)
            and mappings.get("dynamic") == "strict"
            and set(properties) == {"id", "text"}
            and id_field.get("type") == "keyword"
            and text_field.get("type") == "text"
            and text_field.get("analyzer") == "russian"
            and text_field.get("index", True) is True
            and text_field.get("search_analyzer") in (None, "russian")
        )
        if not compatible:
            raise SearchIndexError(
                f"index {self.index_name!r} does not match the required document mapping"
            )


def get_document_search_index(request: Request) -> DocumentSearchIndex:
    return cast(DocumentSearchIndex, request.app.state.document_search_index)


def _require_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SearchIndexError(f"invalid Elasticsearch {context}: expected an object")
    return cast(dict[str, object], value)


def _extract_bulk_failures(items: list[object]) -> list[BulkFailure]:
    failures: list[BulkFailure] = []
    for raw_item in items:
        item = _require_object(raw_item, "bulk response item")
        operation = _require_object(item.get("index"), "bulk response item field 'index'")
        error = operation.get("error")
        if error is None:
            continue
        error_details = _require_object(error, "bulk response item field 'error'")
        document_id = operation.get("_id")
        status = operation.get("status")
        reason = error_details.get("reason")
        if (
            not isinstance(document_id, str)
            or type(status) is not int
            or not isinstance(reason, str)
        ):
            raise SearchIndexError(
                "invalid Elasticsearch bulk response: failed item fields have invalid types"
            )
        failures.append(
            BulkFailure(
                document_id=document_id,
                status=status,
                reason=reason,
            )
        )
    return failures
