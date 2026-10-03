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
        if not response.body["errors"]:
            return

        raise BulkIndexError(_extract_bulk_failures(response.body["items"]))

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
        document_ids: list[UUID] = []
        for hit in response.body["hits"]["hits"]:
            raw_id = hit["_id"]
            try:
                document_ids.append(UUID(raw_id))
            except (AttributeError, TypeError, ValueError) as error:
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
        mappings = response.body[self.index_name]["mappings"]
        properties = mappings["properties"]
        id_field = properties["id"]
        text_field = properties["text"]

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
            raise SearchIndexError(
                f"index {self.index_name!r} does not match the required document mapping"
            )


def get_document_search_index(request: Request) -> DocumentSearchIndex:
    return cast(DocumentSearchIndex, request.app.state.document_search_index)


def _extract_bulk_failures(items: list[dict[str, dict[str, object]]]) -> list[BulkFailure]:
    failures: list[BulkFailure] = []
    for item in items:
        operation = item["index"]
        error = operation.get("error")
        if error is None:
            continue
        failures.append(
            BulkFailure(
                document_id=cast(str, operation["_id"]),
                status=cast(int, operation["status"]),
                reason=cast(dict[str, str], error)["reason"],
            )
        )
    return failures
