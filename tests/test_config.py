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
    monkeypatch.setenv("APP_SEARCH_RESULT_LIMIT", "7")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://app:password@postgres:5432/app"
    assert settings.elasticsearch_url == "http://elasticsearch:9200"
    assert settings.elasticsearch_index == "test-documents"
    assert settings.search_result_limit == 7


@pytest.mark.parametrize("value", ["0", "-1"])
def test_search_result_limit_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """A non-positive limit is rejected during settings validation."""
    monkeypatch.setenv("APP_SEARCH_RESULT_LIMIT", value)

    with pytest.raises(ValueError, match="greater than 0"):
        Settings()
