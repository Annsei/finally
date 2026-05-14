# Market Data Simulator — Design

> Design for the default (no-API-key-needed) market data source. Implements
> the `MarketDataSource` protocol defined in [`MARKET_INTERFACE.md`](MARKET_INTERFACE.md).
> The Massive REST path is described in [`MASSIVE_API.md`](MASSIVE_API.md).

The simulator is the **happy path** for FinAlly users — anyone who can run
Docker can run the app, no API signup required. It needs to feel like a real
ticker tape: prices move continuously, related tickers correlate, the occasional
"event" gives the user something to react to.

References:
- [Geometric Brownian Motion Simulation with Python (QuantStart)](https://www.quantstart.com/articles/geometric-brownian-motion-simulation-with-python/)
- [Simulate Multi-Asset Baskets With Correlated Price Paths (Wouter van Heeswijk)](https://medium.com/codex/simulate-multi-asset-baskets-with-correlated-price-paths-using-python-472cbec4e379)
- [Generating Synthetic Equity Data with Realistic Correlation Structure (QuantStart)](https://www.quantstart.com/articles/generating-synthetic-equity-data-with-realistic-correlation-structure/)

---

## 1. Goals and non-goals

**Goals**

- Produce price ticks that look and feel like a live equity feed.
- Correlated motion across the 10 default tickers (FAANG-ish + JPM/V — they
  should move *somewhat* together when "the market" moves).
- Visually interesting: enough volatility for green/red flashes ~once a
  second per ticker; occasional 2-5% pops or drops to give the user
  something to chase.
- Realistic seed prices (AAPL ~$190, GOOGL ~$175, etc.) so the UI doesn't
  look like Monopoly money.
- 500ms tick cadence, matching the SSE push interval.
- Deterministic when seeded — required for the LLM-mocked E2E tests.

**Non-goals**

- Order-book simulation. We have no bid/ask, no depth, no spread.
- Realistic intraday session behavior (open auction, lunch lull, MOC). The
  simulator is "always trading."
- Modeling earnings, news, splits, dividends. The "event" mechanism is
  cosmetic noise, not modeled news.
- Strict statistical accuracy. We are not running Monte Carlo for risk
  pricing; we're building a demo that *looks* alive.

---

## 2. The math: discrete-time GBM

The standard continuous-time geometric Brownian motion SDE is:

```
dS_t = μ S_t dt + σ S_t dW_t
```

where `μ` is the drift, `σ` is the volatility, and `dW_t` is a Wiener
increment. The closed-form solution is:

```
S_{t+Δt} = S_t · exp( (μ − σ²/2) · Δt  +  σ · √Δt · Z )
```

with `Z ~ N(0, 1)`. We use this exact form per tick — no Euler approximation
needed, and the multiplicative update guarantees prices never go negative.

**Per-tick code (single ticker, no correlation):**

```python
new_price = prev_price * math.exp(
    (drift - 0.5 * vol**2) * dt
    + vol * math.sqrt(dt) * random.gauss(0, 1)
)
```

**Time unit choice:** annualize. Set `dt = 0.5 / (252 * 6.5 * 3600)` so each
500ms tick represents one half-second of one trading day. Then `drift` and
`vol` are interpretable as annualized numbers (e.g., AAPL μ=0.10 means 10%
expected annual return, σ=0.25 means 25% annualized vol). This keeps per-tick
moves in a realistic-feeling range (≈0.05% std per tick at σ=0.25) and lets
us reason about the parameters with finance intuition.

---

## 3. Cross-ticker correlation

A bag of independent GBMs will look fake — when tech is "rallying" you want
to see AAPL, GOOGL, MSFT, META all flashing green together. The standard
trick is to draw correlated normals via Cholesky decomposition of a
correlation matrix `R`.

```
L = cholesky(R)         # lower triangular, R = L · Lᵀ
Z = L · z                # z is i.i.d. N(0,1), Z is correlated N(0, R)
```

then plug `Z[i]` into ticker `i`'s GBM step.

**Choosing the correlation matrix.** We hardcode three "sectors" and assume:

| | Tech mega-cap (AAPL/GOOGL/MSFT/AMZN/META/NVDA) | Consumer/auto (TSLA/NFLX) | Financial (JPM/V) |
|---|---|---|---|
| Within sector | 0.65 | 0.55 | 0.65 |
| Tech ↔ Consumer | 0.45 | — | — |
| Tech ↔ Financial | 0.30 | — | — |
| Consumer ↔ Financial | 0.25 | — | — |
| Self | 1.00 | 1.00 | 1.00 |

These numbers are chosen by feel, not estimation. The matrix is symmetric
positive definite by construction; we sanity-check with `np.linalg.cholesky`
at startup and assert success.

For tickers added at runtime (via watchlist), we fall back to a default
correlation of **0.40 against every existing ticker** — high enough that
they don't look islanded, low enough that they have personality. This avoids
recomputing the full matrix and re-doing Cholesky every time the user adds a
symbol; we just extend `L` with one row by Gram-Schmidt:

```
# New row of R: [0.4, 0.4, ..., 0.4, 1.0]
# Lower-triangular extension: solve L · x = [0.4, ..., 0.4]ᵀ
# Then last entry of new row in L is sqrt(1 - x·x).
```

The math here is straightforward but easy to get wrong. We will write a tight
unit test that asserts `(extended_L @ extended_L.T)` round-trips the expected
correlations.

---

## 4. Per-ticker parameters

Constants live in `backend/app/market/simulator_config.py`:

```python
@dataclass(frozen=True)
class TickerSeed:
    ticker: str
    seed_price: float
    drift: float        # annualized expected return
    vol: float          # annualized stddev
    sector: Literal["tech", "consumer", "financial"]

SEEDS = [
    TickerSeed("AAPL",  190.00, 0.10, 0.25, "tech"),
    TickerSeed("GOOGL", 175.00, 0.12, 0.28, "tech"),
    TickerSeed("MSFT",  420.00, 0.10, 0.22, "tech"),
    TickerSeed("AMZN",  185.00, 0.12, 0.30, "tech"),
    TickerSeed("TSLA",  240.00, 0.05, 0.55, "consumer"),  # high vol on purpose
    TickerSeed("NVDA",  900.00, 0.20, 0.45, "tech"),       # high drift + vol
    TickerSeed("META",  490.00, 0.10, 0.30, "tech"),
    TickerSeed("JPM",   200.00, 0.06, 0.20, "financial"),
    TickerSeed("V",     280.00, 0.08, 0.18, "financial"),
    TickerSeed("NFLX",  620.00, 0.08, 0.35, "consumer"),
]
```

Seed prices are deliberately close to mid-2026 reality so the UI feels
familiar at first glance.

For **runtime-added tickers** we don't know a real price. The simulator
fetches **no real data** (the whole point is no external deps), so we pick:

- Seed price: deterministic hash of ticker symbol → `$50–$500` range.
- Drift: 0.08, Vol: 0.30 (middle of the pack).
- Sector: "tech" (so correlation lookups work).

This is intentionally crude. A user adding `PYPL` will see "PYPL @ $213.47"
or whatever the hash produces, not the real $58. **This is acceptable** —
the simulator is a demo, not a paper-trading exchange. The README and the
PLAN are clear that real prices require `MASSIVE_API_KEY`.

---

## 5. Event jolts ("drama")

In a flat GBM run, the user might watch for 30 seconds and see only small
moves. The "event" mechanism is a thin layer of jolts on top of the GBM
draw:

- Every tick, for each ticker, draw `random.random() < event_prob`. Default
  `event_prob = 1 / (60 * 2)` ≈ one event per ticker per minute on average.
- When an event fires, multiply the tick's normal random by `±k` where `k`
  is drawn uniformly from `[3, 6]`. Sign is uniform.
- The same Cholesky-correlated structure still applies, so a "tech jolt"
  will tug related tech names in the same direction. This is the magic that
  makes the watchlist feel alive.

Event multipliers stack with normal vol, so on a `σ=0.25` ticker, a `k=4`
event tick produces a ~0.2% move — visible but not absurd. Larger `k=6`
events on TSLA (`σ=0.55`) approach the spec'd "2–5% drama" cap.

We cap any single tick at ±**5%** as a safety rail:

```python
new_price = clamp(new_price, prev * 0.95, prev * 1.05)
```

This prevents a long-tail draw from showing the user `AAPL: $190 → $228` in
one tick.

---

## 6. Determinism for tests

The PLAN requires `LLM_MOCK=true` E2E tests. The market simulator needs an
analogous **deterministic mode** for the same E2E suite.

```python
class SimulatorSource:
    def __init__(self, cache: PriceCache, *, seed: int | None = None):
        self._rng = random.Random(seed) if seed is not None else random.SystemRandom()
```

When `MARKET_SIM_SEED` is set in `.env`, we use a seeded `random.Random`.
Combined with a fixed tick clock (we already step every 500ms, no `time.time`
input to the math), this makes the price stream reproducible.

For the E2E suite, the test starts the app with `MARKET_SIM_SEED=42` and can
assert "after N seconds, AAPL is in the range [188.40, 192.10]" — wide enough
for clock jitter, narrow enough to catch regression.

---

## 7. Background task structure

```python
class SimulatorSource:
    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="market-sim-tick")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.5)
                ticks = self._step()
                await self._cache.bulk_set(ticks)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("simulator tick loop crashed")
            raise   # surface to the event loop; we don't silently swallow
```

The loop is fully async-friendly: `asyncio.sleep`, no blocking I/O, math is
pure CPU and trivially fast (~50µs per tick for 10 tickers using numpy).
There is no need for a thread or process.

**Performance budget at default scale:** 10 tickers × 500ms = 20 GBM steps/s.
With numpy vectorization the whole `_step()` is a single matrix multiply for
the Cholesky transform plus an elementwise update — under 200µs. Even at
250 tickers this is well under 5ms per tick, well within budget.

---

## 8. `ensure_tickers` semantics

```python
async def ensure_tickers(self, tickers: set[str]) -> None:
    new = tickers - self._state.keys()
    for t in new:
        self._add_ticker(t)   # seed price, drift/vol, extend Cholesky
    # we intentionally do NOT remove tickers no longer in the set —
    # the user may re-add them later, and the cost of carrying state
    # for a removed ticker is one float and one matrix row.
```

Adding is O(N) for the Cholesky extension (one back-substitution row). Not
removing is a deliberate choice; the cache size never grows beyond the
distinct tickers seen in a process lifetime, which for a human-driven app is
trivially bounded.

---

## 9. What the simulator does **not** simulate

| Real-market thing | Simulator's behavior |
|-------------------|----------------------|
| Market hours | Ignored — always "open" |
| Trading halts | Never happens |
| Earnings announcements | Implicitly: random "events" stand in for news |
| Splits / dividends | Ignored — prices are continuous |
| Order book depth, spread, slippage | Ignored — fills are instant at the last price |
| Volume / volume profile | Not emitted (would require a separate process) |
| Pre/post-market sessions | Ignored |
| Currency / FX | Stocks only, all in USD |

This list is intentional. Every item above is something a future enhancement
*could* add, but each adds complexity to the UI and the math. The MVP runs
without them.

---

## 10. Test plan (simulator-specific)

In `backend/tests/market/`:

1. `test_gbm_math.py` — single-ticker step has no drift bias over 10k
   samples within 2σ; respects the `±5%` clamp; price never goes negative.
2. `test_correlation.py` — given a fixed seed, the empirical Pearson
   correlation of `log(returns)` between AAPL and MSFT over 5k ticks is
   within ±0.05 of the configured 0.65.
3. `test_ensure_tickers.py` — adding `PYPL` at runtime: cache entry appears
   within one tick; the new ticker's draws are correlated with existing
   ones (target 0.40 ± 0.10 empirically over 2k ticks).
4. `test_determinism.py` — two `SimulatorSource(seed=42)` instances produce
   identical tick streams for the first 100 ticks.
5. `test_event_jolts.py` — with `event_prob` cranked to 0.5, jolts are
   visible (max-abs return > 3× vol budget within 100 ticks) but capped at
   5%.
6. `test_lifecycle.py` — `start`/`stop` idempotent; `stop` cancels the task
   cleanly within 100ms; no leaked tasks after teardown.

All tests run in <1 second on CI by directly stepping `_step()` instead of
sleeping in real time.

---

## 11. Implementation outline

```
backend/app/market/
├── __init__.py
├── interface.py         # MarketDataSource Protocol, PriceTick dataclass
├── cache.py             # PriceCache class
├── simulator.py         # SimulatorSource
├── simulator_config.py  # TickerSeed list, correlation constants
└── massive.py           # MassiveSource (see MASSIVE_API.md)
```

The simulator file is ~200 lines: dataclasses, the Cholesky bootstrap, the
`_step` body, the loop, the lifecycle methods. Self-contained, no
dependencies outside the stdlib and numpy (which we want anyway for the
correlation math).

---

## 12. Decisions log

| Decision | Chosen | Alternative | Why |
|----------|--------|-------------|-----|
| Stochastic model | Geometric Brownian Motion | Ornstein-Uhlenbeck, Heston | GBM is the textbook baseline; richer models add knobs we don't need |
| Correlation method | Cholesky on a fixed sector matrix | Estimated from historical data | A fixed matrix is reproducible and avoids shipping a CSV; sector intuition is good enough for a demo |
| Tick cadence | 500ms | 100ms / 1s | matches SSE push cadence; preserves the flash UX |
| Time unit | Annualized μ/σ, `dt = 0.5/(252·6.5·3600)` | Per-tick μ/σ as raw numbers | annualized is the universally understood convention |
| Event mechanism | Multiplier on the random draw | Inject a separate "news price" | Same Cholesky path applies → coordinated sector moves |
| Tick safety rail | ±5% clamp per tick | None | Long-tail draws look broken in the UI |
| Unknown-ticker behavior | Hash-derived seed price | Fail / fetch from Massive | Demo: we keep working; users wanting real prices set MASSIVE_API_KEY |
| Determinism | Seed via `MARKET_SIM_SEED` | Always random | Required for the E2E test contract (mirror of `LLM_MOCK`) |
| Background task | Single `asyncio.Task` | Thread / process | numpy step is microseconds; async is the simpler fit |
