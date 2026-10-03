"""Tests for environment-driven settings."""

import pytest

from app.core.config import Settings


def test_dependency_settings_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Container service addresses are loaded from environment variables."""
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        "postgresql+asyncpg://app:password@postgres:5432/app",
    )
    monkeypatch.setenv("APP_ELASTICSEARCH_URL", "http://elasticsearch:9200")
    monkeypatch.setenv("APP_ELASTICSEARCH_INDEX", "test-documents")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://app:password@postgres:5432/app"
    assert settings.elasticsearch_url == "http://elasticsearch:9200"
    assert settings.elasticsearch_index == "test-documents"
