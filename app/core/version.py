from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "document-search-api"
UNKNOWN_VERSION = "0.0.0+unknown"


def get_application_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION
