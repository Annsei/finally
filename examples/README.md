# FinAlly Examples — Trade the Arena from Your Own Code

FinAlly is not just a UI: every account doubles as an **open paper brokerage**.
Mint an API key, point any program at `http://localhost:8000`, and your bot
trades the same simulated market — and the same leaderboard — as human traders
and the built-in AI copilot.

This directory contains:

| File | What it is |
|---|---|
| `finally_bot.py` | A ~70-line momentum bot (Python, `requests` only) that trades 5/20 moving-average crossovers |

## Prerequisites

- FinAlly running locally (`scripts/start_mac.sh` or `scripts/start_windows.ps1`), UI at <http://localhost:8000>
- Python 3.10+ with `requests` (`pip install requests`)

## Step 1 — Log in and pick a name

Open <http://localhost:8000> and log in from the header with a display name
(2–24 characters; letters, digits, `-`, `_`). That name is your identity on the
leaderboard — the account your bot will trade for. Guest mode works too, but a
named account is what makes the arena fun.

## Step 2 — Mint an API key on /developers

Go to <http://localhost:8000/developers>:

1. In **Create key**, enter a label (e.g. `momentum-bot`).
2. Add guardrails — this is the interesting part. Guardrails are enforced
   server-side on every request the key makes, so a buggy (or overly bold) bot
   can only do what you allowed:
   - **Allowed tickers**: `NVDA` — the key may only trade NVDA.
   - **Max order quantity**: `10` — any single order above 10 shares is refused.
   - **Daily trade cap**: `20` — at most 20 filled orders per UTC day.
3. Click create. The full key (`fk_...`) is shown **exactly once** — copy it
   now. Only a SHA-256 hash is stored; nobody (including you) can recover the
   plaintext later. The list shows just the `fk_XXXXXXXX` prefix.

Key hygiene, enforced by the server:

- **Freeze** a key on the /developers page for an instant kill switch (every
  request gets 403 until you unfreeze). **Revoke** deletes it permanently.
- **Keys cannot manage keys**: key-management endpoints only accept your
  browser session. A leaked key can never unfreeze itself, raise its own
  limits, or mint new keys.
- State-changing calls a key makes on the trading surface — trades, orders,
  rules, watchlist, chat, strategies, the backtest run library, and season
  resets — land in the **audit ledger** on the same page: ok / denied /
  error / rate_limited, per key.
- Rate limit per key: bursts of 10 requests, refilling at 5/s. Excess gets 429.

## Step 3 — Quick verification with curl

```bash
export FINALLY_URL=http://localhost:8000
export FINALLY_API_KEY=fk_...   # paste the key from step 2

# Live quotes (public market data, no key needed)
curl -s "$FINALLY_URL/api/market/quotes"

# Your cash and positions, as the key's owner
curl -s -H "Authorization: Bearer $FINALLY_API_KEY" "$FINALLY_URL/api/portfolio/"

# Buy 1 share of NVDA (instant fill at the live price)
curl -s -X POST "$FINALLY_URL/api/portfolio/trade" \
  -H "Authorization: Bearer $FINALLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "NVDA", "side": "buy", "quantity": 1}'

# Guardrail demo: with the key restricted to NVDA, trading TSLA is refused
curl -s -X POST "$FINALLY_URL/api/portfolio/trade" \
  -H "Authorization: Bearer $FINALLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "TSLA", "side": "buy", "quantity": 1}'
# -> HTTP 403 {"error": "Ticker not allowed for this key"}
```

Refresh the audit table on /developers — you'll see an `ok` row for the NVDA
buy and a `denied` row for the TSLA attempt.

Prefer a fully headless setup? Log in and mint the key with curl too (key
management needs the session cookie, never a Bearer key):

```bash
curl -s -c /tmp/finally.cookies -X POST "$FINALLY_URL/api/auth/login" \
  -H "Content-Type: application/json" -d '{"name": "bot-master"}'
curl -s -b /tmp/finally.cookies -X POST "$FINALLY_URL/api/keys" \
  -H "Content-Type: application/json" \
  -d '{"label": "momentum-bot", "allowed_tickers": ["NVDA"], "daily_trade_cap": 20}'
```

Full interactive API reference (Swagger UI): <http://localhost:8000/api/docs>.

## Step 4 — Run the bot

```bash
pip install requests
export FINALLY_URL=http://localhost:8000
export FINALLY_API_KEY=fk_...   # from step 2
export BOT_TICKER=NVDA          # optional, default NVDA
export BOT_QTY=2                # optional, default 2

python examples/finally_bot.py
```

The strategy in one paragraph: the bot polls `/api/market/quotes` about once a
second and keeps the last 21 prices of `BOT_TICKER`. When the 5-poll average
crosses **above** the 20-poll average (a *golden cross* — momentum turning up)
it buys `BOT_QTY` shares; when it crosses back **below** (a *death cross*) it
reads `/api/portfolio/` and sells everything it holds. The crossover check is
the pure function `crossed(prices, fast, slow)` — no I/O, unit-tested in
`backend/tests/test_example_bot.py`.

Expect ~20 seconds of silence at startup while price history accumulates, then:

```text
[bot] trading NVDA on http://localhost:8000 -- Ctrl-C to stop
[bot] golden cross -> buy 2.0 NVDA
[bot] death cross -> sell 2.0 NVDA
```

If the bot trips a guardrail or the rate limiter it prints the server's reason
(403/429) and backs off for 10 seconds — watch the `denied` / `rate_limited`
rows appear in the audit ledger. Stop it anytime with Ctrl-C; your positions
stay on the books.

## Step 5 — Watch the arena

Open <http://localhost:8000/arena>. The season leaderboard ranks every account
by portfolio performance — accounts driven by hand, by the AI chat copilot, and
by external programs like this bot all compete on the same board. Log in as a
second user, tell the chat assistant to trade, and race your own bot.

## Ideas to build next

- Change `FAST`/`SLOW` or the poll interval and compare results across seasons
- Use `/api/portfolio/orders` (see `/api/docs`) for limit/stop orders instead
  of market fills
- Track several tickers and rank them by momentum before buying
