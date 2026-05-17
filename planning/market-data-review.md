# Market Data Backend — Code Review

**Reviewer:** Claude (claude-sonnet-4-6)
**Date:** 2026-05-17
**Branch:** claude/issue-4-20260517-1247
**Scope:** Full review of `backend/app/market/` (8 modules) and `backend/tests/market/` (6 test modules)

---

## 1. Executive Summary

The market data subsystem is well-architected and largely production-quality for a course capstone. The strategy pattern cleanly separates the simulator from the Massive API client, the PriceCache provides a solid single source of truth, and the test suite covers the critical paths. Several gaps exist between the design documents and the actual implementation — most notably a missing ±5% price clamp and missing deterministic seeding — plus a few minor bugs. None of the issues are blockers for the next development phase; all are straightforward to fix.

**Overall assessment:** ✅ Ready for downstream use, with the noted issues tracked below.

---

## 2. Test Run Status

> **Note:** The CI environment in which this review was conducted did not have Python dependencies pre-installed (no `.venv`, no `uv` available), and the permission settings blocked `pip install`, `uv sync`, and `python3 -m pytest`. Tests could not be executed in this session. The prior review session (see `planning/MARKET_DATA_SUMMARY.md`) reports **73 tests, all passing**. The static analysis below identifies additional cases not covered by those tests.

---

## 3. File-by-File Review

### 3.1 `models.py` — PriceUpdate

**Rating: ✅ Excellent**

- `@dataclass(frozen=True, slots=True)` is the correct choice for an immutable value object used heavily in a hot path.
- `timestamp: float = field(default_factory=time.time)` provides sensible defaults while allowing explicit override in tests.
- `change_percent` correctly guards against division by zero (`previous_price == 0`).
- `to_dict()` is clean and maps 1:1 to the SSE event shape documented in `MARKET_INTERFACE.md`.

**Issues:** None.

---

### 3.2 `cache.py` — PriceCache

**Rating: ✅ Good, one minor bug**

- Correctly uses `threading.Lock` (not `asyncio.Lock`) since the cache is written from a background asyncio task but could be read from sync context.
- `get_price()` → `get()` acquires the lock; then `.price` is accessed outside it, but since `PriceUpdate` is frozen, this is safe.
- `get_all()` returns a shallow copy (`dict(self._prices)`), protecting callers from mutation.

**Bug (minor): Falsy timestamp check** (`cache.py:31`)

```python
ts = timestamp or time.time()  # BUG: timestamp=0 would silently use time.time()
```

If `timestamp=0.0` is passed (epoch, or a test value), the condition evaluates as falsy and `time.time()` is used instead. Should be:

```python
ts = timestamp if timestamp is not None else time.time()
```

This is low-severity in practice (no caller passes `timestamp=0`), but is the kind of subtle bug that surfaces in edge-case tests.

**Minor: `version` property lacks a lock** (`cache.py:65-66`)

```python
@property
def version(self) -> int:
    return self._version  # No lock
```

Reading `self._version` (a plain Python `int`) is atomic in CPython due to the GIL, so this is safe today, but is worth documenting explicitly or protecting with the lock for correctness under non-CPython runtimes.

---

### 3.3 `seed_prices.py` — Seed Data & Parameters

**Rating: ✅ Good, one categorization discrepancy**

- Seed prices and GBM parameters are sensible and match the project documentation.
- `DEFAULT_PARAMS` correctly provides fallback values for runtime-added tickers.

**Discrepancy: NFLX sector grouping**

`MARKET_SIMULATOR.md` groups TSLA and NFLX together in a "consumer/auto" sector. The implementation places NFLX in the `tech` group:

```python
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},  # NFLX here
    "finance": {"JPM", "V"},
}
```

This means NFLX correlates at 0.6 with other tech stocks, rather than 0.55 with TSLA as a "consumer" pair. Not incorrect per se, but diverges from the stated design. TSLA is handled via the dedicated `TSLA_CORR` constant.

---

### 3.4 `interface.py` — MarketDataSource ABC

**Rating: ✅ Excellent**

- Clean abstract interface with well-documented lifecycle semantics.
- Docstring on `start()` notes "Calling start() twice is undefined behavior" — worth making this idempotent (as the design doc recommends) by checking `_task is not None` before starting. `SimulatorDataSource` actually does this; the interface should reflect the contract.
- `get_tickers()` returning `list[str]` instead of `set[str]` is reasonable since ordering matters for the Cholesky index mapping.

---

### 3.5 `simulator.py` — GBMSimulator + SimulatorDataSource

**Rating: ⚠️ Good, two significant gaps vs. design doc**

The GBM math is correct: the closed-form `S(t+dt) = S(t) * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)` is properly implemented, and Cholesky decomposition for correlated normals is working.

**Gap 1: Missing ±5% price clamp**

