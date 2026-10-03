"""Tests for environment-driven settings"""

import pytest

from app.core.config import Settings


def test_dependency_settings_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Container service addresses are loaded from environment variables"""
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        "postgresql+asyncpg://app:password@postgres:5432/app",
    )
    monkeypatch.setenv("APP_ELASTICSEARCH_URL", "http://elasticsearch:9200")
    monkeypatch.setenv("APP_ELASTICSEARCH_INDEX", "test-documents")
    monkeypatch.setenv("APP_ELASTICSEARCH_REQUEST_TIMEOUT_SECONDS", "15.5")
    monkeypatch.setenv("APP_ELASTICSEARCH_MAX_RETRIES", "5")
    monkeypatch.setenv("APP_ELASTICSEARCH_RETRY_ON_TIMEOUT", "false")
    monkeypatch.setenv("APP_SEARCH_RESULT_LIMIT", "7")
    monkeypatch.setenv("APP_IMPORT_BATCH_SIZE", "250")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://app:password@postgres:5432/app"
    assert settings.elasticsearch_url == "http://elasticsearch:9200"
    assert settings.elasticsearch_index == "test-documents"
    assert settings.elasticsearch_request_timeout_seconds == 15.5
    assert settings.elasticsearch_max_retries == 5
    assert settings.elasticsearch_retry_on_timeout is False
    assert settings.search_result_limit == 7
    assert settings.import_batch_size == 250


@pytest.mark.parametrize("value", ["0", "-1", "21"])
def test_search_result_limit_must_match_contract(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The configured limit must remain within the assignment contract"""
    monkeypatch.setenv("APP_SEARCH_RESULT_LIMIT", value)

    with pytest.raises(ValueError):
        Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ELASTICSEARCH_REQUEST_TIMEOUT_SECONDS", "0"),
        ("APP_ELASTICSEARCH_MAX_RETRIES", "-1"),
        ("APP_IMPORT_BATCH_SIZE", "0"),
        ("APP_IMPORT_BATCH_SIZE", "5001"),
    ],
)
def test_elasticsearch_transport_settings_are_validated(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    """Invalid operational values fail before external clients are created"""
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        Settings()
