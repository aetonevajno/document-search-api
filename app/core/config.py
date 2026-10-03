"""Environment-driven application settings"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    name: str = "document-search-api"
    debug: bool = False
    database_url: str = (
        "postgresql+asyncpg://document_search:document_search_local@127.0.0.1:5432/document_search"
    )
    elasticsearch_url: str = "http://127.0.0.1:9200"
    elasticsearch_index: str = "documents"
    elasticsearch_request_timeout_seconds: float = Field(default=10.0, gt=0)
    elasticsearch_max_retries: int = Field(default=3, ge=0)
    elasticsearch_retry_on_timeout: bool = True
    search_result_limit: int = Field(default=20, ge=1, le=20)
    import_batch_size: int = Field(default=500, ge=1, le=5000)


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process"""
    return Settings()
