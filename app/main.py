"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application instance without external-service side effects."""
    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.name,
        version=app_settings.version,
        debug=app_settings.debug,
    )
    application.state.settings = app_settings
    application.include_router(api_router)
    return application


app = create_app()