`MARKET_SIMULATOR.md` §5 specifies:
> We cap any single tick at ±5% as a safety rail.

This is not implemented in `simulator.py`. A very-high-volatility ticker (TSLA, σ=0.50) combined with a shock event (`shock_magnitude` up to 0.05) could produce moves larger than 5% in theory. The fix is a one-liner after the event jolt:

```python
max_price = prev_price * 1.05
min_price = prev_price * 0.95
self._prices[ticker] = max(min_price, min(max_price, self._prices[ticker]))
```

**Gap 2: Deterministic seeding not implemented**

`MARKET_SIMULATOR.md` §6 requires a `MARKET_SIM_SEED` environment variable for reproducible E2E tests. The implementation uses:
- `np.random.standard_normal(n)` — global NumPy RNG (not seeded)
- `random.random()` and `random.choice()` — Python stdlib RNG (not seeded)
- `random.uniform(50.0, 300.0)` for unknown tickers — also not seeded

Without seeding, the E2E tests cannot assert specific price ranges after N seconds. The `GBMSimulator.__init__` should accept an optional `seed` parameter and use a `numpy.random.Generator` and `random.Random` instance instead of the global RNGs.

**Minor: `add_ticker` in SimulatorDataSource doesn't normalize input**

`MassiveDataSource.add_ticker()` normalizes to uppercase and strips whitespace. `SimulatorDataSource.add_ticker()` does not. For consistency:

```python
async def add_ticker(self, ticker: str) -> None:
    ticker = ticker.upper().strip()  # Add this
    if self._sim:
        ...
```

**Minor: Exception swallowed in `_run_loop`**

```python
except Exception:
    logger.exception("Simulator step failed")
# continues looping
```

This is intentional for resilience, but the loop continues even after an unexpected error. A counter-based circuit breaker would be safer for production (e.g., abort after 10 consecutive failures).

---

### 3.6 `massive_client.py` — MassiveDataSource

**Rating: ✅ Good, one missing feature**

- Correctly wraps the synchronous `RESTClient` with `asyncio.to_thread()` to avoid blocking the event loop.
- Graceful error handling in `_poll_once`: catches all exceptions, logs at ERROR level, does not re-raise.
- Timestamps correctly converted from Massive's milliseconds to Unix seconds.

**Gap: No 429 retry logic**

`MARKET_INTERFACE.md` §9 specifies:
> 429 rate-limited: Back off 2s, retry once

The current implementation logs and continues without retry. For free-tier users (5 req/min), a stray extra request could cause a 15-second blackout. The fix would be to check `e` type and sleep before retrying in `_poll_once`.

**Note on Massive Python client**

The implementation uses the `massive` PyPI package (not `httpx` directly), despite `MASSIVE_API.md` §6 recommending against the official client in favor of a direct `httpx` call. This is a valid implementation choice; the `massive` package works correctly and the tests mock it well. The tradeoff is tighter coupling to an external library's API surface.

---

### 3.7 `factory.py` — create_market_data_source

**Rating: ✅ Excellent**

Clean, minimal factory. `.strip()` on the API key correctly handles whitespace-only env vars. No issues.

---

### 3.8 `stream.py` — SSE Endpoint

**Rating: ⚠️ Good, one design divergence and one structural issue**

**Design divergence: Full snapshot on every tick, not delta-only**

`MARKET_INTERFACE.md` §7 specifies two event types:
- `snapshot`: full data on connect
- `tick`: only changed tickers

The implementation sends all tickers' data every time the version changes:

```python
if current_version != last_version:
    prices = price_cache.get_all()
    data = {ticker: update.to_dict() for ticker, update in prices.items()}
    yield f"data: {payload}\n\n"
```

This is functionally correct and simpler, but sends ~10× more bytes per tick than necessary (10 tickers updated vs. ~2-3 that actually changed per cycle). For the 1-user, 10-ticker MVP this is negligible. The frontend flash logic must be aware that unchanged prices appear in every event.

**Structural issue: Module-level router registered inside factory**

```python
router = APIRouter(prefix="/api/stream", tags=["streaming"])  # module-level

def create_stream_router(price_cache: PriceCache) -> APIRouter:
    @router.get("/prices")                                     # registers on shared router
    async def stream_prices(...):
        ...
    return router
```

If `create_stream_router()` is called more than once (e.g., in tests), the `/prices` route is registered multiple times on the same `router` object. FastAPI silently deduplicates identical routes, but this is fragile. The cleaner pattern is to create the router inside the factory:

```python
def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])
    @router.get("/prices")
    ...
    return router
```

**Positive: Good SSE hygiene**

- `X-Accel-Buffering: no` disables nginx buffering — important for streaming.
- `retry: 1000\n\n` on connect tells EventSource to reconnect after 1s.
- `CancelledError` is handled cleanly.
- Disconnect detection via `request.is_disconnected()` is correct.

