# FinAlly — Platform Roadmap (post-realism horizons)

Predecessor: FRONTEND_REALISM.md (complete, 2026-07-06). The platform now has
day-change semantics, candlestick charts with volume, bid/ask spread fills,
history backfill, market/limit orders with a background fill engine, a market
event feed the AI can see, and a full blotter. What follows is the next
horizon, organized as four milestones sized like the realism batches
(each = one focused phase with a backend agent + frontend workstream).

Guiding principle: this is the capstone of an **agentic AI course** — the
highest-value work is where the AI stops being a chatbot and becomes an agent
operating the platform (M2). Trading mechanics (M1) are cheap wins that M2
builds on. Market-world depth (M3) makes the sim feel alive. Multi-user (M4)
is the biggest architectural step — the schema has been user_id-ready since
day one.

---

## P0 — Maintenance debt (DONE — 2026-07-06: E2E orders/ticker specs added,
## key rotated by the user, console script removed)

| Item | Why | Effort |
|---|---|---|
| E2E specs for limit orders (place→rest→cancel; marketable→fill), Orders/Fills tabs, news ticker presence | The flagship features have zero E2E enforcement | S |
| Rotate the OpenRouter key | Flagged compromised 2026-06; still unrotated (manual, openrouter.ai) | S |
| `pyproject.toml` broken `finally-server` console script | Invoking it fails; unused | S |

## M1 — Advanced orders & risk rails (DONE — 2026-07-06, commits 6391296/e600c1f)

Everything extends the existing `orders` table + 1s fill loop.

1. **Stop-loss / take-profit / stop-limit** — new `order_kind` column
   (`limit` | `stop` | `stop_limit`) + `stop_price`. Trigger semantics in the
   fill loop: stop-sell arms when bid <= stop_price (then market-fill or
   become a limit order). UI: order-type dropdown grows; Orders tab shows
   trigger prices. The classic "protect my position" workflow. (M)
2. **Time-in-force** — `DAY` vs `GTC` + `expires_at`; the fill loop expires
   DAY orders at session close (needs M3.1's session clock; until then, 24h
   TTL). Status gains `expired`. (S)
3. **Fees & slippage toggle** — env-driven commission (e.g. $0 default,
   configurable bps) and volume-aware slippage on market orders; surfaced in
   fill toasts and the blotter as an explicit cost column. Teaches why
   over-trading loses. (S)
4. **Realized P&L ledger** — per-fill realized P&L computed against avg cost
   at sale time, stored on the trade row; Fills tab gains a Realized column;
   Header/portfolio summary splits Realized vs Unrealized. (M)
