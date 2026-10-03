"""Regression tests for the checked-in OpenAPI deliverable"""

import json
from pathlib import Path
from typing import cast

from app.main import create_app


def test_docs_json_matches_generated_openapi() -> None:
    """The assignment's static API document cannot drift from FastAPI"""
    docs_path = Path(__file__).resolve().parents[1] / "docs.json"
    documented = cast(
        dict[str, object],
        json.loads(docs_path.read_text(encoding="utf-8")),
    )

    assert documented == create_app().openapi()
