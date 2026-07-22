#!/usr/bin/env python3
"""Export FinAlly's fully initialized OpenAPI schema without external calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("openapi.json"))
    args = parser.parse_args()

    # Import after installing a deterministic, isolated application environment.
    # Routers are registered during lifespan, so app.openapi() at bare import
    # time would silently export only the import-time health routes.
    with tempfile.TemporaryDirectory(prefix="finally-openapi-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "openapi.sqlite")
        os.environ["LLM_MOCK"] = "true"
        os.environ["FINALLY_RUNTIME_MODE"] = "local-demo"
        os.environ["FINALLY_HOST"] = "127.0.0.1"
        os.environ["FINALLY_MARKET"] = "us"
        os.environ.pop("MASSIVE_API_KEY", None)
        sys.path.insert(0, str(BACKEND))

        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/openapi.json")
            response.raise_for_status()
            schema = response.json()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(schema, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
