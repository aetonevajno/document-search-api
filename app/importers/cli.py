import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from elasticsearch import ApiError, TransportError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.db.session import Database
from app.importers.csv_parser import ImportPlan, parse_document_csv
from app.importers.service import (
    DocumentImportService,
    ImportResult,
    PostgreSQLDocumentSink,
)
from app.search.client import ElasticsearchConnection
from app.search.index import DocumentSearchIndex


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        plan = parse_document_csv(arguments.csv_path)
        result = asyncio.run(_run_import(plan, settings or get_settings()))
    except KeyboardInterrupt:
        print("Import interrupted.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, ApiError, TransportError, SQLAlchemyError) as error:
        print(f"Import failed: {error}", file=sys.stderr)
        return 1

    print(
        "Import completed: "
        f"rows={result.rows_read}, "
        f"unique={result.unique_documents}, "
        f"duplicates={result.duplicate_rows}, "
        f"batches={result.batches_processed}."
    )
    return 0


async def _run_import(plan: ImportPlan, settings: Settings) -> ImportResult:
    database = Database(settings.database_url)
    try:
        elasticsearch = ElasticsearchConnection(
            settings.elasticsearch_url,
            request_timeout_seconds=settings.elasticsearch_request_timeout_seconds,
            max_retries=settings.elasticsearch_max_retries,
            retry_on_timeout=settings.elasticsearch_retry_on_timeout,
        )
        try:
            search_index = DocumentSearchIndex(
                elasticsearch.client,
                settings.elasticsearch_index,
                settings.search_result_limit,
            )
            service = DocumentImportService(
                PostgreSQLDocumentSink(database),
                search_index,
                batch_size=settings.import_batch_size,
            )
            return await service.import_plan(plan)
        finally:
            await elasticsearch.close()
    finally:
        await database.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import-documents",
        description="Import documents from CSV into PostgreSQL and Elasticsearch.",
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="path to the source CSV file",
    )
    return parser
