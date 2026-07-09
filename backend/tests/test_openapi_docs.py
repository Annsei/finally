"""P3 §7 — OpenAPI schema and Swagger UI exposed under /api/*.

The module-level app in app.main serves /api/openapi.json and /api/docs
(routes registered at construction — no lifespan startup needed), while the
old root paths are gone so the static frontend export owns them. ReDoc is
disabled.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app as main_app


@pytest_asyncio.fixture
async def docs_client(tmp_path, monkeypatch):
    """Client over the real module-level app WITHOUT running its lifespan.

    DB_PATH points at a temp file so even the gateway's env fallback could
    never touch a real database (no-Bearer requests bypass it anyway).
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "docs.db"))
    async with AsyncClient(
        transport=ASGITransport(app=main_app), base_url="http://test"
    ) as client:
        yield client


class TestOpenApiExposure:
    async def test_openapi_json_served_under_api(self, docs_client):
        resp = await docs_client.get("/api/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "FinAlly"
        assert schema["info"]["version"] == "0.1.0"

    async def test_root_openapi_json_404(self, docs_client):
        assert (await docs_client.get("/openapi.json")).status_code == 404

    async def test_swagger_ui_served_under_api_docs(self, docs_client):
        resp = await docs_client.get("/api/docs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "swagger" in resp.text.lower()

    async def test_root_docs_404(self, docs_client):
        assert (await docs_client.get("/docs")).status_code == 404

    async def test_redoc_disabled(self, docs_client):
        assert (await docs_client.get("/redoc")).status_code == 404
        assert (await docs_client.get("/api/redoc")).status_code == 404

    async def test_app_configuration(self):
        assert main_app.openapi_url == "/api/openapi.json"
        assert main_app.docs_url == "/api/docs"
        assert main_app.redoc_url is None

    async def test_health_route_still_in_schema(self, docs_client):
        schema = (await docs_client.get("/api/openapi.json")).json()
        assert "/api/health" in schema["paths"]
