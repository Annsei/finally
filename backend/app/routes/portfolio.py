"""Portfolio API routes for FinAlly.

Provides:
- GET /api/portfolio — current positions, cash, and total portfolio value
- POST /api/portfolio/trade — market order execution (buy/sell)
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

logger = logging.getLogger(__name__)


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

    Returns:
        On success: {"status": "executed", "ticker", "side", "quantity", "price", "trade_id"}
        On failure: {"status": "failed", "ticker", "error"}
    """
    ticker = ticker.upper()
    side = side.lower()

    # Validate price availability
    current_price = price_cache.get_price(ticker)
    if current_price is None:
        return {"status": "failed", "ticker": ticker, "error": "Ticker not found in price cache"}

    # Validate side
    if side not in {"buy", "sell"}:
        return {"status": "failed", "ticker": ticker, "error": "Side must be 'buy' or 'sell'"}

    # Validate quantity
    if quantity <= 0:
        return {"status": "failed", "ticker": ticker, "error": "Quantity must be greater than 0"}

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
    cost = quantity * current_price

    if side == "buy":
        if cash_balance < cost:
            return {"status": "failed", "ticker": ticker, "error": "Insufficient cash"}

        # Deduct cash
        conn.execute(
            "UPDATE users_profile SET cash_balance = cash_balance - ? WHERE id = 'default'",
            (cost,),
        )

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
            (position_id, ticker, quantity, current_price, now),
        )

    else:  # sell
        pos_row = conn.execute(
            "SELECT quantity FROM positions WHERE user_id = 'default' AND ticker = ?",
            (ticker,),
        ).fetchone()
        current_qty: float = pos_row["quantity"] if pos_row else 0.0

        if current_qty < quantity:
            return {"status": "failed", "ticker": ticker, "error": "Insufficient shares to sell"}

        # Add cash proceeds
        conn.execute(
            "UPDATE users_profile SET cash_balance = cash_balance + ? WHERE id = 'default'",
            (cost,),
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

    # Insert trade log entry
    conn.execute(
        "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) VALUES (?, 'default', ?, ?, ?, ?, ?)",
        (trade_id, ticker, side, quantity, current_price, now),
    )

    return {
        "status": "executed",
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price": current_price,
        "trade_id": trade_id,
    }


def create_portfolio_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the portfolio APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

    @router.get("/")
    async def get_portfolio(request: Request) -> dict:
        """Return current cash balance, all positions, and total portfolio value."""
        conn = get_conn(db_path)
        try:
            user_row = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = 'default'"
            ).fetchone()
            cash_balance: float = user_row["cash_balance"] if user_row else 0.0

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
        ``{"error": "message"}``; nothing is committed in that case.
        On success returns trade confirmation with status="ok" and trade_id.
        """
        conn = get_conn(db_path)
        try:
            outcome = execute_trade_on_conn(
                conn, price_cache, body.ticker, body.side, body.quantity
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
            "trade_id": outcome["trade_id"],
        }

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
