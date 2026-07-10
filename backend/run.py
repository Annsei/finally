"""Development runner for FinAlly backend.

Usage:
    uv run run.py

This is a convenience script for local development with hot-reload.
In production, run via Docker using uvicorn directly.
"""

import uvicorn

from app.settings import LOCAL_DEMO, RuntimeSettings

if __name__ == "__main__":
    settings = RuntimeSettings.from_env().validate()
    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=8000,
        reload=settings.mode == LOCAL_DEMO,
    )
