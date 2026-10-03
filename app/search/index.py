"""Document index operations backed by Elasticsearch"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from elastic_transport import ObjectApiResponse
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
    """Base error for invalid index state or Elasticsearch responses"""


class IncompatibleIndexError(SearchIndexError):
    """The configured index exists with an incompatible mapping"""


class InvalidSearchResponseError(SearchIndexError):
    """Elasticsearch returned a response that violates the adapter contract"""


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """The subset of a document stored in Elasticsearch"""

    id: UUID
    text: str


@dataclass(frozen=True, slots=True)
class BulkFailure:
    """One failed item from an Elasticsearch bulk response"""

    document_id: str
    status: int | None
    reason: str


class BulkIndexError(SearchIndexError):
    """At least one item in a bulk indexing request failed"""

    def __init__(self, failures: Collection[BulkFailure]) -> None:
        self.failures = tuple(failures)
        summary = "; ".join(
            f"id={failure.document_id} status={failure.status}: {failure.reason}"
            for failure in self.failures
        )
        super().__init__(f"Elasticsearch bulk indexing failed: {summary}")


class DocumentSearchIndex:
    """Index, search, and delete the searchable subset of documents"""

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
        """Create the index if needed and reject incompatible mappings"""
        exists = bool(await self._client.indices.exists(index=self.index_name))
        if not exists:
            try:
                await self._client.indices.create(
                    index=self.index_name,
                    mappings=INDEX_MAPPINGS,
                )
            except BadRequestError as error:
                if not _is_resource_already_exists(error):
                    raise

        await self._validate_mapping()

    async def upsert_many(self, documents: Collection[SearchDocument]) -> None:
        """Index a batch by UUID without refreshing or hiding item failures"""
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
        body = _response_body(response, "bulk response")
        errors = body.get("errors")
        if errors is False:
            return
        if errors is not True:
            raise InvalidSearchResponseError("bulk response has no boolean 'errors' field")

        failures = _extract_bulk_failures(body.get("items"))
        if not failures:
            raise InvalidSearchResponseError(
                "bulk response reports errors but contains no failed items"
            )
        raise BulkIndexError(failures)

    async def search_ids(self, query: str) -> list[UUID]:
        """Return UUIDs in Elasticsearch relevance order up to the configured limit"""
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
        body = _response_body(response, "search response")
        hits_container = _require_mapping(body.get("hits"), "search response 'hits'")
        raw_hits = hits_container.get("hits")
        if not isinstance(raw_hits, list):
            raise InvalidSearchResponseError("search response 'hits.hits' is not a list")

        document_ids: list[UUID] = []
        for raw_hit in raw_hits:
            hit = _require_mapping(raw_hit, "search hit")
            raw_id = hit.get("_id")
            if not isinstance(raw_id, str):
                raise InvalidSearchResponseError("search hit has no string '_id'")
            try:
                document_ids.append(UUID(raw_id))
            except ValueError as error:
                raise InvalidSearchResponseError(
                    f"search hit contains an invalid UUID: {raw_id!r}"
                ) from error
        return document_ids

    async def delete(self, document_id: UUID) -> bool:
        """Delete by UUID and report whether the indexed document existed"""
        try:
            response = await self._client.delete(
                index=self.index_name,
                id=str(document_id),
            )
        except NotFoundError as error:
            if _is_document_not_found(error):
                return False
            raise

        body = _response_body(response, "delete response")
        result = body.get("result")
        if result == "deleted":
            return True
        if result == "not_found":
            return False
        raise InvalidSearchResponseError(
            f"delete response contains an unsupported result: {result!r}"
        )

    async def refresh(self) -> None:
        """Make all completed indexing operations visible to search"""
        await self._client.indices.refresh(index=self.index_name)

    async def _validate_mapping(self) -> None:
        response = await self._client.indices.get_mapping(index=self.index_name)
        body = _response_body(response, "get mapping response")
        index_definition = _require_mapping(
            body.get(self.index_name),
            f"mapping for index {self.index_name!r}",
        )
        mappings = _require_mapping(index_definition.get("mappings"), "index mappings")
        properties = _require_mapping(mappings.get("properties"), "index properties")
        id_field = _require_mapping(properties.get("id"), "mapping for field 'id'")
        text_field = _require_mapping(properties.get("text"), "mapping for field 'text'")

        compatible = (
            mappings.get("dynamic") == "strict"
            and set(properties) == {"id", "text"}
            and id_field.get("type") == "keyword"
            and text_field.get("type") == "text"
            and text_field.get("analyzer") == "russian"
            and text_field.get("index", True) is True
            and text_field.get("search_analyzer") in (None, "russian")
        )
        if not compatible:
            raise IncompatibleIndexError(
                f"index {self.index_name!r} does not match the required document mapping"
            )


def get_document_search_index(request: Request) -> DocumentSearchIndex:
    """Return the search index owned by the current application"""
    return cast(DocumentSearchIndex, request.app.state.document_search_index)


def _response_body(
    response: ObjectApiResponse[Any],
    context: str,
) -> Mapping[str, object]:
    return _require_mapping(cast(object, response.body), context)


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidSearchResponseError(f"{context} is not an object")
    return cast(Mapping[str, object], value)


def _is_resource_already_exists(error: BadRequestError) -> bool:
    info = cast(object, error.info)
    if not isinstance(info, Mapping):
        return False
    error_details = info.get("error")
    if not isinstance(error_details, Mapping):
        return False
    return error_details.get("type") == "resource_already_exists_exception"


def _is_document_not_found(error: NotFoundError) -> bool:
    info = cast(object, error.info)
    return isinstance(info, Mapping) and info.get("result") == "not_found"


def _extract_bulk_failures(raw_items: object) -> list[BulkFailure]:
    if not isinstance(raw_items, list):
        raise InvalidSearchResponseError("bulk response 'items' is not a list")

    failures: list[BulkFailure] = []
    for raw_item in raw_items:
        item = _require_mapping(raw_item, "bulk response item")
        if len(item) != 1:
            raise InvalidSearchResponseError("bulk response item has an invalid shape")
        operation = _require_mapping(next(iter(item.values())), "bulk operation result")
        if "error" not in operation:
            continue

        raw_status = operation.get("status")
        status = (
            raw_status if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
        )
        raw_document_id = operation.get("_id")
        document_id = raw_document_id if isinstance(raw_document_id, str) else "unknown"
        error_details = operation.get("error")
        reason = "unknown Elasticsearch error"
        if isinstance(error_details, Mapping):
            raw_reason = error_details.get("reason")
            if isinstance(raw_reason, str):
                reason = raw_reason
        elif isinstance(error_details, str):
            reason = error_details

        failures.append(
            BulkFailure(
                document_id=document_id,
                status=status,
                reason=reason,
            )
        )
    return failures
