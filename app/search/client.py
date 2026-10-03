"""Lifecycle owner for the asynchronous Elasticsearch client."""

from elasticsearch import AsyncElasticsearch


class ElasticsearchConnection:
    """Own one Elasticsearch client for the application lifetime."""

    def __init__(self, url: str) -> None:
        self.client = AsyncElasticsearch(url)

    async def close(self) -> None:
        """Close transports and any underlying HTTP connections."""
        await self.client.close()