5. **Risk rails** — configurable single-position concentration warning
   (e.g. >40% of portfolio) surfaced in the UI before order submit and as a
   watchlist badge; no hard blocks (it's a sim). (S)

## M2 — The AI becomes an agent (flagship milestone) — COMPLETE 2026-07-06
### M2.1+2.2: 56e497a/7db0cd3 · M2.3+2.4: 27c7df2/37a6b32
### Known refinement: constrain brief prompts to supported actions (no shorting)

1. **AI places advanced orders** (DONE) — extend the chat structured-output schema:
   `orders: [{ticker, side, quantity, kind, limit_price?, stop_price?}]`
   alongside `trades`. "Buy 10 AAPL if it drops to 180 and protect it with a
   stop at 170" becomes two resting orders from one sentence. (M)
2. **Standing rules engine** (DONE) — user states a rule in chat ("if NVDA drops 3%
   in a day, buy 5"); the AI emits a structured `rules` action; a backend
   rules table + evaluator (piggybacks on the fill-loop cadence) executes and
   logs activations; Rules panel in the UI with enable/disable/delete. This is
   the single most course-relevant feature: durable, user-authored agency. (L)
3. **Event-driven AI briefs** — when the event feed fires for a ticker the
   user holds/watches, generate a one-line AI take (throttled, e.g. max
   1/min) pushed into the chat panel as an unsolicited "brief" message type.
   Requires marking assistant-initiated messages in chat_messages. (M)
4. **Daily AI review** — a scheduled (or on-demand "review my day") report:
   trades made, realized/unrealized P&L, best/worst decision, rule
   activations; rendered as a rich chat message. (S, after M1.4)

## M3 — A living market world — COMPLETE 2026-07-06
### Wave 1 (sessions/settlement/crypto): 811053e/34c1d6f · Wave 2 (narratives/bursts/analytics): 983852c/7a44cb3

1. **Sessions & settlement** — a sim clock with open/close (accelerated or
   real-time), closing auction that stamps the official close → `prev_close`
   becomes *yesterday's actual close* instead of the seed price; pre/post
   market visual state; Header session badge replaces the static SIM 24/7. (M)
2. **LLM-generated news narratives** — today's headlines are templated
   ("surges +3.4%"); pipe events through the LLM (mockable) to generate
   flavor ("NVDA jumps on rumored datacenter win") with a cached/backoff
   path; sector-correlated event bursts (tech-wide selloffs). (M)
3. **More asset classes** — a crypto set (BTC, ETH — 24/7, higher vol
   parameters, 4+ decimal price precision) proves the multi-asset plumbing;
   optional ETF basket whose price derives from constituents. (M)
4. **Analytics page** — new route/panel: drawdown curve, Sharpe (from
   snapshots), win rate, sector allocation donut (static ticker→sector map),
   holding-period distribution. Read-only, pure frontend + one summary
   endpoint. (M)

## M4 — Multi-user arena — COMPLETE 2026-07-06 (8b9770f/93ccae9)

**THE ROADMAP IS COMPLETE.** All four milestones shipped. M4.4's Postgres
migration path remains documented-not-built by design (SQLite + WAL + BEGIN
IMMEDIATE holds at classroom scale); Terraform deploy stays a stretch goal.

The schema carried `user_id` from day one; this milestone cashes that in.

1. **Lightweight identity** — name-only login (cookie session), no passwords
   (it's a sim); every route scopes by the session user; per-user seed $10k. (L)
2. **Leaderboard** — return % since season start, computed from snapshots;
   a public board panel. (S, after 4.1)
3. **Seasons** — admin reset that archives portfolios and restarts everyone
   at $10k. (S)
4. **Infra step-up** — SQLite stays fine for classroom scale (WAL + the
   existing BEGIN IMMEDIATE discipline); document the Postgres migration path
   but don't build it until concurrency hurts. Optional: deploy/ Terraform for
   App Runner (PLAN.md stretch goal). (M)

## M5 — Strategy backtester — COMPLETE 2026-07-07 (65757bf/680804e)
## (post-roadmap; inspired by tickflow-stock-panel)

Contract: `planning/M5_BACKTEST_CONTRACT.md`. Closes the M2 loop: AI
proposes a rule → backtest validates it on simulated history → user arms
it live. Stateless compute — no schema migration.
Shipped: pytest 621 / jest 174 / E2E 11-of-11 in docker; live real-LLM
check parsed a one-sentence Chinese ask into an exact 10-run Monte Carlo
backtest instruction.

1. **Backtest engine** — dependency-free per-bar state machine over
   synthetic GBM history (ticker's own mu/sigma, seeded/reproducible):
   daily re-armed buy-entry trigger (rules-engine semantics), conservative
   intrabar TP/SL exits (SL first), spread+commission fill math, equity
   curve vs buy-and-hold baseline, rejection counters. (L)
2. **`POST /api/backtest`** — synchronous endpoint; Monte Carlo `runs`
   (1–50 consecutive seeds) with median-representative run + p5/p95
   distribution summary. (S)
3. **AI runs backtests** — chat structured output gains `backtests`;
   outcomes render as chat badges with compact stats. (M)
4. **Backtest tab** — config form (prefillable from a rule's "test"
   button), stat cards, equity-vs-baseline chart, trades list. (M)

## Explicitly out of scope (unchanged judgment)

- Options chains, real L2 order book / matching engine, HFT-style latency work
- Real-money brokerage integration of any kind
- Full margin/short-selling math (a simplified "borrow at flat rate" short
  could be a M3+ stretch, but it doubles portfolio-math complexity)
- WebSocket migration — SSE remains sufficient

## Sequencing & effort summary

| Milestone | Theme | Effort | Depends on |
|---|---|---|---|
| P0 | Test debt + key rotation | S | — |
| M1 | Advanced orders & risk | M | — |
| M2 | AI agency (rules, advanced-order chat, briefs) | L | M1 (order kinds) |
| M3 | Market world (sessions, news, crypto, analytics) | M-L | M1.2 benefits from M3.1 |
| M4 | Multi-user arena | L | independent, best last |

Recommended order: P0 → M1 → M2 (the payoff) → M3 → M4. Each milestone ships
independently; stop anywhere and the platform is still coherent.
