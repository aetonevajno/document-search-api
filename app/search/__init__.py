"""Elasticsearch integration package"""

from app.search.client import ElasticsearchConnection
from app.search.index import (
    BulkFailure,
    BulkIndexError,
    DocumentSearchIndex,
    IncompatibleIndexError,
    InvalidSearchResponseError,
    SearchDocument,
    SearchIndexError,
)

__all__ = [
    "BulkFailure",
    "BulkIndexError",
    "DocumentSearchIndex",
    "ElasticsearchConnection",
    "IncompatibleIndexError",
    "InvalidSearchResponseError",
    "SearchDocument",
    "SearchIndexError",
]
