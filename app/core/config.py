"""Environment-driven application settings."""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported runtime environments."""

    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    name: str = "document-search-api"
    version: str = "0.1.0"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = (
        "postgresql+asyncpg://document_search:document_search_local@127.0.0.1:5432/document_search"
    )
    elasticsearch_url: str = "http://127.0.0.1:9200"
    elasticsearch_index: str = "documents"
    search_result_limit: PositiveInt = 20


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process."""
    return Settings()
