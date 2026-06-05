"""Tests for the /api/health endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestHealthEndpoint:
    """Integration tests for the health check endpoint."""

    async def test_health_returns_ok(self, app_client):
        """GET /api/health returns 200 with {"status": "ok"}."""
        response = await app_client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_health_content_type_json(self, app_client):
        """GET /api/health response Content-Type is application/json."""
        response = await app_client.get("/api/health")
        assert "application/json" in response.headers["content-type"]
