# Coding Conventions

**Analysis Date:** 2026-06-04

## Naming Patterns

**Files:**
- `snake_case.py` for all Python modules: `cache.py`, `massive_client.py`, `seed_prices.py`, `simulator.py`
- Test files prefixed with `test_`: `test_cache.py`, `test_factory.py`, `test_massive.py`
- Descriptive names reflecting the module's single responsibility

**Classes:**
- `PascalCase` throughout: `PriceCache`, `PriceUpdate`, `GBMSimulator`, `SimulatorDataSource`, `MassiveDataSource`, `MarketDataSource`
- Abstract base classes use the abstract concept name directly: `MarketDataSource` (not `AbstractMarketDataSource`)
- Concrete implementations suffix with the distinguishing trait: `SimulatorDataSource`, `MassiveDataSource`

**Functions and Methods:**
- `snake_case` for all functions and methods: `create_market_data_source()`, `get_price()`, `add_ticker()`, `rebuild_cholesky()`
- Private methods prefixed with single underscore: `_poll_once()`, `_poll_loop()`, `_run_loop()`, `_add_ticker_internal()`, `_rebuild_cholesky()`, `_fetch_snapshots()`, `_generate_events()`
- Factory functions named `create_*`: `create_market_data_source()`, `create_stream_router()`
- Boolean-returning methods not present yet; use `is_*` or `has_*` if added

**Variables and Attributes:**
- `snake_case` for local variables and instance attributes
- Private instance attributes prefixed with single underscore: `self._prices`, `self._lock`, `self._tickers`, `self._task`, `self._cache`, `self._client`, `self._interval`
- Public interface attributes (like properties) have no underscore: `cache.version`

**Constants:**
- `UPPER_SNAKE_CASE` for module-level constants, with type annotations: `SEED_PRICES: dict[str, float]`, `TICKER_PARAMS: dict[str, dict[str, float]]`, `DEFAULT_PARAMS: dict[str, float]`, `INTRA_TECH_CORR = 0.6`
- Class-level constants also use `UPPER_SNAKE_CASE`: `GBMSimulator.DEFAULT_DT`, `GBMSimulator.TRADING_SECONDS_PER_YEAR`

## Code Style

**Formatting:**
- Tool: `ruff` (configured in `backend/pyproject.toml`)
- Line length: 100 characters (`line-length = 100` in `[tool.ruff]`)
- Target Python version: 3.12

**Linting:**
- Tool: `ruff` with rule sets `E`, `F`, `I`, `N`, `W` enabled
- `E501` (line too long) is explicitly ignored — the formatter handles wrapping
- Run: `uv run --extra dev ruff check app/ tests/`

**Type Annotations:**
- Full type annotations on all function signatures — parameters and return types
- `from __future__ import annotations` in every source file for PEP 563 deferred evaluation
- Union types use Python 3.10+ `|` syntax: `float | None`, `np.ndarray | None`
- Built-in generics used directly (Python 3.12): `list[str]`, `dict[str, float]`, `dict[str, PriceUpdate]`
- Complex types from `collections.abc`: `AsyncGenerator` imported from `collections.abc`
- Dataclass fields are fully typed including field defaults: `timestamp: float = field(default_factory=time.time)`

## Import Organization

**Order (per ruff `I` rules):**
1. `from __future__ import annotations` (always first in source files)
2. Standard library imports (alphabetical within group)
3. Third-party imports (alphabetical within group)
4. Local/relative imports (relative dot syntax)

**Relative vs Absolute:**
- Internal package imports use relative syntax: `from .cache import PriceCache`, `from .interface import MarketDataSource`
- Public API re-exported from `__init__.py` using explicit `__all__` list
- Test files use absolute package imports: `from app.market.cache import PriceCache`

**Example pattern from `backend/app/market/simulator.py`:**
```python
from __future__ import annotations

import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS,
    ...
)
```

## Error Handling

**Background Tasks (Asyncio Loops):**
- Wrap the entire loop body in `try/except Exception` to prevent task death on transient errors
- Log with `logger.exception()` for unexpected errors (includes stack trace): `logger.exception("Simulator step failed")`
- Log with `logger.error()` for expected/recoverable errors: `logger.error("Massive poll failed: %s", e)`
- Never re-raise from background loops — comment explains why: `# Don't re-raise — the loop will retry`

