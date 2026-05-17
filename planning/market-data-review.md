# Market Data Backend — Code Review

**Date:** 2026-05-17
**Reviewer:** Claude (claude-sonnet-4-6)
**Scope:** `backend/app/market/` (8 source modules) and `backend/tests/market/` (6 test modules)
**Prior Review:** `planning/archive/MARKET_DATA_REVIEW.md` (2026-02-10)

---

## 1. Test Execution

The test suite could not be executed independently in this environment — `uv` is not installed and `pip install` requires manual approval. Tests must be run locally or in CI using:

```bash
cd backend
uv sync --extra dev
uv run --extra dev pytest -v --cov=app
```

The prior review (2026-02-10) documented **73 tests, all passing, 84% overall coverage** after issue resolution. Code inspection confirms the fixes from that review have been applied. Test results referenced below are from that documented baseline.

---

## 2. Prior Review Issues — Status

All 7 issues from the 2026-02-10 review have been addressed:

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 3.1 | `pyproject.toml` missing hatchling wheel config | High | ✅ Fixed — `[tool.hatch.build.targets.wheel] packages = ["app"]` added |
| 3.2 | Massive test fragility (lazy import / `asyncio.to_thread` mock) | Medium | ✅ Resolved — `massive` imports are now top-level; tests mock `_fetch_snapshots` directly |
| 3.3 | `_generate_events` return type annotated as `None` | Low | ✅ Fixed — now annotated as `AsyncGenerator[str, None]` |
| 3.4 | `version` property reads `_version` without lock | Low | ⚠️ Not fixed — still unlocked (see §4.1) |
| 3.5 | `SimulatorDataSource.get_tickers()` accessed `_sim._tickers` (private) | Low | ✅ Fixed — `GBMSimulator.get_tickers()` public method added; `SimulatorDataSource` calls it |
| 3.6 | Module-level router accumulates routes on repeated calls | Low | ⚠️ Not fixed — still present (see §4.2) |
| 3.7 | Unused test imports (`pytest`, `math`, `asyncio`) | Trivial | ✅ Fixed — cleaned up across all test files |

Additionally: `DEFAULT_CORR` (mentioned in §4.3 of prior review as unused and confusing) has been removed from `seed_prices.py`. Only `CROSS_GROUP_CORR`, `INTRA_TECH_CORR`, `INTRA_FINANCE_CORR`, and `TSLA_CORR` remain, each clearly named.

---

## 3. Architecture Assessment

The architecture is sound and production-ready for this project's scope.

```
MarketDataSource (ABC)          interface.py
├── SimulatorDataSource    ←    simulator.py  (GBM + Cholesky correlation)
└── MassiveDataSource      ←    massive_client.py  (Polygon.io REST polling)
        │
        ▼
   PriceCache              ←    cache.py  (thread-safe, version-stamped)
        │
        ├──▶ GET /api/stream/prices (SSE)    stream.py
        ├──▶ Portfolio valuation
        └──▶ Trade execution
```

**Strengths:**

- **Strategy pattern with clean ABC.** `MarketDataSource` declares a minimal lifecycle interface (`start`, `stop`, `add_ticker`, `remove_ticker`, `get_tickers`). Downstream code is source-agnostic.
- **PriceCache as the single source of truth.** Producers write to it; consumers read from it. No direct coupling between data source and SSE stream.
- **Correct GBM math.** `S(t+dt) = S(t) * exp((μ - 0.5σ²)dt + σ√dt·Z)` produces log-normal price paths that can never go negative. The tiny `dt` (~8.5e-8) produces sub-cent per-tick moves that accumulate naturally.
- **Cholesky decomposition for correlated moves.** Sector-based correlation matrix (tech 0.6, finance 0.5, TSLA/cross 0.3) is mathematically correct and adds visual realism. Rebuilt on each ticker add/remove (O(n²), n < 50 — fine).
- **Shock events** (~0.1% per tick per ticker, 2–5% magnitude) add drama. With 10 tickers at 2 ticks/s, an event occurs roughly every 50 seconds on average.
- **Resilient background tasks.** Both `_run_loop` (simulator) and `_poll_loop` (Massive) catch all exceptions and continue. Correct for long-running background services.
- **SSE implementation.** Version-based change detection (`last_version != current_version`) avoids sending redundant payloads. `retry: 1000` directive ensures browser auto-reconnect. `X-Accel-Buffering: no` proactively disables nginx buffering.
- **Seed prices in cache at `start()`.** Frontend receives data on the first SSE poll with no visible delay.
- **Immutable `PriceUpdate`.** `frozen=True, slots=True` dataclass is correct and memory-efficient.
- **Factory cleanly selects source.** Whitespace-stripped `MASSIVE_API_KEY` check avoids common config mistakes.

---

## 4. Issues Found

### 4.1 `version` Property Reads Without Lock (Severity: Low — Carried from Prior Review)

**File:** `backend/app/market/cache.py:64–67`

```python
@property
def version(self) -> int:
    """Current version counter. Useful for SSE change detection."""
    return self._version
```

`self._version` is read without acquiring `self._lock`, while every write to it occurs under the lock. On CPython with the GIL, reading a plain `int` is atomic and this won't cause corruption. However, it is inconsistent with the rest of the class, and would become a real race on a free-threaded Python build (PEP 703, Python 3.13t+).

**Fix:**
```python
@property
def version(self) -> int:
    with self._lock:
        return self._version
```

---

### 4.2 Module-Level Router Instance (Severity: Low — Carried from Prior Review)

**File:** `backend/app/market/stream.py:17`

```python
router = APIRouter(prefix="/api/stream", tags=["streaming"])

def create_stream_router(price_cache: PriceCache) -> APIRouter:
    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        ...
    return router
```

