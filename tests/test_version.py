from importlib.metadata import version

from app.core.version import PACKAGE_NAME, get_application_version


def test_application_version_matches_installed_distribution() -> None:
    assert get_application_version() == version(PACKAGE_NAME)
