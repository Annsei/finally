"""Chat API routes for FinAlly.

Provides:
- POST /api/chat — LLM-powered chat with structured output; auto-executes
  trades, advanced orders (limit/stop/stop_limit — M2.1), standing rules
  (M2.2), strategy backtests (M5 — stateless engine runs, compact outcomes),
  and watchlist changes; persists conversation history to chat_messages.
- GET /api/chat — last 20 chat_messages rows of every kind
  ('chat' | 'brief' | 'review' | 'rule').
- POST /api/chat/review — on-demand daily AI review (M2.4): summarizes
  today's trades, rule firings, P&L, and market events as a plain-text
  assistant message stored with kind='review'.

All routes are created via the factory function ``create_chat_router`` which
closes over the shared ``PriceCache`` instance and the database path.

When ``LLM_MOCK=true`` the endpoints return deterministic responses without
network calls (D-06/D-07).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.backtest import STARTING_CASH, normalize_backtest_config, run_backtest
from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.profiles import MarketProfile
from app.market.session import SessionClock

# Bind the profile-aware placement impl to the legacy module name: chat
# auto-exec must pass a market ``profile`` (CN-2), and existing rollback tests
# monkeypatch ``chat.place_order_on_conn``. The public wrapper stays frozen in
# app.routes.orders for pre-CN-2 callers.
from app.routes.orders import _place_order_on_conn as place_order_on_conn
from app.routes.portfolio import (
    _execute_trade_on_conn as execute_trade_on_conn,
)
from app.routes.portfolio import (
    _record_snapshot,
)
from app.routes.rules import create_rule_on_conn
from app.routes.watchlist import (
    apply_watchlist_change_on_conn,
    sync_market_source,
    ticker_watched_by_anyone,
)

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}

# System prompt for the assistant (M2: the AI is an agent — beyond immediate
# market trades it can place resting limit/stop/stop_limit orders and create
# standing rules). The portfolio context is appended per-request.
SYSTEM_PROMPT = (
    "You are FinAlly, an AI trading assistant. Be concise and data-driven. "
    "Execute trades when asked. Always respond with valid structured JSON.\n\n"
    "You act through five action arrays in your JSON response:\n"
    "- 'trades': immediate market orders {ticker, side, quantity}.\n"
    "- 'orders': resting limit/stop/stop-limit orders {ticker, side, quantity, "
    "kind, limit_price?, stop_price?, time_in_force?}. kind is one of 'limit' "
    "(limit_price required), 'stop' (stop_price required, market-on-trigger), "
    "'stop_limit' (both required). time_in_force is 'gtc' (default) or 'day'. "
    "Use resting orders whenever the user names a trigger price: 'buy X if it "
    "drops to Y' means a limit buy with limit_price Y; 'protect it with a stop "
    "at Z' means a sell stop with stop_price Z; 'take profit at W' means a "
    "limit sell at W. A sell stop must be below the current price, a buy stop "
    "above it. Limit orders already marketable at placement fill immediately.\n"
    "- 'rules': standing one-shot automations {ticker, trigger_type, "
    "threshold, side, quantity, description} evaluated continuously against "
    "live quotes. trigger_type is exactly one of 'price_above', 'price_below', "
    "'day_change_pct_above', 'day_change_pct_below'. threshold is a dollar "
    "price for price_* triggers and a percent for day_change_pct_* triggers "
    "(negative for drops — 'if NVDA drops 3% today, buy 5' means trigger_type "
    "'day_change_pct_below' with threshold -3). When a rule fires it executes "
    "a market trade once and moves to status 'fired' until the user re-arms "
    "it. Always write a clear human-readable description, e.g. "
    "'Buy 5 NVDA when day change <= -3%'.\n"
    "- 'backtests': strategy backtests {ticker, trigger_type, threshold, "
    "quantity, take_profit_pct?, stop_loss_pct?, days?, runs?} simulated "
    "against synthetic history. Buy-entry only — there is no side field; "
    "exits are modeled with take_profit_pct/stop_loss_pct (percent "
    "above/below entry, each > 0 when given). trigger_type/threshold use "
    "the same semantics as 'rules'. days defaults to 30 (5-120), runs to 1 "
    "(1-50 Monte Carlo re-runs). Use this when the user asks to backtest a "
    "strategy ('backtest', '回测') or how a rule/strategy would have "
    "performed.\n"
    "- 'watchlist_changes': {ticker, action} with action 'add' or 'remove'."
)

# System prompt for the daily review (M2.4) — plain text, no structured output.
REVIEW_SYSTEM_PROMPT = (
    "You are FinAlly, an AI trading assistant, writing the user's daily "
    "review. Reply in plain text (no JSON, no markdown headings), 3-6 "
    "sentences: what happened today, the best and worst decision, and one "
    "concrete suggestion. Be concise and data-driven."
)

# Chinese (zh-CN) variant of SYSTEM_PROMPT, selected when the active market
# profile's locale is 'zh-CN' (CN-3). It injects A-share trading constraints
# (整手 100-share lots, T+1 settlement, ¥ currency, 涨跌停 price limits, 印花税
# stamp tax) so the AI never emits trades the backend would reject, while the
# structured-output schema keys and enum values stay ENGLISH (trades / orders /
# rules / backtests / watchlist_changes, and every kind/trigger_type/action/
# side value) — the frontend and validators need zero adaptation, only the
# conversational message language changes.
SYSTEM_PROMPT_ZH = (
    "你是 FinAlly，一个 AI 交易助手。回答简洁、以数据为依据。用户要求时执行交易。"
    "始终以合法的结构化 JSON 回复。\n\n"
    "这是中国 A 股市场（沪深两市）。你建议或执行的所有操作都必须符合 A 股交易"
    "规则，否则会被系统拒绝：\n"
    "- 买入数量必须为整手，即 100 股的整数倍（1 手 = 100 股）；卖出可以是任意"
    "股数（含零股）。\n"
    "- T+1 交收：当日买入的股票，次日方可卖出。\n"
    "- 货币单位为人民币 ¥；卖出需缴纳印花税，买卖双向收取佣金。\n"
    "- 个股有涨跌停限制（主板 ±10%，创业板/科创板 ±20%）；委托价触及涨停或"
    "跌停时可能无法成交。\n\n"
    "你通过 JSON 响应中的五个动作数组来行动。数组的键名与所有枚举值一律使用"
    "英文原文，切勿翻译：\n"
    "- 'trades'：即时市价单 {ticker, side, quantity}。\n"
    "- 'orders'：挂单 limit/stop/stop-limit 委托 {ticker, side, quantity, kind, "
    "limit_price?, stop_price?, time_in_force?}。kind 取 'limit'（须给 "
    "limit_price）、'stop'（须给 stop_price，触发后市价成交）、'stop_limit'"
    "（两者都给）之一。time_in_force 取 'gtc'（默认）或 'day'。只要用户给出触发"
    "价就用挂单：“跌到 Y 就买”表示以 limit_price Y 的限价买单；“在 Z 挂止损"
    "保护”表示以 stop_price Z 的卖出止损单；“到 W 止盈”表示在 W 的限价卖单。"
    "卖出止损价须低于现价，买入止损价须高于现价。挂单时已可成交的限价单会立即"
    "成交。\n"
    "- 'rules'：常驻的一次性自动化规则 {ticker, trigger_type, threshold, side, "
    "quantity, description}，持续对实时行情求值。trigger_type 恰好取 "
    "'price_above'、'price_below'、'day_change_pct_above'、'day_change_pct_below' "
    "之一。threshold 对 price_* 触发是价格（¥），对 day_change_pct_* 触发是百分比"
    "（下跌为负——“000858 今天跌 3% 就买 100 股”对应 trigger_type "
    "'day_change_pct_below'、threshold -3）。规则触发时执行一次市价交易，随后"
    "置为 'fired' 状态，直到用户重新启用。务必写清楚人类可读的 description，"
    "例如“当日跌幅 <= -3% 时买入 100 股 000858”。\n"
    "- 'backtests'：策略回测 {ticker, trigger_type, threshold, quantity, "
    "take_profit_pct?, stop_loss_pct?, days?, runs?}，在合成历史上模拟。仅支持"
    "买入建仓——没有 side 字段；退出用 take_profit_pct/stop_loss_pct 建模（相对"
    "建仓价上/下浮的百分比，给定时均须 > 0）。trigger_type/threshold 语义同 "
    "'rules'。days 默认 30（5-120），runs 默认 1（1-50 次蒙特卡洛重跑）。当用户"
    "要求回测某策略（“回测”“backtest”）或询问某规则/策略过去表现时使用。\n"
    "- 'watchlist_changes'：{ticker, action}，action 取 'add' 或 'remove'。"
)

# Chinese (zh-CN) variant of REVIEW_SYSTEM_PROMPT (CN-3) — plain-text A-share
# daily review; currency is ¥. Selected on locale 'zh-CN'.
REVIEW_SYSTEM_PROMPT_ZH = (
    "你是 FinAlly，一个 AI 交易助手，正在撰写用户的每日复盘。用纯文本回复"
    "（不要 JSON、不要 markdown 标题），3-6 句话：今天发生了什么、最好与最差"
    "的决策、以及一条具体的建议。回答简洁、以数据为依据。这是 A 股市场，货币"
    "为 ¥。"
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str


class TradeInstruction(BaseModel):
    ticker: str
    side: str  # "buy" | "sell"
    quantity: float


class WatchlistChange(BaseModel):
    ticker: str
    action: str  # "add" | "remove"


class OrderInstruction(BaseModel):
    """An advanced order the LLM asks to place (M2.1) — POST /orders fields."""

    ticker: str
    side: str  # "buy" | "sell"
    quantity: float
    kind: str  # "limit" | "stop" | "stop_limit"
    limit_price: float | None = None
    stop_price: float | None = None
    # None tolerated (strict structured-output modes emit null for optionals);
    # place_order_on_conn normalizes None/empty to "gtc".
    time_in_force: str | None = None  # "day" | "gtc" (default "gtc")


class RuleInstruction(BaseModel):
    """A standing rule the LLM asks to create (M2.2) — POST /rules fields."""

    ticker: str
    trigger_type: str  # price_above | price_below | day_change_pct_above | day_change_pct_below
    threshold: float
    side: str  # "buy" | "sell"
    quantity: float
    description: str | None = None


class BacktestInstruction(BaseModel):
    """A strategy backtest the LLM asks to run (M5) — POST /backtest fields.

    Buy-entry only, so there is no side field; exits are modeled with
    take_profit_pct/stop_loss_pct. None days/runs fall back to the engine
    defaults (30 days, 1 run).
    """

    ticker: str
    trigger_type: str  # price_above | price_below | day_change_pct_above | day_change_pct_below
    threshold: float
    quantity: float
    take_profit_pct: float | None = None
    stop_loss_pct: float | None = None
    days: int | None = None
    runs: int | None = None


class ChatResponse(BaseModel):
    message: str
    trades: list[TradeInstruction] = []
    watchlist_changes: list[WatchlistChange] = []
    orders: list[OrderInstruction] = []
    rules: list[RuleInstruction] = []
    backtests: list[BacktestInstruction] = []


# ---------------------------------------------------------------------------
# Context assembly helper
# ---------------------------------------------------------------------------


def _assemble_portfolio_context(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    user_id: str = "default",
) -> str:
    """Build a compact portfolio context string for injection into the system prompt.

    Reads current cash, positions, and watchlist from the open connection and
    enriches both positions and watchlist tickers with live prices from the
    price cache (spec §9: "watchlist with live prices").

    Args:
        conn: An open SQLite connection (caller manages lifecycle).
        price_cache: Live price cache for current market prices.

    Returns:
        Multi-line string with cash, total value, positions table, watchlist
        with current prices, and (when any exist) the newest market events so
        the assistant can reference sudden moves.
    """
    # Cash balance
    user_row = conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
    ).fetchone()
    cash: float = user_row["cash_balance"] if user_row else 0.0

    # Positions with P&L
    position_rows = conn.execute(
        "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ?",
        (user_id,),
    ).fetchall()

    lines: list[str] = []
    market_value = 0.0
    for row in position_rows:
        ticker: str = row["ticker"]
        quantity: float = row["quantity"]
        avg_cost: float = row["avg_cost"]
        current_price: float = price_cache.get_price(ticker) or 0.0
        pnl = (current_price - avg_cost) * quantity
        pnl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0
        market_value += quantity * current_price
        lines.append(
            f"{ticker} | qty {quantity} | avg {avg_cost:.2f} | cur {current_price:.2f}"
            f" | pnl {pnl:.2f} | pnl% {pnl_pct:.2f}"
        )

    total = cash + market_value
    positions_block = "\n".join(lines) if lines else "(no open positions)"

    # Watchlist tickers enriched with live prices from the cache (spec §9)
    watchlist_rows = conn.execute(
        "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at ASC",
        (user_id,),
    ).fetchall()
    watchlist_parts: list[str] = []
    for r in watchlist_rows:
        wl_ticker: str = r["ticker"]
        wl_price = price_cache.get_price(wl_ticker)
        watchlist_parts.append(
            f"{wl_ticker} ${wl_price:.2f}" if wl_price is not None else f"{wl_ticker} (no price)"
        )
    watchlist_tickers = ", ".join(watchlist_parts)

    context = (
        f"Cash: ${cash:.2f}\n"
        f"Total portfolio value: ${total:.2f}\n"
        f"Positions (ticker | qty | avg_cost | current_price | pnl | pnl%):\n"
        f"{positions_block}\n"
        f"Watchlist: {watchlist_tickers}"
    )

    # Recent market events (sudden >=1% tick moves detected in the cache
    # funnel). Appended only when events exist so quiet markets add nothing
    # to the prompt.
    events = price_cache.get_events(limit=5)
    if events:
        event_lines = "\n".join(
            f"{datetime.fromtimestamp(e.timestamp, tz=timezone.utc):%H:%M:%S} UTC - {e.headline}"
            for e in events
        )
        context += f"\nRecent market events:\n{event_lines}"

    return context


def _assemble_review_context(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    user_id: str = "default",
) -> tuple[str, int]:
    """Build the daily-review context string for the M2.4 review prompt.

    Gathers, for today's UTC date: executed trades (side/qty/price/commission/
    realized P&L), rule firings (rules whose last_fired_at falls today, with
    their descriptions), the current portfolio (cash, positions with
    unrealized P&L, total value, lifetime realized P&L), day P&L per position
    (qty x (price - prev_close) from the cache), and up to 5 of today's
    market events.

    Args:
        conn: An open SQLite connection (caller manages lifecycle).
        price_cache: Live price cache for current prices and prev_close.

    Returns:
        (context, trade_count) — the multi-line context string and the number
        of trades executed today (the LLM_MOCK review interpolates the count).
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Today's trades
    trade_rows = conn.execute(
        """
        SELECT ticker, side, quantity, price, commission, realized_pnl
        FROM trades
        WHERE user_id = ? AND substr(executed_at, 1, 10) = ?
        ORDER BY executed_at ASC, rowid ASC
        """,
        (user_id, today),
    ).fetchall()
    trade_lines = [
        f"{t['side']} {t['quantity']:g} {t['ticker']} @ ${t['price']:.2f}"
        f" | commission ${t['commission']:.2f}"
        + (
            f" | realized P&L ${t['realized_pnl']:+.2f}"
            if t["realized_pnl"] is not None
            else ""
        )
        for t in trade_rows
    ]
    trades_block = "\n".join(trade_lines) if trade_lines else "(no trades today)"

    # Today's rule firings
    rule_rows = conn.execute(
        """
        SELECT description, last_fired_at
        FROM rules
        WHERE user_id = ? AND last_fired_at IS NOT NULL
              AND substr(last_fired_at, 1, 10) = ?
        ORDER BY last_fired_at ASC
        """,
        (user_id, today),
    ).fetchall()
    rules_block = (
        "\n".join(f"{r['description']} (fired {r['last_fired_at']})" for r in rule_rows)
        if rule_rows
        else "(no rules fired today)"
    )

    # Current portfolio: cash, positions with unrealized + day P&L, totals
    user_row = conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
    ).fetchone()
    cash: float = user_row["cash_balance"] if user_row else 0.0
    realized_row = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl), 0.0) AS total FROM trades "
        "WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    lifetime_realized = round(realized_row["total"] or 0.0, 2)

    position_rows = conn.execute(
        "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    position_lines: list[str] = []
    market_value = 0.0
    for row in position_rows:
        ticker: str = row["ticker"]
        quantity: float = row["quantity"]
        avg_cost: float = row["avg_cost"]
        quote = price_cache.get(ticker)
        current_price = quote.price if quote else 0.0
        unrealized = (current_price - avg_cost) * quantity
        # Day P&L: what the position gained/lost today vs the previous close.
        day_pnl = quantity * (current_price - quote.prev_close) if quote else 0.0
        market_value += quantity * current_price
        position_lines.append(
            f"{ticker} | qty {quantity:g} | avg {avg_cost:.2f} | cur {current_price:.2f}"
            f" | unrealized {unrealized:+.2f} | day P&L {day_pnl:+.2f}"
        )
    positions_block = "\n".join(position_lines) if position_lines else "(no open positions)"
    total = cash + market_value

    # Today's market events (up to 5, newest first)
    today_events = [
        e
        for e in price_cache.get_events()
        if datetime.fromtimestamp(e.timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        == today
    ][:5]
    events_block = (
        "\n".join(e.headline for e in today_events)
        if today_events
        else "(no market events today)"
    )

    context = (
        f"Date: {today} (UTC)\n"
        f"Today's trades (side qty ticker @ price):\n{trades_block}\n"
        f"Rules fired today:\n{rules_block}\n"
        f"Cash: ${cash:.2f}\n"
        f"Total portfolio value: ${total:.2f}\n"
        f"Lifetime realized P&L: ${lifetime_realized:+.2f}\n"
        f"Positions (ticker | qty | avg_cost | current | unrealized | day P&L):\n"
        f"{positions_block}\n"
        f"Today's market events:\n{events_block}"
    )
    return context, len(trade_rows)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_chat_router(
    price_cache: PriceCache,
    db_path: str,
    commission_bps: float = 0.0,
    session_clock: SessionClock | None = None,
    profile: MarketProfile | None = None,
) -> APIRouter:
    """Factory: build the chat APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.
        commission_bps: Commission in basis points of notional applied to every
            fill — chat-executed trades pay the same commission as manual ones
            (FINALLY_COMMISSION_BPS, read once at app startup in main.py).
        session_clock: Session clock (M3.1) — chat-executed market trades on
            equities inherit the same "Market closed" rejection as manual
            trades via the shared ``execute_trade_on_conn`` helper (returned
            as a failed trade outcome, never an HTTP error).
        profile: Active market profile (CN-2) — AI-executed trades, orders,
            rules, and backtests obey the same 整手/涨跌停/T+1/fee mechanics as
            the REST routes, threaded through the shared helpers. None/us is a
            no-op.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/chat", tags=["chat"])
    # CN-3: select the AI's conversational language by the profile locale. With
    # no profile or an 'en-US' locale the existing English prompts and English
    # LLM_MOCK text are used byte-for-byte; only a 'zh-CN' locale switches to
    # the Chinese prompts and the Chinese deterministic mock branches. The
    # structured-output schema is unaffected — action keys and enums stay
    # English in both languages.
    is_zh = profile is not None and profile.locale == "zh-CN"
    system_prompt = SYSTEM_PROMPT_ZH if is_zh else SYSTEM_PROMPT
    review_system_prompt = REVIEW_SYSTEM_PROMPT_ZH if is_zh else REVIEW_SYSTEM_PROMPT
    universe = profile.universe if profile is not None else None
    # Mirror routes/backtest.py: AI-run backtests open the same account the
    # REST route does — the active profile's seed cash (CN=¥100k), else the
    # US $10,000. None/us keeps run_backtest's default value-for-value.
    starting_cash = profile.seed_cash if profile is not None else STARTING_CASH

    @router.get("/")
    async def get_chat_history(request: Request) -> dict:
        """Return last 20 chat messages in ascending chronological order.

        Query selects DESC then reverses so the response is ascending by created_at.
        The ``actions`` field is parsed from its stored JSON string to a dict (or None).
        Every kind is returned ('chat' | 'brief' | 'review' | 'rule') — the
        frontend labels non-conversation messages by their ``kind``.

        Returns:
            {"messages": [{"role", "content", "actions", "kind", "created_at"}, ...]}
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                """
                SELECT role, content, actions, kind, created_at
                FROM chat_messages
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id,),
            ).fetchall()
            messages = list(reversed([
                {
                    "role": row["role"],
                    "content": row["content"],
                    "actions": json.loads(row["actions"]) if row["actions"] else None,
                    "kind": row["kind"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]))
            return {"messages": messages}
        finally:
            conn.close()

    @router.post("/")
    async def chat(body: ChatRequest, request: Request) -> dict:
        """Process a chat message: call LLM (or mock), auto-execute actions, persist.

        Returns structured JSON with the assistant message, trade outcomes,
        watchlist change outcomes, and — when the turn contained them —
        advanced-order outcomes ("orders": full order JSON for placed/filled,
        failed dict otherwise), rule outcomes ("rules":
        {"status": "created", "rule": {...}} or failed dict), and backtest
        outcomes ("backtests": compact {"status": "completed", ticker,
        config, stats} — never curves/trades — or failed dict, M5). Per-action
        validation failures are returned as outcome dicts (status=failed) —
        they do NOT raise HTTP errors and do not abort the remaining actions.

        Ordering: watchlist changes are applied BEFORE trades, and each
        successful "add" is registered with the live market source
        immediately (add_ticker seeds the price cache) so a single turn like
        "add PYPL and buy 5 shares" finds a price at trade time. Advanced
        orders are placed AFTER trades, then standing rules are created,
        then backtests run (stateless — they touch no tables). Market source
        removals stay AFTER the commit so an in-flight turn cannot lose
        prices and a rollback cannot orphan the source state.

        Transaction boundary: all executed trades, placed orders (including
        immediate marketable fills), created rules, applied watchlist changes,
        and both chat_messages rows are committed atomically in ONE commit. An
        unexpected error anywhere before that commit rolls back everything —
        no half-applied chat turns — and any pre-commit market source adds are
        reconciled back to match the DB (best-effort). After the commit, a
        portfolio snapshot is recorded (own commit) and watchlist removals are
        synced to the live market data source (best-effort; DB is the source
        of truth).

        LLM errors (when LLM_MOCK=false) return HTTP 500 with
        ``{"error": "LLM unavailable"}``.
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        # Tickers registered with the market source before the commit this
        # turn — used to reconcile the source if the transaction rolls back.
        source_adds: list[str] = []
        try:
            # Step 1: Load conversation history (D-04). Assistant-initiated
            # rows (event briefs, daily reviews, rule activations — M2.3/M2.4)
            # are excluded from the LLM's conversation window: they are
            # notifications, not turns the user replied to, and would drown
            # the recent history. GET /api/chat/ still returns every kind.
            rows = conn.execute(
                "SELECT role, content FROM chat_messages "
                "WHERE user_id = ? "
                "AND kind NOT IN ('brief', 'rule', 'review') "
                "ORDER BY created_at DESC LIMIT 20",
                (user_id,),
            ).fetchall()
            history = list(reversed(rows))

            # Step 2: Assemble portfolio context (D-01/D-02)
            context = _assemble_portfolio_context(conn, price_cache, user_id)

            # Step 3: Build messages list for LLM
            messages: list[dict] = [
                {
                    "role": "system",
                    "content": f"{system_prompt}\n\nCurrent portfolio:\n{context}",
                }
            ]
            messages.extend(
                {"role": r["role"], "content": r["content"]} for r in history
            )
            messages.append({"role": "user", "content": body.message})

            # Step 4: Get LLM response — mock path (D-06/D-07) or real LiteLLM call
            if os.getenv("LLM_MOCK", "false").lower() == "true":
                # Construct deterministic responses; fall through to auto-exec
                # (D-07). On a 'zh-CN' locale (CN-3) the Chinese mock branch
                # translates ONLY the conversational message — the action
                # arrays (tickers, sides, quantities) are byte-identical to the
                # US mocks below, so downstream execution is unchanged and the
                # profile still governs it (e.g. the buy-5-AAPL non-lot order is
                # rejected under the CN 整手 rule). The '回测'/'backtest' keyword
                # both route to the backtest branch. With no/US locale the
                # English payload below is byte-identical to today (the existing
                # E2E command depends on it).
                if is_zh:
                    if "backtest" in body.message.lower() or "回测" in body.message:
                        parsed = ChatResponse(
                            message=(
                                "[模拟] 回测完成：已在 20 个模拟交易日上测试 NVDA "
                                "逢跌买入策略。"
                            ),
                            backtests=[
                                BacktestInstruction(
                                    ticker="NVDA",
                                    trigger_type="day_change_pct_below",
                                    threshold=-3,
                                    quantity=5,
                                    take_profit_pct=5,
                                    stop_loss_pct=3,
                                    days=20,
                                    runs=1,
                                )
                            ],
                        )
                    else:
                        parsed = ChatResponse(
                            message="已将 PYPL 加入你的自选，并为你买入 5 股 AAPL。",
                            trades=[
                                TradeInstruction(ticker="AAPL", side="buy", quantity=5)
                            ],
                            watchlist_changes=[
                                WatchlistChange(ticker="PYPL", action="add")
                            ],
                        )
                # Messages mentioning "backtest" get the M5 backtest mock;
                # everything else keeps the original PYPL/AAPL payload
                # byte-identical for the E2E suite.
                elif "backtest" in body.message.lower():
                    parsed = ChatResponse(
                        message=(
                            "[MOCK] Backtest complete: NVDA dip-buy strategy "
                            "tested over 20 simulated days."
                        ),
                        backtests=[
                            BacktestInstruction(
                                ticker="NVDA",
                                trigger_type="day_change_pct_below",
                                threshold=-3,
                                quantity=5,
                                take_profit_pct=5,
                                stop_loss_pct=3,
                                days=20,
                                runs=1,
                            )
                        ],
                    )
                else:
                    parsed = ChatResponse(
                        message=(
                            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
                        ),
                        trades=[TradeInstruction(ticker="AAPL", side="buy", quantity=5)],
                        watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
                    )
            else:
                from litellm import completion  # lazy import — never reached when mocked

                try:
                    response = await asyncio.to_thread(
                        completion,
                        model=MODEL,
                        messages=messages,
                        response_format=ChatResponse,
                        reasoning_effort="low",
                        extra_body=EXTRA_BODY,
                    )
                    parsed = ChatResponse.model_validate_json(
                        response.choices[0].message.content
                    )
                except Exception:
                    logger.exception("LLM call/parse failed")
                    return JSONResponse(
                        status_code=500, content={"error": "LLM unavailable"}
                    )

            # Step 5: Auto-execute watchlist changes FIRST (DB writes join the
            # single transaction committed in Step 7). Each successful "add"
            # is registered with the live market source immediately —
            # add_ticker seeds the price cache, so a same-turn trade on a
            # brand-new ticker (e.g. "add PYPL and buy 5 shares") finds a
            # price in Step 6. Market source REMOVALS are deferred to Step 9
            # (post-commit) so an in-flight turn keeps its prices and a
            # rollback cannot orphan the source state.
            watch_outcomes: list[dict] = []
            for w in parsed.watchlist_changes:
                outcome = apply_watchlist_change_on_conn(
                    conn, w.ticker, w.action, user_id=user_id
                )
                watch_outcomes.append(outcome)
                if outcome["status"] == "added":
                    source_adds.append(outcome["ticker"])
                    await sync_market_source(request, outcome["ticker"], "add")

            # Step 6: Auto-execute trades (T-02-05/T-02-06 — ticker normalized in
            # helper). Helpers do NOT commit — everything joins one transaction
            # committed in Step 7. Per-trade validation failures return outcome
            # dicts and never abort the batch.
            trade_outcomes = [
                execute_trade_on_conn(
                    conn,
                    price_cache,
                    t.ticker.strip().upper(),
                    t.side.lower(),
                    t.quantity,
                    commission_bps=commission_bps,
                    session_clock=session_clock,
                    user_id=user_id,
                    profile=profile,
                )
                for t in parsed.trades
            ]

            # Step 6b (M2.1): Place advanced orders AFTER trades, on the same
            # shared connection/transaction — order rows (and any immediate
            # marketable fill + its snapshot) commit atomically with the rest
            # of the turn in Step 7. Per-order validation failures return
            # {"status": "failed", ...} dicts and never abort the batch;
            # placed/filled orders yield their full public order JSON.
            order_outcomes = [
                place_order_on_conn(
                    conn,
                    price_cache,
                    ticker=o.ticker,
                    side=o.side,
                    quantity=o.quantity,
                    kind=o.kind,
                    limit_price=o.limit_price,
                    stop_price=o.stop_price,
                    time_in_force=o.time_in_force,
                    commission_bps=commission_bps,
                    user_id=user_id,
                    profile=profile,
                )
                for o in parsed.orders
            ]

            # Step 6c (M2.2): Create standing rules on the shared connection —
            # same single-transaction semantics and non-fatal per-rule
            # failures as trades and orders.
            rule_outcomes = [
                create_rule_on_conn(
                    conn,
                    price_cache,
                    ticker=r.ticker,
                    trigger_type=r.trigger_type,
                    threshold=r.threshold,
                    side=r.side,
                    quantity=r.quantity,
                    description=r.description,
                    user_id=user_id,
                    profile=profile,
                )
                for r in parsed.rules
            ]

            # Step 6d (M5): Run strategy backtests — stateless compute, so
            # nothing joins the transaction. Validation shares
            # normalize_backtest_config with POST /api/backtest (identical
            # error messages) and per-instruction failures never abort the
            # batch. Outcomes stay compact: config + stats only — curves and
            # trades are never stored in chat actions. The engine runs off
            # the event loop like the LLM call itself.
            backtest_outcomes: list[dict] = []
            for b in parsed.backtests:
                normalized = normalize_backtest_config(
                    price_cache,
                    ticker=b.ticker,
                    trigger_type=b.trigger_type,
                    threshold=b.threshold,
                    quantity=b.quantity,
                    take_profit_pct=b.take_profit_pct,
                    stop_loss_pct=b.stop_loss_pct,
                    days=b.days,
                    runs=b.runs,
                    universe=universe,
                    profile=profile,
                )
                if normalized["status"] == "failed":
                    backtest_outcomes.append(normalized)
                    continue
                result = await asyncio.to_thread(
                    run_backtest,
                    normalized["config"],
                    commission_bps=commission_bps,
                    starting_cash=starting_cash,
                    profile=profile,
                )
                backtest_outcomes.append(
                    {
                        "status": "completed",
                        "ticker": result["config"]["ticker"],
                        "config": result["config"],
                        "stats": result["stats"],
                    }
                )

            # Step 7: Persist both messages (T-02-12 — parameterized SQL)
            # Separate timestamps guarantee deterministic ORDER BY created_at ordering
            # even when both rows are written in the same request (WR-02).
            # The "orders"/"rules"/"backtests" keys are appended only when
            # the turn contained such instructions, keeping the default
            # LLM_MOCK response (no orders/rules/backtests) byte-identical
            # for the E2E suite.
            actions = {"trades": trade_outcomes, "watchlist_changes": watch_outcomes}
            if order_outcomes:
                actions["orders"] = order_outcomes
            if rule_outcomes:
                actions["rules"] = rule_outcomes
            if backtest_outcomes:
                actions["backtests"] = backtest_outcomes
            user_ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, kind, created_at) "
                "VALUES (?, ?, 'user', ?, NULL, 'chat', ?)",
                (str(uuid.uuid4()), user_id, body.message, user_ts),
            )
            asst_ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, kind, created_at) "
                "VALUES (?, ?, 'assistant', ?, ?, 'chat', ?)",
                (str(uuid.uuid4()), user_id, parsed.message, json.dumps(actions), asst_ts),
            )
            # Single atomic commit: all trades + watchlist changes + both
            # chat messages succeed or fail together.
            conn.commit()

            # Step 8: Record a portfolio snapshot if any trade executed
            # (spec §7: snapshot immediately after trade execution). Runs after
            # the main commit; best-effort — a snapshot failure must not fail
            # the already-committed chat turn.
            if any(t["status"] == "executed" for t in trade_outcomes):
                try:
                    _record_snapshot(conn, price_cache, user_id)
                    conn.commit()
                except Exception:
                    logger.exception("Post-trade snapshot failed (chat turn already committed)")

            # Step 9: Sync watchlist REMOVALS to the live market data source
            # so removed tickers stop simulating/streaming — but only when NO
            # user still watches the ticker (M4: the source tracks the union
            # of all users' watchlists). Adds were already synced in Step 5
            # (pre-trade). Runs after the commit — DB is the source of truth
            # and the sync is best-effort (failures logged inside the helper).
            for outcome in watch_outcomes:
                if outcome["status"] == "removed" and not ticker_watched_by_anyone(
                    conn, outcome["ticker"]
                ):
                    await sync_market_source(request, outcome["ticker"], "remove")

            # Step 10: Return structured response (mirrors the stored actions:
            # "orders"/"rules"/"backtests" keys appear only when the turn
            # contained them, keeping the default LLM_MOCK payload
            # byte-identical for E2E).
            response_payload = {
                "message": parsed.message,
                "trades": trade_outcomes,
                "watchlist_changes": watch_outcomes,
            }
            if order_outcomes:
                response_payload["orders"] = order_outcomes
            if rule_outcomes:
                response_payload["rules"] = rule_outcomes
            if backtest_outcomes:
                response_payload["backtests"] = backtest_outcomes
            return response_payload

        except Exception:
            conn.rollback()
            # Best-effort reconcile: successful adds were registered with the
            # market source BEFORE the commit (Step 5). After a rollback the
            # DB may no longer contain those tickers — remove any such ticker
            # from the source so it matches the DB again. Tickers still in
            # ANY user's watchlist (idempotent re-adds of already-watched
            # tickers, or tickers other users watch) keep streaming.
            for added_ticker in source_adds:
                try:
                    if not ticker_watched_by_anyone(conn, added_ticker):
                        await sync_market_source(request, added_ticker, "remove")
                except Exception:
                    logger.exception(
                        "Market source reconcile failed for %s after rollback",
                        added_ticker,
                    )
            raise
        finally:
            conn.close()

    @router.post("/review")
    async def daily_review(request: Request) -> dict:
        """Generate the daily AI review on demand (M2.4). No request body.

        Assembles today's activity (trades, rule firings, portfolio state,
        day P&L per position, market events) and asks the LLM for a concise
        plain-text review. The review is stored as an assistant chat_messages
        row with kind='review' and actions NULL — it appears in GET /api/chat/
        history but never feeds back into the chat LLM's conversation window.

        Returns:
            200 ``{"message": "<text>", "kind": "review"}`` on success.
            500 ``{"error": "LLM unavailable"}`` on any LLM failure — nothing
            is stored in that case.

        When ``LLM_MOCK=true`` a deterministic review with the real trade
        count interpolated is returned — no network call.
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            context, trade_count = _assemble_review_context(conn, price_cache, user_id)

            if os.getenv("LLM_MOCK", "false").lower() == "true":
                # CN-3: Chinese deterministic review on a 'zh-CN' locale; the
                # English mock stays byte-identical otherwise.
                if is_zh:
                    text = (
                        f"[模拟复盘] 你今天进行了 {trade_count} 笔交易。请在下一个"
                        "交易时段开始前检视你的持仓与风险。"
                    )
                else:
                    text = (
                        f"[MOCK REVIEW] You made {trade_count} trades today. "
                        "Review your positions and risk before the next session."
                    )
            else:
                from litellm import completion  # lazy import — never reached when mocked

                messages = [
                    {"role": "system", "content": review_system_prompt},
                    {
                        "role": "user",
                        "content": f"Write my daily review.\n\n{context}",
                    },
                ]
                try:
                    response = await asyncio.to_thread(
                        completion,
                        model=MODEL,
                        messages=messages,
                        reasoning_effort="low",
                        extra_body=EXTRA_BODY,
                    )
                    text = (response.choices[0].message.content or "").strip()
                except Exception:
                    logger.exception("Daily review LLM call failed")
                    return JSONResponse(
                        status_code=500, content={"error": "LLM unavailable"}
                    )
                if not text:
                    logger.error("Daily review LLM returned empty content")
                    return JSONResponse(
                        status_code=500, content={"error": "LLM unavailable"}
                    )

            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, kind, created_at) "
                "VALUES (?, ?, 'assistant', ?, NULL, 'review', ?)",
                (str(uuid.uuid4()), user_id, text, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return {"message": text, "kind": "review"}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return router
