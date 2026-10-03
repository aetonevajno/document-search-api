"""Application version derived from installed package metadata."""

from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "document-search-api"
UNKNOWN_VERSION = "0.0.0+unknown"


def get_application_version() -> str:
    """Return the installed distribution version with a source-tree fallback."""
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION
