from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.version import get_application_version
from app.db.session import Database
from app.search.client import ElasticsearchConnection
from app.search.index import DocumentSearchIndex


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    database = Database(app_settings.database_url)
    elasticsearch = ElasticsearchConnection(
        app_settings.elasticsearch_url,
        request_timeout_seconds=app_settings.elasticsearch_request_timeout_seconds,
        max_retries=app_settings.elasticsearch_max_retries,
        retry_on_timeout=app_settings.elasticsearch_retry_on_timeout,
    )
    document_search_index = DocumentSearchIndex(
        elasticsearch.client,
        app_settings.elasticsearch_index,
        app_settings.search_result_limit,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        try:
            await document_search_index.ensure_exists()
            yield
        finally:
            try:
                await elasticsearch.close()
            finally:
                await database.dispose()

    application = FastAPI(
        title=app_settings.name,
        version=get_application_version(),
        debug=app_settings.debug,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.database = database
    application.state.elasticsearch = elasticsearch
    application.state.document_search_index = document_search_index
    application.include_router(api_router)
    return application


app = create_app()
