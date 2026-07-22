# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation.

## Structure

- `app/` - Application code
  - `market/` - Market data subsystem
    - `models.py` - PriceUpdate dataclass
    - `cache.py` - Thread-safe price cache
    - `interface.py` - MarketDataSource abstract interface
    - `simulator.py` - GBM-based market simulator
    - `massive_client.py` - Massive/Polygon.io API client
    - `factory.py` - Data source factory
    - `stream.py` - SSE streaming endpoint
    - `seed_prices.py` - Default ticker prices and parameters

- `tests/` - Unit and integration tests
  - `market/` - Market data tests

## Running Tests

```bash
# Install dependencies
uv sync --dev

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/market/test_simulator.py

# Run with verbose output
uv run pytest -v
```

## Environment Variables

- `FINALLY_RUNTIME_MODE` — `local-demo` (default, loopback only) or
  `classroom-server` (shared, single-replica SQLite).
- `FINALLY_HOST` — bind host; defaults to `127.0.0.1` locally and `0.0.0.0`
  in classroom-server mode.
- `FINALLY_SERVER_AUTH_SECRET` — required in classroom-server mode; clients
  send it as `access_code` to `POST /api/auth/login`.
- `FINALLY_ADMIN_TOKEN` — season-reset administrator token, sent as
  `X-FinAlly-Admin-Token`. Mandatory in classroom-server mode; local-demo
  works without it (a supplied token is still validated). Season reset
  always requires an `Idempotency-Key` header in every mode.
- `FINALLY_SINGLE_REPLICA=true` — required in classroom-server mode. Multiple
  replicas are unsupported because market state is in memory and SQLite is
  the persistence layer.
- `FINALLY_MAX_BEARER_BODY_BYTES` — maximum audited Bearer request body
  (default 65536).
- `FINALLY_QUOTE_MAX_AGE_SECONDS` — quote freshness limit for new fills and
  readiness (default 45 seconds).
- `FINALLY_MARKET` — `us` (default) or `cn`. Massive currently supports only
  the US profile.
- `MASSIVE_API_KEY` — optional real US market data; absent means simulator.
- `DB_PATH` — SQLite database path; classroom-server mode requires persistent
  storage.

`GET /api/health` is the liveness endpoint. `GET /api/ready` additionally
checks that tracked market quotes are present and fresh while the market is
open.

## Development

```bash
# Install dependencies
uv sync --dev

# Run linter
uv run ruff check .

# Format code
uv run ruff format .
```