---

## 4. Test Suite Analysis

### Coverage Summary (from MARKET_DATA_SUMMARY.md)

| Module | Tests | Reported Coverage |
|--------|-------|-------------------|
| test_models.py | 11 | models.py: 100% |
| test_cache.py | 13 | cache.py: 100% |
| test_simulator.py | 17 | simulator.py: ~98% |
| test_simulator_source.py | 10 | Integration |
| test_factory.py | 7 | factory.py: 100% |
| test_massive.py | 13 | massive_client.py: 56% |

### Gaps Identified by Static Analysis

1. **`test_cache.py`**: No test for `timestamp=0` falsy bug (passes `timestamp=custom_ts` with `1234567890.0` only).
2. **`test_cache.py`**: No concurrent-write test verifying thread-safety.
3. **`test_simulator.py`**: No test for the ±5% clamp (which is unimplemented anyway).
4. **`test_simulator.py`**: No empirical correlation test (was in the design doc's test plan but not implemented).
5. **`test_simulator.py`**: No determinism test (was in design doc, but feature not implemented).
6. **`test_simulator.py`**: Uses `sim._tickers`, `sim._cholesky` private attributes — fragile if internals change.
7. **`stream.py`**: No tests at all for the SSE endpoint. This is the most significant test gap.
8. **`test_massive.py`**: 56% line coverage — `_poll_loop`, `start()` full flow, and lifecycle partially covered.

---

## 5. Issues Summary

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | 🐛 Bug (minor) | `cache.py:31` | `timestamp or time.time()` falsy check — should be `is not None` |
| 2 | ⚠️ Gap | `simulator.py` | ±5% per-tick price clamp not implemented (specified in MARKET_SIMULATOR.md §5) |
| 3 | ⚠️ Gap | `simulator.py` | Deterministic seeding (`MARKET_SIM_SEED`) not implemented (required for E2E tests) |
| 4 | ⚠️ Gap | `massive_client.py` | No 429 retry with backoff (specified in MARKET_INTERFACE.md §9) |
| 5 | 🔧 Design drift | `stream.py` | Sends full snapshot on every tick instead of delta-only (not wrong, just different from spec) |
| 6 | 🔧 Structural | `stream.py` | Module-level router + factory creates re-registration risk |
| 7 | 🔧 Consistency | `simulator.py` | `add_ticker`/`remove_ticker` don't normalize ticker to uppercase (MassiveDataSource does) |
| 8 | 🔧 Design drift | `seed_prices.py` | NFLX categorized as tech, not consumer (diverges from MARKET_SIMULATOR.md §3) |
| 9 | 📋 Test gap | `stream.py` | No tests for the SSE streaming endpoint |
| 10 | 📋 Test gap | `simulator.py` | No empirical correlation or determinism tests |

---

## 6. Positive Highlights

- **Architecture**: The strategy pattern is clean. Every caller goes through the cache; nothing talks to a data source directly. The boundary is well-enforced.
- **PriceCache design**: Thread-safe, minimal, correct. The `version` counter for SSE change detection is a nice touch.
- **SimulatorDataSource lifecycle**: `start()`/`stop()` are idempotent, the background task is properly named (`name="simulator-loop"`), and `CancelledError` is handled.
- **MassiveDataSource `asyncio.to_thread()`**: Correctly offloads the blocking synchronous RESTClient to a thread pool.
- **`__init__.py` public API**: Clearly re-exports the five public symbols; downstream code never needs to import from submodules.
- **Test isolation**: Each test creates its own `PriceCache` and source instance — no shared state, no ordering dependencies.
- **`pyproject.toml`**: Correct `asyncio_mode = "auto"` for pytest-asyncio; dev dependencies properly in `[project.optional-dependencies]`.

---

## 7. Recommendations for Next Phase

The following items should be addressed before the market data layer is depended on by the portfolio, chat, or E2E test systems:

1. **Fix `timestamp or time.time()` → `timestamp if timestamp is not None else time.time()`** — 1 line, prevents a silent bug.
2. **Implement `MARKET_SIM_SEED`** — Required for deterministic E2E tests. Add an optional `seed` parameter to `GBMSimulator`, instantiate `np.random.Generator` and `random.Random` with it, and read the env var in `SimulatorDataSource`.
3. **Add the ±5% price clamp** — 3 lines in `GBMSimulator.step()`. Prevents UI-breaking price spikes from compound shocks.
4. **Fix `create_stream_router` to create the router internally** — Avoids the multi-call re-registration issue.
5. **Add at least one smoke test for the SSE endpoint** — The streaming path is untested. A minimal test seeding the cache and collecting one SSE event would catch regressions.

Items 1–4 are low-effort and high-value. Item 5 requires a bit more infrastructure (test client + async generator handling) but is worth adding before the frontend depends on the stream.
