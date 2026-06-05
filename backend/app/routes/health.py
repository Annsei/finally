"""Health check endpoint for FinAlly.

Used by Docker health checks and load balancers to verify the service is running.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return service health status.

    Returns:
        JSON object with ``status`` key set to ``"ok"``.
    """
    return {"status": "ok"}
