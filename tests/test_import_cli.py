"""Tests for the document import command-line boundary"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from elastic_transport import TransportError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.importers.cli import _run_import, main
from app.importers.csv_parser import ImportPlan
from app.importers.service import ImportResult


def test_cli_reports_missing_file_without_opening_storage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Local file errors produce a concise non-zero result before connections"""
    missing_path = tmp_path / "missing.csv"

    exit_code = main([str(missing_path)], settings=Settings())

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Import failed:" in captured.err
    assert str(missing_path) in captured.err


def test_cli_prints_stable_success_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """Operators receive counts useful for verifying an idempotent import"""
    plan = ImportPlan(rows_read=3, documents=())
    result = ImportResult(
        rows_read=3,
        unique_documents=2,
        duplicate_rows=1,
        batches_processed=1,
    )
    with (
        patch("app.importers.cli.parse_document_csv", return_value=plan),
        patch("app.importers.cli._run_import", new=AsyncMock(return_value=result)) as run,
    ):
        exit_code = main(["posts.csv"], settings=Settings())

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == ("Import completed: rows=3, unique=2, duplicates=1, batches=1.\n")
    run.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        SQLAlchemyError("database unavailable"),
        TransportError("search unavailable"),
        RuntimeError("bulk failed"),
    ],
)
def test_cli_reports_storage_failures_without_traceback(
    error: Exception,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expected backend failures become a concise exit-code-one result"""
    plan = ImportPlan(rows_read=0, documents=())
    with (
        patch("app.importers.cli.parse_document_csv", return_value=plan),
        patch("app.importers.cli._run_import", new=AsyncMock(side_effect=error)),
    ):
        exit_code = main(["posts.csv"], settings=Settings())

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.err == f"Import failed: {error}\n"
    assert "Traceback" not in captured.err


async def test_run_import_always_closes_created_resources() -> None:
    """Both clients close even when the import fails after construction"""
    plan = ImportPlan(rows_read=0, documents=())
    database = MagicMock()
    database.dispose = AsyncMock()
    elasticsearch = MagicMock()
    elasticsearch.close = AsyncMock()
    service = MagicMock()
    service.import_plan = AsyncMock(side_effect=RuntimeError("import failed"))

    with (
        patch("app.importers.cli.Database", return_value=database),
        patch("app.importers.cli.ElasticsearchConnection", return_value=elasticsearch),
        patch("app.importers.cli.DocumentSearchIndex"),
        patch("app.importers.cli.PostgreSQLDocumentSink"),
        patch("app.importers.cli.DocumentImportService", return_value=service),
        pytest.raises(RuntimeError, match="import failed"),
    ):
        await _run_import(plan, Settings())

    elasticsearch.close.assert_awaited_once_with()
    database.dispose.assert_awaited_once_with()
