from httpx import ASGITransport, AsyncClient

from app.core.version import get_application_version
from app.main import create_app


async def test_health_check() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


async def test_health_is_in_openapi_schema() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        schema = (await client.get("/openapi.json")).json()

    assert "/health" in schema["paths"]
    assert schema["info"]["version"] == get_application_version()