**Task Cancellation:**
- Explicit `asyncio.CancelledError` handling in `stop()` methods:
  ```python
  async def stop(self) -> None:
      if self._task and not self._task.done():
          self._task.cancel()
          try:
              await self._task
          except asyncio.CancelledError:
              pass
      self._task = None
  ```
- `stop()` is always idempotent — safe to call multiple times

**Per-Item Error Handling:**
- When processing a batch (e.g., API snapshots), wrap individual item processing in inner `try/except` with specific exception types: `except (AttributeError, TypeError) as e`
- Log with `logger.warning()` and skip the bad item; do not fail the batch

**Guard Clauses:**
- Early return pattern for missing preconditions: `if not self._tickers or not self._client: return`

## Logging

**Framework:** Python stdlib `logging` module

**Logger Creation (per-module):**
```python
logger = logging.getLogger(__name__)
```
Every module with side effects gets its own logger via `__name__`.

**Log Levels:**
- `logger.info()` — lifecycle events: start, stop, add/remove ticker, client connect/disconnect
- `logger.debug()` — high-frequency operational data: per-poll counts, random events
- `logger.warning()` — skippable errors: malformed API response for a single ticker
- `logger.error()` — recoverable failures affecting a full operation: API poll failure
- `logger.exception()` — unexpected exceptions (includes full traceback): simulator step crash

**Format:** `%`-style formatting, not f-strings: `logger.info("Simulator started with %d tickers", len(tickers))`

## Docstrings

**Module level:** Single-line `"""Module purpose."""` on every file.

**Class level:** Multi-line docstring explaining purpose, key design notes (threading, lifecycle), and often includes a "Lifecycle:" usage example:
```python
class MarketDataSource(ABC):
    """Contract for market data providers.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])
        ...
    """
```

**Method level:** Single-line docstring on every public method. Longer methods include a note about the hot path, threading model, or behavioral edge cases.

**Private methods:** Docstrings explain the "why" not just "what": `"""Rebuild the Cholesky decomposition of the ticker correlation matrix. Called whenever tickers are added or removed. O(n^2) but n < 50."""`

## Function Design

**Size:** Methods are focused and short. The longest method (`GBMSimulator.step()`) is ~40 lines including comments. Internal helpers split out at `# --- Internals ---` section markers.

**Section Comments:** Classes use comment markers to separate public API from internals:
```python
# --- Public API ---
def step(self): ...

# --- Internals ---
def _add_ticker_internal(self): ...
```

**Parameters:**
- Keyword arguments with defaults used for all optional config: `update_interval: float = 0.5`, `event_probability: float = 0.001`, `poll_interval: float = 15.0`
- Injected dependencies passed to `__init__` (dependency injection, not global lookup): `PriceCache` passed in, not imported as a singleton

**Return Values:**
- Never return `None` implicitly when a meaningful value is expected; use `| None` in the return type
- Immutable data preferred: `get_all()` returns `dict(self._prices)` (a copy); `get_tickers()` returns `list(self._tickers)` (a copy)

## Module Design

**Public API via `__init__.py`:**
- Subpackages expose a curated public API through `__init__.py` with an explicit `__all__` list
- Example: `backend/app/market/__init__.py` re-exports `PriceUpdate`, `PriceCache`, `MarketDataSource`, `create_market_data_source`, `create_stream_router`

**Single Responsibility:**
- Each file has one primary class or one primary function group: `models.py` → `PriceUpdate`, `cache.py` → `PriceCache`, `interface.py` → `MarketDataSource`, `factory.py` → `create_market_data_source()`

**Dependency Direction:**
- Models have no dependencies on other local modules
- Cache depends only on models
- Interface depends on nothing local
- Implementations depend on cache and interface
- Factory depends on implementations
- Stream depends on cache only

**Factory Pattern:**
- Used for selecting between implementations at runtime: `create_market_data_source(cache)` checks environment variable and returns the appropriate concrete type
- Used for creating FastAPI routers with injected dependencies: `create_stream_router(price_cache)` returns a configured `APIRouter`

## Dataclass Usage

**Immutable models** use `@dataclass(frozen=True, slots=True)`:
```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)
```
- `frozen=True` enforces immutability — mutation raises `AttributeError`
- `slots=True` reduces memory usage for high-frequency data objects
- Computed properties (not stored fields) for derived values: `change`, `change_percent`, `direction`

---

*Convention analysis: 2026-06-04*
