"""Tests for external-client application lifecycle."""

from unittest.mock import AsyncMock

import pytest

from app.db.session import Database
from app.main import create_app
from app.search.client import ElasticsearchConnection
from app.search.index import DocumentSearchIndex


async def test_lifespan_prepares_index_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup validates Elasticsearch and shutdown releases both clients."""
    ensure_index = AsyncMock()
    close_elasticsearch = AsyncMock()
    dispose_database = AsyncMock()
    monkeypatch.setattr(DocumentSearchIndex, "ensure_exists", ensure_index)
    monkeypatch.setattr(ElasticsearchConnection, "close", close_elasticsearch)
    monkeypatch.setattr(Database, "dispose", dispose_database)
    application = create_app()

    async with application.router.lifespan_context(application):
        ensure_index.assert_awaited_once_with()
        close_elasticsearch.assert_not_awaited()
        dispose_database.assert_not_awaited()

    close_elasticsearch.assert_awaited_once_with()
    dispose_database.assert_awaited_once_with()
