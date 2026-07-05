"""Chat API routes for FinAlly.

Provides:
- POST /api/chat — LLM-powered chat with structured output; auto-executes trades
  and watchlist changes; persists conversation history to chat_messages.

All routes are created via the factory function ``create_chat_router`` which
closes over the shared ``PriceCache`` instance and the database path.

When ``LLM_MOCK=true`` the endpoint returns a deterministic response that
exercises the full auto-execution pipeline without network calls (D-06/D-07).
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

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.routes.portfolio import _record_snapshot, execute_trade_on_conn
from app.routes.watchlist import apply_watchlist_change_on_conn, sync_market_source

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}


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


class ChatResponse(BaseModel):
    message: str
    trades: list[TradeInstruction] = []
    watchlist_changes: list[WatchlistChange] = []


# ---------------------------------------------------------------------------
# Context assembly helper
# ---------------------------------------------------------------------------


def _assemble_portfolio_context(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
) -> str:
    """Build a compact portfolio context string for injection into the system prompt.

    Reads current cash, positions, and watchlist from the open connection and
    enriches both positions and watchlist tickers with live prices from the
    price cache (spec §9: "watchlist with live prices").

    Args:
        conn: An open SQLite connection (caller manages lifecycle).
        price_cache: Live price cache for current market prices.

    Returns:
        Multi-line string with cash, total value, positions table, and
        watchlist with current prices.
    """
    # Cash balance
    user_row = conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = 'default'"
    ).fetchone()
    cash: float = user_row["cash_balance"] if user_row else 0.0

    # Positions with P&L
    position_rows = conn.execute(
        "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = 'default'"
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
        "SELECT ticker FROM watchlist WHERE user_id = 'default' ORDER BY added_at ASC"
    ).fetchall()
    watchlist_parts: list[str] = []
    for r in watchlist_rows:
        wl_ticker: str = r["ticker"]
        wl_price = price_cache.get_price(wl_ticker)
        watchlist_parts.append(
            f"{wl_ticker} ${wl_price:.2f}" if wl_price is not None else f"{wl_ticker} (no price)"
        )
    watchlist_tickers = ", ".join(watchlist_parts)

    return (
        f"Cash: ${cash:.2f}\n"
        f"Total portfolio value: ${total:.2f}\n"
        f"Positions (ticker | qty | avg_cost | current_price | pnl | pnl%):\n"
        f"{positions_block}\n"
        f"Watchlist: {watchlist_tickers}"
    )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_chat_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the chat APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    @router.get("/")
    async def get_chat_history(request: Request) -> dict:
        """Return last 20 chat messages in ascending chronological order.

        Query selects DESC then reverses so the response is ascending by created_at.
        The ``actions`` field is parsed from its stored JSON string to a dict (or None).

        Returns:
            {"messages": [{"role", "content", "actions", "created_at"}, ...]}
        """
        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                """
                SELECT role, content, actions, created_at
                FROM chat_messages
                WHERE user_id = 'default'
                ORDER BY created_at DESC
                LIMIT 20
                """
            ).fetchall()
            messages = list(reversed([
                {
                    "role": row["role"],
                    "content": row["content"],
                    "actions": json.loads(row["actions"]) if row["actions"] else None,
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

        Returns structured JSON with the assistant message, trade outcomes, and
        watchlist change outcomes. Per-action validation failures are returned as
        outcome dicts (status=failed) — they do NOT raise HTTP errors and do not
        abort the remaining actions.

        Ordering: watchlist changes are applied BEFORE trades, and each
        successful "add" is registered with the live market source
        immediately (add_ticker seeds the price cache) so a single turn like
        "add PYPL and buy 5 shares" finds a price at trade time. Market
        source removals stay AFTER the commit so an in-flight turn cannot
        lose prices and a rollback cannot orphan the source state.

        Transaction boundary: all executed trades, applied watchlist changes,
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
        conn = get_conn(db_path)
        # Tickers registered with the market source before the commit this
        # turn — used to reconcile the source if the transaction rolls back.
        source_adds: list[str] = []
        try:
            # Step 1: Load conversation history (D-04)
            rows = conn.execute(
                "SELECT role, content FROM chat_messages "
                "WHERE user_id = 'default' ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            history = list(reversed(rows))

            # Step 2: Assemble portfolio context (D-01/D-02)
            context = _assemble_portfolio_context(conn, price_cache)

            # Step 3: Build messages list for LLM
            messages: list[dict] = [
                {
                    "role": "system",
                    "content": (
                        "You are FinAlly, an AI trading assistant. Be concise and data-driven. "
                        "Execute trades when asked. Always respond with valid structured JSON.\n\n"
                        f"Current portfolio:\n{context}"
                    ),
                }
            ]
            messages.extend(
                {"role": r["role"], "content": r["content"]} for r in history
            )
            messages.append({"role": "user", "content": body.message})

            # Step 4: Get LLM response — mock path (D-06/D-07) or real LiteLLM call
            if os.getenv("LLM_MOCK", "false").lower() == "true":
                # Construct deterministic response; fall through to auto-exec (D-07)
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
                outcome = apply_watchlist_change_on_conn(conn, w.ticker, w.action)
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
                )
                for t in parsed.trades
            ]

            # Step 7: Persist both messages (T-02-12 — parameterized SQL)
            # Separate timestamps guarantee deterministic ORDER BY created_at ordering
            # even when both rows are written in the same request (WR-02).
            actions = {"trades": trade_outcomes, "watchlist_changes": watch_outcomes}
            user_ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
                "VALUES (?, 'default', 'user', ?, NULL, ?)",
                (str(uuid.uuid4()), body.message, user_ts),
            )
            asst_ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
                "VALUES (?, 'default', 'assistant', ?, ?, ?)",
                (str(uuid.uuid4()), parsed.message, json.dumps(actions), asst_ts),
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
                    _record_snapshot(conn, price_cache)
                    conn.commit()
                except Exception:
                    logger.exception("Post-trade snapshot failed (chat turn already committed)")

            # Step 9: Sync watchlist REMOVALS to the live market data source
            # so removed tickers stop simulating/streaming. Adds were already
            # synced in Step 5 (pre-trade). Runs after the commit — DB is the
            # source of truth and the sync is best-effort (failures logged
            # inside the helper).
            for outcome in watch_outcomes:
                if outcome["status"] == "removed":
                    await sync_market_source(request, outcome["ticker"], "remove")

            # Step 10: Return structured response
            return {
                "message": parsed.message,
                "trades": trade_outcomes,
                "watchlist_changes": watch_outcomes,
            }

        except Exception:
            conn.rollback()
            # Best-effort reconcile: successful adds were registered with the
            # market source BEFORE the commit (Step 5). After a rollback the
            # DB may no longer contain those tickers — remove any such ticker
            # from the source so it matches the DB again. Tickers still in the
            # watchlist (idempotent re-adds of already-watched tickers) keep
            # streaming.
            for added_ticker in source_adds:
                try:
                    row = conn.execute(
                        "SELECT 1 FROM watchlist WHERE user_id = 'default' AND ticker = ?",
                        (added_ticker,),
                    ).fetchone()
                    if row is None:
                        await sync_market_source(request, added_ticker, "remove")
                except Exception:
                    logger.exception(
                        "Market source reconcile failed for %s after rollback",
                        added_ticker,
                    )
            raise
        finally:
            conn.close()

    return router
