<!-- refreshed: 2026-06-04 -->
# Testing

**Analysis Date:** 2026-06-04

## Framework & Tooling

| Tool | Version | Purpose |
|------|---------|---------|
| pytest | >=8.3 | Test runner |
| pytest-asyncio | >=0.24 | Async test support |
| pytest-cov | >=5.0 | Coverage reporting |
| httpx | >=0.27 | ASGI client for FastAPI integration tests |
| ruff | >=0.7 | Linting (separate from tests, same dev deps) |

Configuration in `backend/pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

`asyncio_mode = "auto"` means all async test functions run under an event loop automatically — no decorator needed per-function.

## File Organization

Tests mirror the `app/` structure exactly:

```
backend/
├── app/
│   └── market/
│       ├── cache.py
│       ├── factory.py
│       ├── massive_client.py
│       ├── models.py
│       ├── simulator.py
│       ├── stream.py
│       └── seed_prices.py
└── tests/
    ├── conftest.py               ← Shared fixtures (currently minimal)
    └── market/
        ├── test_cache.py         ← PriceCache unit tests
        ├── test_factory.py       ← Factory env-var switching
        ├── test_massive.py       ← MassiveDataSource (mocked external API)
        ├── test_models.py        ← PriceUpdate dataclass
        ├── test_simulator.py     ← GBMSimulator math unit tests
        ├── test_simulator_source.py ← SimulatorDataSource async integration
        └── test_stream.py        ← SSE endpoint integration tests
```

75+ test functions across 7 test files. All passing as of last run.

## Test Class Structure

Tests use `TestClassName` classes (no pytest class fixtures — all setup is inline):

```python
class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert cache.get("AAPL") == update
```

- Each test docstring describes what it verifies
- AAA (Arrange-Act-Assert) inline — no shared state between tests
- Fresh instance per test (no class-level or module-level setup)

## Async Testing Patterns

Async tests use class-level `@pytest.mark.asyncio` (or rely on `asyncio_mode = "auto"`):

```python
@pytest.mark.asyncio
class TestSimulatorSource:
    async def test_start_stop(self):
        cache = PriceCache()
        source = SimulatorDataSource(cache, interval=0.05)
        await source.start(["AAPL"])
        await asyncio.sleep(0.1)
        await source.stop()
        assert cache.get("AAPL") is not None
```

Key patterns:
- **Short real intervals** (0.05–0.1s) rather than mocked time — tests actual async behavior
- **Always `await source.stop()`** in cleanup — prevents background tasks leaking between tests
- **`asyncio.wait_for(..., timeout=N)`** for SSE collection tests to prevent hangs

## Mocking Strategy

Uses `unittest.mock` exclusively — no third-party mocking libraries:

| Scenario | Pattern | Example location |
|----------|---------|-----------------|
| Environment variables | `patch.dict(os.environ, {...}, clear=True)` | `test_factory.py` |
| External API client methods | `patch.object(source, '_fetch_snapshots', return_value=...)` | `test_massive.py` |
| External API objects | `MagicMock` for `massive` library client | `test_massive.py` |

```python
def test_creates_simulator_when_no_api_key(self):
    cache = PriceCache()
    with patch.dict(os.environ, {}, clear=True):
        source = create_market_data_source(cache)
    assert isinstance(source, SimulatorDataSource)
```

## SSE Integration Tests

SSE streaming tested end-to-end using in-process FastAPI (no network):

```python
async def test_stream_delivers_price_update(self):
    cache = PriceCache()
    app = FastAPI()
    app.include_router(create_stream_router(cache))

    received: list[str] = []

    async def collect() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("GET", "/api/stream/prices") as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        received.append(line)
                        return

    cache.update("AAPL", 190.0)
    await asyncio.wait_for(collect(), timeout=3.0)
    assert len(received) >= 1
```

Key: `httpx.ASGITransport` mounts the FastAPI app directly — no server process needed.

## Running Tests

```bash
cd backend
uv run pytest                          # All tests
uv run pytest tests/market/test_stream.py  # Single file
uv run pytest -v                       # Verbose output
uv run pytest --cov=app --cov-report=term-missing  # With coverage
```

## Coverage

Configured via `pyproject.toml`:
```toml
[tool.coverage.run]
source = ["app"]
omit = ["tests/*"]

[tool.coverage.report]
exclude_lines = [
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    ...
]
```

No minimum coverage threshold enforced. Coverage is measured but not gated in CI (as of current state — planned to add).

## Test Patterns to Follow

**No-op tests:** Verify idempotent operations are safe to repeat:
```python
async def test_stop_without_start_is_safe(self):
    source = SimulatorDataSource(cache)
    await source.stop()  # Should not raise
```

**Resilience tests:** Verify errors are swallowed in background tasks:
```python
async def test_poll_failure_does_not_crash_loop(self):
    # Poll fails but loop continues
```

**State isolation:** Always create fresh instances per test — never reuse or share `PriceCache`, `SimulatorDataSource`, etc.

## What's Not Tested Yet

- FastAPI app entry point (doesn't exist yet)
- Database layer (not implemented)
- Portfolio/trade/watchlist/chat routes (not implemented)
- LLM integration (not implemented)
- Frontend components (not implemented)
- E2E Playwright tests (infrastructure planned in `test/`)

---

*Testing analysis: 2026-06-04*