`router` is a module-level singleton. Every call to `create_stream_router()` registers an additional `/prices` handler on the same object. In production this is called once at startup, so it's harmless. In test code (e.g., if tests create multiple app instances), duplicate route registration would cause confusing behaviour.

**Fix:** Move the router construction inside `create_stream_router()`:
```python
def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])
    @router.get("/prices")
    ...
    return router
```

---

### 4.3 `timestamp or time.time()` Falsy Semantics (Severity: Low — New)

**File:** `backend/app/market/cache.py:31`

```python
ts = timestamp or time.time()
```

Python's `or` operator evaluates `timestamp` for truthiness. A `timestamp` of `0.0` (Unix epoch) is falsy and would silently fall through to `time.time()`. The correct null check is:

```python
ts = timestamp if timestamp is not None else time.time()
```

In practice, timestamp 0.0 represents 1970-01-01 and will never be passed in this application, so this causes no real bug. Nevertheless, the intent is "use provided value if present", which `is not None` expresses correctly.

---

### 4.4 No SSE Streaming Tests (Severity: Low — Carried from Prior Review)

`stream.py` has ~31% test coverage (per prior review). The `_generate_events` async generator has no dedicated tests. The version-detection logic, `retry` directive, and disconnect handling are all untested.

A basic integration test using HTTPX's async client would cover the critical path:

```python
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

async def test_sse_stream_sends_prices():
    cache = PriceCache()
    cache.update("AAPL", 190.00)
    app = FastAPI()
    app.include_router(create_stream_router(cache))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as response:
            # Read first event
            ...
```

---

### 4.5 Test Accesses Private Attribute (Severity: Trivial)

**File:** `backend/tests/market/test_simulator.py:48`

```python
def test_add_duplicate_is_noop(self):
    sim = GBMSimulator(tickers=["AAPL"])
    sim.add_ticker("AAPL")
    assert len(sim._tickers) == 1  # private attribute access
```

Now that `GBMSimulator.get_tickers()` is a public method, the test should use it:

```python
assert len(sim.get_tickers()) == 1
```

---

### 4.6 `conftest.py` Fixture Likely Unused (Severity: Trivial)

**File:** `backend/tests/conftest.py`

```python
@pytest.fixture
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
```

With `pytest-asyncio` in `auto` mode (`asyncio_mode = "auto"` in `pyproject.toml`), the framework manages event loops automatically. This fixture overrides the loop policy only if a test explicitly requests it as a parameter, which none currently do. It is harmless but dead code.

---

## 5. Test Coverage Summary

Per prior review documentation (73 tests, all passing):

| Module | Coverage | Notes |
|--------|----------|-------|
| `models.py` | 100% | All properties, serialization, immutability |
| `cache.py` | 100% | Update, get, remove, version, threading |
| `interface.py` | 100% | ABC — covered by implementations |
| `seed_prices.py` | 100% | Data-only module |
| `factory.py` | 100% | All env var branches covered |
| `simulator.py` | 98% | Uncovered: duplicate-guard in `_add_ticker_internal`, exception log path in `_run_loop` |
| `massive_client.py` | 56% | Expected — real API paths require `massive` package and API key |
| `stream.py` | 31% | No ASGI test; SSE generator untested |
| **Overall** | **84%** | |

---

## 6. Test Quality Assessment

The test suite is well-structured and covers the most important cases:

- **`test_models.py` (11 tests):** Thorough — creation, change calculation (positive/negative), percentage change (including zero-division edge case), direction, serialization, immutability.
- **`test_cache.py` (13 tests):** Complete — first-update semantics (flat), direction, remove, get_all, version counter, convenience methods, `__len__`, `__contains__`, custom timestamp, price rounding.
- **`test_simulator.py` (17 tests):** Good coverage — positive prices invariant (10,000 iterations), add/remove tickers, Cholesky matrix rebuilding, correlation values, edge cases (empty, unknown ticker, duplicate add, nonexistent remove).
- **`test_simulator_source.py` (10 tests):** Integration tests with real asyncio tasks — start populates cache, prices update over time, stop is idempotent, add/remove tickers, empty start, exception resilience, custom interval.
- **`test_factory.py` (7 tests):** All environment variable branches, whitespace/empty key handling, cache injection verification.
- **`test_massive.py` (13 tests):** Comprehensive mock-based tests — poll updates cache, malformed snapshot skipping, API error resilience, timestamp conversion (ms→s), add/remove with normalization, empty-tickers guard, stop cancellation, immediate first poll.

One notable gap: no concurrent write test for `PriceCache`. The locking code is correct by inspection, but a test with multiple threads writing simultaneously would verify it empirically.

---

## 7. Verdict

**The market data backend is production-ready for integration with the rest of the FinAlly platform.** The architecture is clean, the GBM math is correct, and the test suite is comprehensive. All high and medium severity issues from the prior review have been resolved.

### Recommended Actions

**Should fix:**
1. `cache.py:31` — Change `timestamp or time.time()` to `timestamp if timestamp is not None else time.time()` (correctness at edge case, low risk)
2. `stream.py` — Move `router` instantiation inside `create_stream_router()` to prevent duplicate registration in tests
3. Add at least one SSE integration test using HTTPX + ASGI transport

**Nice to have:**
4. `cache.py` — Add lock to `version` property for future-proofing against free-threaded Python
5. `test_simulator.py:48` — Replace `sim._tickers` with `sim.get_tickers()`
6. `tests/conftest.py` — Remove unused `event_loop_policy` fixture

**No blockers.** The subsystem integrates cleanly via `from app.market import PriceCache, create_market_data_source, create_stream_router`.
