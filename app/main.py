"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application instance without external-service side effects."""
    app_settings = settings or get_settings()
    database = Database(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await database.dispose()

    application = FastAPI(
        title=app_settings.name,
        version=app_settings.version,
        debug=app_settings.debug,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.database = database
    application.include_router(api_router)
    return application


app = create_app()
