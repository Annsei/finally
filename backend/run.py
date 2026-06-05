"""Development runner for FinAlly backend.

Usage:
    uv run run.py

This is a convenience script for local development with hot-reload.
In production, run via Docker using uvicorn directly.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
