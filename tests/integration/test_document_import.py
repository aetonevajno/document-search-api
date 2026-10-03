import csv
import os
from io import StringIO
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db.models import Document
from app.db.repositories import DocumentRepository
from app.db.session import Database
from app.importers.csv_parser import parse_document_csv_stream
from app.importers.service import DocumentImportService, PostgreSQLDocumentSink
from app.search.client import ElasticsearchConnection
from app.search.index import DocumentSearchIndex

pytestmark = pytest.mark.integration


async def test_repeated_csv_import_is_idempotent_across_both_backends() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    elasticsearch_url = os.getenv("TEST_ELASTICSEARCH_URL")
    index_name = os.getenv("TEST_ELASTICSEARCH_INDEX")
    if database_url is None or elasticsearch_url is None or index_name is None:
        pytest.skip(
            "TEST_DATABASE_URL, TEST_ELASTICSEARCH_URL, and TEST_ELASTICSEARCH_INDEX "
            "are required for importer integration tests"
        )

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("integration tests require a database name ending in '_test'")
    if not index_name.endswith("-test"):
        pytest.fail("integration tests require an Elasticsearch index ending in '-test'")

    marker = f"importintegration{uuid4().hex}"
    expected_text = f"Проверка импорта {marker}\nВторая строка документа"
    expected_rubrics = ["интеграция", "CSV, multiline"]
    source = StringIO()
    writer = csv.writer(source, lineterminator="\n")
    writer.writerow(["text", "created_date", "rubrics"])
    writer.writerow(
        [
            expected_text,
            "2020-02-03 04:05:06",
            repr(expected_rubrics),
        ]
    )
    source.seek(0)
    plan = parse_document_csv_stream(source)
    document = plan.documents[0]

    settings = Settings()
    database = Database(database_url)
    connection = ElasticsearchConnection(
        elasticsearch_url,
        request_timeout_seconds=settings.elasticsearch_request_timeout_seconds,
        max_retries=settings.elasticsearch_max_retries,
        retry_on_timeout=settings.elasticsearch_retry_on_timeout,
    )
    search_index = DocumentSearchIndex(connection.client, index_name, result_limit=20)
    index_existed: bool | None = None

    try:
        index_existed = bool(await connection.client.indices.exists(index=index_name))
        async with database.session_factory.begin() as session:
            await session.execute(delete(Document).where(Document.id == document.id))
        if index_existed:
            await search_index.delete(document.id)

        importer = DocumentImportService(
            PostgreSQLDocumentSink(database),
            search_index,
            batch_size=1,
        )
        first_result = await importer.import_plan(plan)
        second_result = await importer.import_plan(plan)

        assert first_result == second_result
        assert first_result.rows_read == 1
        assert first_result.unique_documents == 1
        assert first_result.duplicate_rows == 0
        assert first_result.batches_processed == 1

        async with database.session_factory() as session:
            stored = await DocumentRepository(session).get(document.id)
            stored_count = await session.scalar(
                select(func.count()).select_from(Document).where(Document.id == document.id)
            )

        assert stored_count == 1
        assert stored is not None
        assert stored.id == document.id
        assert stored.text == expected_text
        assert stored.rubrics == expected_rubrics
        assert stored.created_date == document.created_date
        assert stored.created_date.tzinfo is None

        assert await search_index.search_ids(marker) == [document.id]
        indexed_response = await connection.client.get(
            index=index_name,
            id=str(document.id),
        )
        indexed_body = indexed_response.body
        assert indexed_body["_id"] == str(document.id)
        assert indexed_body["_source"] == {
            "id": str(document.id),
            "text": expected_text,
        }
    finally:
        try:
            async with database.session_factory.begin() as session:
                await session.execute(delete(Document).where(Document.id == document.id))
        finally:
            try:
                if index_existed is not None and await connection.client.indices.exists(
                    index=index_name
                ):
                    if index_existed:
                        await search_index.delete(document.id)
                    else:
                        await connection.client.indices.delete(index=index_name)
            finally:
                try:
                    await connection.close()
                finally:
                    await database.dispose()
