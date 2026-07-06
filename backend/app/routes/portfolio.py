"""Portfolio API routes for FinAlly.

Provides:
- GET /api/portfolio — current positions, cash, and total portfolio value
- POST /api/portfolio/trade — market order execution (buy/sell)
- GET /api/portfolio/trades — trade blotter (executed trades, newest first)
- GET /api/portfolio/history — portfolio value snapshots over time

All routes are created via the factory function ``create_portfolio_router`` which
closes over the shared ``PriceCache`` instance and the database path.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.seed_prices import asset_class_for
from app.market.session import SessionClock

logger = logging.getLogger(__name__)

# Error message for market orders on equities while the session is closed
# (M3.1). Contract fixed — the frontend and chat tests match on this string.
MARKET_CLOSED_ERROR = "Market closed"


class TradeRequest(BaseModel):
    ticker: str
    quantity: float
    side: str  # "buy" or "sell"


def _record_snapshot(conn: sqlite3.Connection, price_cache: PriceCache) -> None:
    """Compute current total portfolio value and insert a snapshot row.

    Does NOT commit — the caller owns the transaction boundary and must
    commit (or roll back) the inserted row.

    Args:
        conn: An open SQLite connection (caller manages lifecycle and commit).
        price_cache: Live price cache for current market prices.
    """
    row = conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = 'default'"
    ).fetchone()
    cash_balance: float = row["cash_balance"] if row else 0.0

    positions = conn.execute(
        "SELECT ticker, quantity FROM positions WHERE user_id = 'default'"
    ).fetchall()

    total_value = cash_balance + sum(
        qty * (price_cache.get_price(ticker) or 0.0)
        for ticker, qty in ((p["ticker"], p["quantity"]) for p in positions)
    )

    conn.execute(
        "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) VALUES (?, 'default', ?, ?)",
        (str(uuid.uuid4()), total_value, datetime.now(timezone.utc).isoformat()),
    )


def execute_trade_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    commission_bps: float = 0.0,
    session_clock: SessionClock | None = None,
) -> dict:
    """Execute a market order on an open SQLite connection.

    Validates and executes a buy or sell trade against the provided connection.
    All validation failures return a dict with status="failed" and an "error" key
    — this function never raises on validation errors.

    Transaction semantics: this function does NOT commit. The caller owns the
    transaction boundary and must commit on success (or roll back on error).
    This allows callers to execute several trades plus related writes
    (snapshots, chat messages) atomically in a single transaction. If no
    transaction is already open, a ``BEGIN IMMEDIATE`` is issued before the
    cash/shares check so the check and the subsequent balance update are
    serialized against concurrent writers (prevents a TOCTOU race on the cash
    balance). When the caller already opened a transaction (e.g. the
    multi-trade chat flow), the existing transaction provides that guarantee.

    Args:
        conn: An open SQLite connection (caller manages lifecycle, commit, rollback).
        price_cache: Live price cache for current market prices.
        ticker: Ticker symbol (normalized to uppercase internally).
        side: "buy" or "sell" (normalized to lowercase internally).
        quantity: Number of shares to trade (must be > 0).
        commission_bps: Commission in basis points of notional (0 = free).
            Read once from FINALLY_COMMISSION_BPS at app startup and passed
            down; never read from the environment here.
        session_clock: Session clock (M3.1). When provided and the market is
            CLOSED, market orders on EQUITY tickers are rejected with
            "Market closed" (equity quotes are frozen while closed — a fill
            would execute at a stale price). Crypto trades 24/7. With no
            clock, or a 24/7 clock, nothing is ever rejected. Deliberately
            NOT threaded through the order fill loop or the rules engine —
            resting-order semantics are unchanged (frozen quotes mean nothing
            crosses while closed).

    Fill price: buys fill at the cached ask, sells at the cached bid, falling
    back to the last price when bid/ask are absent or equal (zero spread).
    The "price" in the returned dict and the trades table is that fill price.

    Commission (M1): commission = round(notional * bps / 10000, 2). Buys pay
    notional + commission from cash and fold the commission into the position
    cost basis; sells receive notional - commission and realize
    round((fill_price - avg_cost_at_sale) * quantity - commission, 2) as
    realized_pnl on the trade row (NULL for buys). With commission_bps == 0
    the cash/position math is bit-identical to the pre-commission behavior.

    Returns:
        On success: {"status": "executed", "ticker", "side", "quantity",
                     "price", "commission", "realized_pnl", "trade_id"}
        On failure: {"status": "failed", "ticker", "error"}
    """
    ticker = ticker.upper()
    side = side.lower()

    # Validate price availability
    quote = price_cache.get(ticker)
    if quote is None:
        return {"status": "failed", "ticker": ticker, "error": "Ticker not found in price cache"}

    # Validate side
    if side not in {"buy", "sell"}:
        return {"status": "failed", "ticker": ticker, "error": "Side must be 'buy' or 'sell'"}

    # Validate quantity
    if quantity <= 0:
        return {"status": "failed", "ticker": ticker, "error": "Quantity must be greater than 0"}

    # M3.1: reject equity market orders while the session is closed — the
    # quote is frozen at the close, so a fill would execute at a stale price.
    # Crypto trades 24/7; in 24/7 mode (no sessions) is_open is always True.
    if (
        session_clock is not None
        and not session_clock.is_open
        and asset_class_for(ticker) == "equity"
    ):
        return {"status": "failed", "ticker": ticker, "error": MARKET_CLOSED_ERROR}

    # Fill at the quote: buy at ask, sell at bid. When the source supplies no
    # quote, bid == ask == price (model default) and the fill is at the price.
    if quote.bid is not None and quote.ask is not None and quote.bid != quote.ask:
        current_price = quote.ask if side == "buy" else quote.bid
    else:
        current_price = quote.price

    # Serialize the cash/shares check with the subsequent debit/credit by taking
    # SQLite's write lock up front (TOCTOU protection). Skipped when the caller
    # already opened a transaction — its writes are already serialized.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")

    user_row = conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = 'default'"
    ).fetchone()
    cash_balance: float = user_row["cash_balance"] if user_row else 0.0

    trade_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    cost = quantity * current_price  # notional at the fill price
    # Commission on the notional, rounded to cents. When commission_bps is 0
    # (the default) every formula below reduces exactly to the pre-commission
    # math (x + 0.0 == x in IEEE 754), keeping legacy behavior bit-identical.
    commission = round(cost * commission_bps / 10000.0, 2) if commission_bps else 0.0
    realized_pnl: float | None = None

    if side == "buy":
        if cash_balance < cost + commission:
            return {"status": "failed", "ticker": ticker, "error": "Insufficient cash"}

        # Deduct cash: notional plus commission
        conn.execute(
            "UPDATE users_profile SET cash_balance = cash_balance - ? WHERE id = 'default'",
            (cost + commission,),
        )

        # Commission folds into the cost basis: the per-share cost of THIS lot
        # is (notional + commission) / quantity, weighted into avg_cost by the
        # upsert below. With zero commission use current_price verbatim so the
        # stored avg_cost is bit-identical to the pre-commission behavior.
        effective_cost = (cost + commission) / quantity if commission else current_price

        # Upsert position — weighted average cost on conflict
        position_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)
            VALUES (?, 'default', ?, ?, ?, ?)
            ON CONFLICT(user_id, ticker) DO UPDATE SET
                avg_cost = (avg_cost * quantity + excluded.avg_cost * excluded.quantity)
                           / (quantity + excluded.quantity),
                quantity = quantity + excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (position_id, ticker, quantity, effective_cost, now),
        )

    else:  # sell
        pos_row = conn.execute(
            "SELECT quantity, avg_cost FROM positions WHERE user_id = 'default' AND ticker = ?",
            (ticker,),
        ).fetchone()
        current_qty: float = pos_row["quantity"] if pos_row else 0.0
        avg_cost_at_sale: float = pos_row["avg_cost"] if pos_row else 0.0

        if current_qty < quantity:
            return {"status": "failed", "ticker": ticker, "error": "Insufficient shares to sell"}

        # Add cash proceeds: notional minus commission
        conn.execute(
            "UPDATE users_profile SET cash_balance = cash_balance + ? WHERE id = 'default'",
            (cost - commission,),
        )

        realized_pnl = round(
            (current_price - avg_cost_at_sale) * quantity - commission, 2
        )

        new_qty = current_qty - quantity
        # Float subtraction can leave a ~1e-16 residue when selling the full
        # position (e.g. 0.1+0.1+0.1 bought, 0.3 sold). Treat anything below
        # epsilon as fully closed so no ghost position row lingers.
        if new_qty <= 1e-9:
            conn.execute(
                "DELETE FROM positions WHERE user_id = 'default' AND ticker = ?",
                (ticker,),
            )
        else:
            conn.execute(
                "UPDATE positions SET quantity = ?, updated_at = ? WHERE user_id = 'default' AND ticker = ?",
                (new_qty, now, ticker),
            )

    # Insert trade log entry (commission always stored; realized_pnl NULL on buys)
    conn.execute(
        "INSERT INTO trades (id, user_id, ticker, side, quantity, price, commission, realized_pnl, executed_at)"
        " VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?)",
        (trade_id, ticker, side, quantity, current_price, commission, realized_pnl, now),
    )

    return {
        "status": "executed",
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price": current_price,
        "commission": commission,
        "realized_pnl": realized_pnl,
        "trade_id": trade_id,
    }


def create_portfolio_router(
    price_cache: PriceCache,
    db_path: str,
    commission_bps: float = 0.0,
    session_clock: SessionClock | None = None,
) -> APIRouter:
    """Factory: build the portfolio APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.
        commission_bps: Commission in basis points of notional applied to every
            fill (FINALLY_COMMISSION_BPS, read once at app startup in main.py).
        session_clock: Session clock (M3.1) — equity market orders are
            rejected with HTTP 400 "Market closed" while the session is
            closed. None (or a 24/7 clock) never rejects.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

    @router.get("/")
    async def get_portfolio(request: Request) -> dict:
        """Return cash balance, positions, total value, and lifetime realized P&L."""
        conn = get_conn(db_path)
        try:
            user_row = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = 'default'"
            ).fetchone()
            cash_balance: float = user_row["cash_balance"] if user_row else 0.0

            realized_row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0.0) AS total FROM trades "
                "WHERE user_id = 'default'"
            ).fetchone()
            realized_pnl: float = round(realized_row["total"] or 0.0, 2)

            position_rows = conn.execute(
                "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = 'default'"
            ).fetchall()

            positions = []
            position_market_value = 0.0
            for row in position_rows:
                ticker: str = row["ticker"]
                quantity: float = row["quantity"]
                avg_cost: float = row["avg_cost"]
                current_price: float = price_cache.get_price(ticker) or 0.0

                unrealized_pnl = (current_price - avg_cost) * quantity
                pnl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0

                position_market_value += quantity * current_price
                positions.append(
                    {
                        "ticker": ticker,
                        "quantity": quantity,
                        "avg_cost": avg_cost,
                        "current_price": current_price,
                        "unrealized_pnl": unrealized_pnl,
                        "pnl_pct": pnl_pct,
                    }
                )

            total_value = cash_balance + position_market_value
            return {
                "cash": cash_balance,
                "total_value": total_value,
                "realized_pnl": realized_pnl,
                "positions": positions,
            }
        finally:
            conn.close()

    @router.post("/trade")
    async def execute_trade(body: TradeRequest, request: Request) -> dict:
        """Execute a market order (buy or sell).

        Thin wrapper over ``execute_trade_on_conn``. On success the trade and
        its post-trade portfolio snapshot are committed together in a single
        transaction. Validation errors return HTTP 400 with
        ``{"error": "message"}``; nothing is committed in that case. Equity
        market orders while the session is closed (M3.1) return HTTP 400
        ``{"error": "Market closed"}``.
        On success returns trade confirmation with status="ok" and trade_id.
        """
        conn = get_conn(db_path)
        try:
            outcome = execute_trade_on_conn(
                conn, price_cache, body.ticker, body.side, body.quantity,
                commission_bps=commission_bps, session_clock=session_clock,
            )
            if outcome["status"] == "executed":
                # Record portfolio snapshot immediately after the trade and
                # commit both atomically (spec §7).
                _record_snapshot(conn, price_cache)
                conn.commit()
            else:
                # Validation failures write nothing; rollback releases any
                # write lock taken by BEGIN IMMEDIATE before the failure.
                conn.rollback()
        except Exception:
            conn.rollback()
            logger.exception(
                "Unexpected error executing trade %s %s %s",
                body.side, body.quantity, body.ticker,
            )
            raise
        finally:
            conn.close()

        if outcome["status"] == "failed":
            return JSONResponse(status_code=400, content={"error": outcome["error"]})

        return {
            "status": "ok",
            "ticker": outcome["ticker"],
            "side": outcome["side"],
            "quantity": outcome["quantity"],
            "price": outcome["price"],
            "commission": outcome["commission"],
            "realized_pnl": outcome["realized_pnl"],
            "trade_id": outcome["trade_id"],
        }

    @router.get("/trades")
    async def get_trades(request: Request, limit: str | None = None) -> dict:
        """Return the trade blotter: executed trades, newest first.

        Query params:
            limit: maximum number of trades to return. Defaults to 50 and is
                clamped to the range 1..500. Non-integer values return
                HTTP 400 with ``{"error": "message"}``.

        Ordering is executed_at DESC with rowid DESC as a tie-break so trades
        executed within the same timestamp still return in stable
        newest-first insertion order.

        Note: the path is distinct from POST /trade (the execution endpoint);
        FastAPI matches literal paths exactly, so there is no route conflict.
        """
        if limit is None:
            limit_value = 50
        else:
            try:
                limit_value = int(limit)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": "limit must be an integer"}
                )
        limit_value = max(1, min(500, limit_value))

        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                """
                SELECT id, ticker, side, quantity, price, commission,
                       realized_pnl, executed_at
                FROM trades
                WHERE user_id = 'default'
                ORDER BY executed_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit_value,),
            ).fetchall()
            return {
                "trades": [
                    {
                        "id": row["id"],
                        "ticker": row["ticker"],
                        "side": row["side"],
                        "quantity": row["quantity"],
                        "price": row["price"],
                        "commission": row["commission"],
                        "realized_pnl": row["realized_pnl"],
                        "executed_at": row["executed_at"],
                    }
                    for row in rows
                ]
            }
        finally:
            conn.close()

    @router.get("/history")
    async def get_portfolio_history(request: Request) -> dict:
        """Return portfolio value snapshots in ascending chronological order."""
        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                """
                SELECT total_value, recorded_at
                FROM portfolio_snapshots
                WHERE user_id = 'default'
                ORDER BY recorded_at ASC
                LIMIT 500
                """
            ).fetchall()
            return {
                "snapshots": [
                    {"total_value": row["total_value"], "recorded_at": row["recorded_at"]}
                    for row in rows
                ]
            }
        finally:
            conn.close()

    return router
