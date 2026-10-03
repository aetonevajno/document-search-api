from elasticsearch import AsyncElasticsearch


class ElasticsearchConnection:
    def __init__(
        self,
        url: str,
        *,
        request_timeout_seconds: float,
        max_retries: int,
        retry_on_timeout: bool,
    ) -> None:
        self.client = AsyncElasticsearch(
            url,
            request_timeout=request_timeout_seconds,
            max_retries=max_retries,
            retry_on_timeout=retry_on_timeout,
        )

    async def close(self) -> None:
        await self.client.close()
