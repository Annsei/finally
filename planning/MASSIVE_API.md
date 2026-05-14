# Massive (formerly Polygon.io) Market Data API — Research

> Research document for the FinAlly market data layer. This covers what we need
> to know to implement the **Massive** code path (`MASSIVE_API_KEY` set in
> `.env`). The default path is the in-process simulator; see
> [`MARKET_SIMULATOR.md`](MARKET_SIMULATOR.md). The shared abstraction that
> both implementations satisfy is in [`MARKET_INTERFACE.md`](MARKET_INTERFACE.md).

Sources (May 2026):
- [Pricing | Massive](https://massive.com/pricing) — JS-rendered, see footnotes
- [Stocks Single Ticker Snapshot](https://massive.com/docs/rest/stocks/snapshots/single-ticker-snapshot)
- [Stocks Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot)
- [Previous Day Bar (OHLC)](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
- [Custom Bars (OHLC)](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
- [Last Trade](https://massive.com/docs/rest/stocks/trades-quotes/last-trade)
- [What is the request limit for Massive's RESTful APIs?](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-polygons-restful-apis)
- [Massive Python client v2.7.0 (formerly polygon-io/client-python)](https://github.com/polygon-io/client-python)

---

## 1. Naming and current status

Polygon.io rebranded to **Massive** during 2025–2026. As of May 2026:

- The marketing site is `https://massive.com`.
- `https://polygon.io/*` URLs `301`-redirect to `massive.com/*`.
- The REST API host **continues to work as `api.polygon.io`**, and most
  third-party blog posts still call the service "Polygon." Existing API keys
  and tokens are unchanged.
- The official Python client package was renamed from `polygon-api-client` to
  `massive` (latest v2.7.0, released 2026-05-04). Older `polygon-api-client`
  installs continue to function for now.

**Implication for FinAlly:** the env var stays `MASSIVE_API_KEY` (matches the
current name), but our HTTP client should hit `api.polygon.io` until/unless the
data host is publicly retired. We should keep the host configurable via an
internal constant to make a future switch a one-line change.

---

## 2. Authentication

API key auth, two forms (both accepted; pick one and stick with it):

| Form | Example |
|------|---------|
| Query param | `?apiKey=YOUR_KEY` |
| Bearer header | `Authorization: Bearer YOUR_KEY` |

We will use **`Authorization: Bearer`** so the key never appears in URLs (logs,
error messages, browser dev tools if a request ever leaks to the frontend).

---

## 3. Pricing tiers and rate limits

The pricing page is JS-rendered and could not be scraped cleanly. The numbers
below are the consensus across the knowledge-base article, Massive's own
marketing copy, and recent (2025–2026) third-party reviews. They are the
**planning assumption**; the integration code should treat them as
configuration, not hardcoded constants.

### Stocks tiers (as of May 2026)

| Tier | Price/mo | Calls/min | Data freshness | History | WebSocket |
|------|----------|-----------|----------------|---------|-----------|
| **Basic (Free)** | $0 | **5** | 15-min delayed | 2 years EOD | No |
| **Starter** | $29 | **Unlimited** | 15-min delayed | 5 years | No |
| **Developer** | $79 | **Unlimited** | Real-time | 10 years | Limited |
| **Advanced** | $199 | **Unlimited** | Real-time + L2 | 15+ years | Yes |
| **Business** | Custom | Unlimited | Real-time + FMV | Full | Yes |

Notes:
- "Unlimited" calls means **no documented per-minute cap**; fair-use abuse
  protection still applies. There is no daily quota on paid tiers.
- 15-minute-delayed data is fine for a learning project — the simulator is
  zero-delay anyway, and the demo audience will not notice the lag.
- WebSocket is only on higher tiers. **We will not depend on WebSocket.** Our
  whole architecture uses REST polling so the free tier works end-to-end.

### Practical poll cadence we will use

| Tier in use | Poll interval | Tickers per call | Notes |
|-------------|---------------|------------------|-------|
| Free / unknown | **15s** | up to 250 (one unified-snapshot call) | Uses 4 calls/min, well under 5/min limit |
| Starter+ | **2–5s** | up to 250 | Pushes toward perceived real-time |
| Developer+ | **2s** | up to 250 | Closest to simulator's 500ms cadence |

A **single** `/v3/snapshot` request returns up to 250 tickers, so the call rate
is independent of watchlist size in the realistic case (10–50 tickers).

---

## 4. Endpoints we will use

For FinAlly we only need **one endpoint** in the steady state: the unified
snapshot. Two others are useful at startup / for sparkline seeding.

### 4.1 Unified Snapshot — primary live-price source

```
GET https://api.polygon.io/v3/snapshot?ticker.any_of=AAPL,GOOGL,MSFT,...&limit=250
Authorization: Bearer ${MASSIVE_API_KEY}
```

| Param | Notes |
|-------|-------|
| `ticker.any_of` | Comma-separated, max **250** tickers in one call |
| `type` | We don't need to set this; stocks-only watchlist is fine |
| `limit` | Set to `250` to match `any_of` cap |

**Response shape (one entry per ticker):**

```json
{
  "request_id": "...",
  "status": "OK",
  "results": [
    {
      "ticker": "AAPL",
      "type": "stocks",
      "name": "Apple Inc.",
      "market_status": "open",
      "last_trade": {
        "price": 190.42,
        "size": 100,
        "exchange": 4,
        "sip_timestamp": 1715712345678000000
      },
      "last_quote": {
        "bid": 190.40, "ask": 190.43,
        "bid_size": 200, "ask_size": 150,
        "midpoint": 190.415
      },
      "session": {
        "open": 188.90, "close": 190.42,
        "high": 191.20, "low": 188.50,
        "change": 1.52,
        "change_percent": 0.81,
        "volume": 12345678,
        "early_trading_change": 0.10,
        "regular_trading_change": 1.42,
        "late_trading_change": 0.00
      },
      "last_minute": { "...": "..." },
      "fmv": null
    }
  ]
}
```

**What FinAlly needs from this response, per ticker:**

| FinAlly field | Source path |
|---------------|-------------|
| `price` | `results[i].last_trade.price` (fallback: `session.close`) |
| `prev_price` | locally cached previous `price` |
| `change` | `results[i].session.change` |
| `change_percent` | `results[i].session.change_percent` |
| `volume` | `results[i].session.volume` |
| `timestamp` | server-side `time.time()` at the moment of the poll |

We deliberately ignore last-quote / bid-ask, FMV, conditions, exchange ID, and
nanosecond timestamps — they are not used anywhere in the UI.

### 4.2 Single Ticker Snapshot — fallback / one-off lookups

```
GET https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{stocksTicker}
```

Response shape uses different (older v2) field names: `day`, `min`, `prevDay`,
`lastTrade`, `lastQuote`, `todaysChange`, `todaysChangePerc`, `updated`. Inner
OHLC objects use short letters: `o, h, l, c, v, vw`.

**Use case:** if `/v3/snapshot` is ever missing a ticker (recently added, low
volume), fall back here for that one symbol. Not needed for the steady-state
poll loop.

### 4.3 Custom Bars (OHLC) — sparkline backfill (optional)

```
GET /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}
```

Path params: `multiplier` × `timespan` (e.g., `5/minute` for 5-minute bars).

Per-bar JSON: `{ o, h, l, c, v, vw, n, t }` where `t` is Unix-ms.

**Use case:** when the user first loads the page, the sparkline is empty
because we only accumulate ticks since page load. If a richer sparkline is
desired, we can pre-fill 30–60 bars per ticker at startup. **MVP punts on this
— sparklines fill in from the SSE stream as planned.**

### 4.4 Previous Day Bar — open/close for "change today" anchor

```
GET /v2/aggs/ticker/{stocksTicker}/prev?adjusted=true
```

Returns one row: `{ T, o, h, l, c, v, vw, n, t }`.

**Use case:** if we want a stable "% change today" rather than tick-over-tick.
The unified snapshot already gives us `session.change_percent`, so we likely
don't need this endpoint. Documenting it for completeness.

---

## 5. Error and quota response shapes

When you exceed the free-tier 5 req/min limit, you get `429 Too Many Requests`:

```json
{ "status": "ERROR", "request_id": "...", "error": "exceeded the maximum requests per minute" }
```

When a ticker doesn't exist, you typically get a `200 OK` with an empty
`results` array (for unified snapshot) or `404` (for single-ticker endpoints).

When the API key is invalid: `401 Unauthorized` with `status: "NOT_AUTHORIZED"`.

Our client should:

1. Retry **once** on `429` after a 2-second back-off, then surface the error.
2. Treat `404` / empty `results` as "no price for that ticker, leave the cache
   entry alone, log at DEBUG level."
3. Surface `401` to the operator (log at ERROR, fail fast on startup if the
   key was set but is invalid).

---

## 6. Python client library

The official client is now `massive` on PyPI (v2.7.0, May 2026), formerly
`polygon-api-client`. It wraps the REST endpoints with typed methods.

**Decision: we will not use it.**

Reasons:
- We need exactly **one** endpoint (`/v3/snapshot`) in the hot loop.
- `httpx` is already a transitive dep of FastAPI and supports async cleanly.
- The client's typed return values change between releases; an `httpx` call
  parsing a stable JSON shape is easier to mock and test.
- Avoids pulling in a 50-endpoint SDK to call one URL.

Reference implementation sketch:

```python
import httpx

class MassiveClient:
    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str, timeout: float = 5.0):
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def unified_snapshot(self, tickers: list[str]) -> dict[str, float]:
        if not tickers:
            return {}
        resp = await self._client.get(
            "/v3/snapshot",
            params={"ticker.any_of": ",".join(tickers), "limit": 250},
        )
        resp.raise_for_status()
        body = resp.json()
        out: dict[str, float] = {}
        for row in body.get("results", []):
            price = (row.get("last_trade") or {}).get("price")
            if price is None:
                price = (row.get("session") or {}).get("close")
            if price is not None:
                out[row["ticker"]] = float(price)
        return out
```

---

## 7. Open questions for implementation

1. **15-min delay on free tier — do we care?** No. The simulator path is
   what most users will run. Real-key users on free tier get delayed data, but
   the price flash UX is identical.
2. **Do we need market-hours awareness?** Yes — outside RTH the same poll
   returns unchanging numbers, which makes the UI feel broken. Approach: when
   `market_status != "open"` for *all* tickers in three consecutive polls,
   slow polling to 60s and surface a "market closed" badge in the header. The
   simulator ignores hours.
3. **Backfilling sparklines via `/v2/aggs/.../range/...`** — deferred to a
   follow-up. SSE-accumulated sparklines satisfy the MVP.
4. **Switching hosts from `api.polygon.io` to a future `api.massive.com`** —
   keep the base URL as a module constant. One-line swap if/when Massive
   announces deprecation.

---

## 8. Footnotes / data quality

- The pricing tier numbers in §3 could not be verified directly from
  `massive.com/pricing` (JS-rendered, WebFetch sees an empty page). They are
  cross-referenced from the knowledge-base article and 2026 third-party
  reviews. **Before going to production**, re-verify by hitting the live
  pricing page in a real browser.
- "Developer at $7/mo" appears in some older sources — this is the
  *Indices* Developer tier, not stocks. Stocks Developer is $79.
