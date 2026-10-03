"""Tests for Elasticsearch client construction and cleanup."""

from unittest.mock import AsyncMock, patch

from app.search.client import ElasticsearchConnection


async def test_connection_applies_transport_settings_and_closes() -> None:
    """Validated runtime settings are forwarded to the official client."""
    with patch("app.search.client.AsyncElasticsearch") as client_class:
        client_class.return_value.close = AsyncMock()
        connection = ElasticsearchConnection(
            "http://elasticsearch:9200",
            request_timeout_seconds=12.5,
            max_retries=4,
            retry_on_timeout=True,
        )

        client_class.assert_called_once_with(
            "http://elasticsearch:9200",
            request_timeout=12.5,
            max_retries=4,
            retry_on_timeout=True,
        )
        await connection.close()
        client_class.return_value.close.assert_awaited_once_with()
