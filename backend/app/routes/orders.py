"""Limit-order API routes and fill engine for FinAlly.

Provides:
- POST   /api/portfolio/orders            — place a limit order (marketable orders fill immediately)
- GET    /api/portfolio/orders            — list orders (status filter, newest first)
- DELETE /api/portfolio/orders/{order_id} — cancel an open order

plus the background fill engine:
- ``process_open_orders_once(db_path, price_cache)`` — one scan-and-fill pass
  over open orders (synchronous, unit-testable)
- ``orders_fill_loop(price_cache, db_path, interval)`` — asyncio background
  task wired in main.py's lifespan, calling the pass every ~1 second

Routes are created via the factory function ``create_orders_router`` which
closes over the shared ``PriceCache`` instance and the database path, mirroring
the other routers.

Marketability semantics (mirrors ``execute_trade_on_conn`` fill pricing):
a buy is marketable when the current ask is at or below the limit price and
fills at the ask; a sell is marketable when the current bid is at or above the
limit price and fills at the bid. On a zero spread (bid == ask), the last price
is used for both, matching the market-order fill path.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.models import PriceUpdate
from app.routes.portfolio import _record_snapshot, execute_trade_on_conn

logger = logging.getLogger(__name__)

ORDER_STATUSES = {"open", "filled", "cancelled", "rejected"}


class LimitOrderRequest(BaseModel):
    ticker: str
    quantity: float
    side: str  # "buy" or "sell"
    limit_price: float


def _order_dict(
    *,
    order_id: str,
    ticker: str,
    side: str,
    quantity: float,
    limit_price: float,
    status: str,
    reject_reason: str | None = None,
    created_at: str,
    filled_at: str | None = None,
    fill_price: float | None = None,
) -> dict:
    """Build the public JSON shape of an order (fill_trade_id stays internal)."""
    return {
        "id": order_id,
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "limit_price": limit_price,
        "status": status,
        "reject_reason": reject_reason,
        "created_at": created_at,
        "filled_at": filled_at,
        "fill_price": fill_price,
    }


def _order_row_to_dict(row: sqlite3.Row) -> dict:
    """Serialize an ``orders`` table row to the public JSON shape."""
    return _order_dict(
        order_id=row["id"],
        ticker=row["ticker"],
        side=row["side"],
        quantity=row["quantity"],
        limit_price=row["limit_price"],
        status=row["status"],
        reject_reason=row["reject_reason"],
        created_at=row["created_at"],
        filled_at=row["filled_at"],
        fill_price=row["fill_price"],
    )


def _marketable_price(quote: PriceUpdate, side: str, limit_price: float) -> float | None:
    """Return the executable fill price when the order is marketable, else None.

    Mirrors ``execute_trade_on_conn``'s fill-price selection: buys fill at the
    ask, sells at the bid, falling back to the last price on a zero spread.
    A buy is marketable when that price is <= limit_price; a sell when it is
    >= limit_price.
    """
    if quote.bid is not None and quote.ask is not None and quote.bid != quote.ask:
        price = quote.ask if side == "buy" else quote.bid
    else:
        price = quote.price
    if side == "buy":
        return price if price <= limit_price else None
    return price if price >= limit_price else None


def _execute_fill(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    limit_price: float,
) -> dict:
    """Execute the trade for a marketable order within the caller's transaction.

    Thin guard around ``execute_trade_on_conn``: if the cache ticked between the
    marketability check and execution and the executed price would violate the
    limit, returns ``{"status": "not_marketable"}`` — the caller must roll back
    and leave the order open. Otherwise returns the trade outcome dict as-is.
    Does NOT commit; the caller owns the transaction boundary.
    """
    outcome = execute_trade_on_conn(conn, price_cache, ticker, side, quantity)
    if outcome["status"] == "executed":
        price = outcome["price"]
        if (side == "buy" and price > limit_price) or (side == "sell" and price < limit_price):
            return {"status": "not_marketable", "ticker": ticker}
    return outcome


def _try_fill_order(
    conn: sqlite3.Connection, price_cache: PriceCache, order: sqlite3.Row
) -> str:
    """Attempt to fill one open order. Returns 'filled', 'rejected', or 'skipped'.

    Transaction semantics: commits on fill/reject, rolls back otherwise. The
    trade, its portfolio snapshot, and the order-status update land in the SAME
    commit. Orders whose ticker has no quote (removed from the cache) are
    skipped and stay open — the ticker may come back.
    """
    ticker: str = order["ticker"]
    side: str = order["side"]
    quantity: float = order["quantity"]
    limit_price: float = order["limit_price"]

    quote = price_cache.get(ticker)
    if quote is None:
        return "skipped"  # No quote (e.g. removed from cache) — leave open.
    if _marketable_price(quote, side, limit_price) is None:
        return "skipped"  # Not marketable yet.

    outcome = _execute_fill(conn, price_cache, ticker, side, quantity, limit_price)

    if outcome["status"] == "executed":
        _record_snapshot(conn, price_cache)
        filled_at = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """
            UPDATE orders
            SET status = 'filled', filled_at = ?, fill_price = ?, fill_trade_id = ?
            WHERE id = ? AND status = 'open'
            """,
            (filled_at, outcome["price"], outcome["trade_id"], order["id"]),
        )
        if cur.rowcount == 0:
            # Order was cancelled between the scan and taking the write lock —
            # undo the trade.
            conn.rollback()
            return "skipped"
        conn.commit()
        logger.info(
            "Limit order %s filled: %s %s %s @ %s",
            order["id"], side, quantity, ticker, outcome["price"],
        )
        return "filled"

    # Undo any partial trade writes / release BEGIN IMMEDIATE.
    conn.rollback()

    if outcome["status"] == "not_marketable":
        return "skipped"  # Price moved off the limit mid-fill — leave open.

    error: str = outcome.get("error", "Trade execution failed")
    if error == "Ticker not found in price cache":
        return "skipped"  # Cache raced away between our check and the fill.

    # Genuine validation failure at fill time (insufficient cash/shares).
    cur = conn.execute(
        "UPDATE orders SET status = 'rejected', reject_reason = ? WHERE id = ? AND status = 'open'",
        (error, order["id"]),
    )
    conn.commit()
    if cur.rowcount == 0:
        return "skipped"  # Cancelled concurrently — nothing to reject.
    logger.info("Limit order %s rejected at fill time: %s", order["id"], error)
    return "rejected"


def process_open_orders_once(db_path: str, price_cache: PriceCache) -> dict[str, int]:
    """One scan-and-fill pass over all open limit orders.

    Opens (and always closes) its own connection. Each order is processed in
    its own transaction — one bad order (or a DB lock error on it) is logged
    and does not stop the pass. Orders are processed oldest-first.

    Returns:
        Counts for observability/tests: {"filled": n, "rejected": n, "skipped": n}.
    """
    counts = {"filled": 0, "rejected": 0, "skipped": 0}
    conn = get_conn(db_path)
    try:
        open_rows = conn.execute(
            """
            SELECT id, ticker, side, quantity, limit_price
            FROM orders
            WHERE user_id = 'default' AND status = 'open'
            ORDER BY created_at ASC, rowid ASC
            """
        ).fetchall()
        for row in open_rows:
            try:
                result = _try_fill_order(conn, price_cache, row)
            except Exception:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                logger.exception(
                    "Fill loop: error processing order %s — continuing", row["id"]
                )
                continue
            counts[result] += 1
    finally:
        conn.close()
    return counts


async def orders_fill_loop(
    price_cache: PriceCache, db_path: str, interval: float = 1.0
) -> None:
    """Background task: fill marketable open limit orders every ``interval`` seconds.

    Runs indefinitely until cancelled via ``asyncio.CancelledError``. Any other
    exception (bad order, DB lock, etc.) is logged and the loop continues.
    """
    while True:
        try:
            process_open_orders_once(db_path, price_cache)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Orders fill loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)


def create_orders_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the limit-orders APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/portfolio", tags=["orders"])

    @router.post("/orders")
    async def place_order(body: LimitOrderRequest) -> dict:
        """Place a limit order.

        Marketable orders (buy: ask <= limit_price; sell: bid >= limit_price)
        execute immediately through the same transactional path as market
        orders — positions/cash/trades row/snapshot and the 'filled' order row
        commit atomically. If the immediate fill fails validation (e.g.
        insufficient cash), HTTP 400 is returned and NOTHING is stored.
        Non-marketable orders are stored as status 'open' for the fill loop.
        """
        ticker = body.ticker.strip().upper()
        side = body.side.lower()

        quote = price_cache.get(ticker)
        if quote is None:
            return JSONResponse(
                status_code=400, content={"error": "Ticker not found in price cache"}
            )
        if side not in {"buy", "sell"}:
            return JSONResponse(
                status_code=400, content={"error": "Side must be 'buy' or 'sell'"}
            )
        if body.quantity <= 0:
            return JSONResponse(
                status_code=400, content={"error": "Quantity must be greater than 0"}
            )
        if body.limit_price <= 0:
            return JSONResponse(
                status_code=400, content={"error": "Limit price must be greater than 0"}
            )

        order_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        marketable = _marketable_price(quote, side, body.limit_price) is not None

        conn = get_conn(db_path)
        try:
            filled_at: str | None = None
            fill_price: float | None = None
            fill_trade_id: str | None = None
            status = "open"

            if marketable:
                outcome = _execute_fill(
                    conn, price_cache, ticker, side, body.quantity, body.limit_price
                )
                if outcome["status"] == "executed":
                    status = "filled"
                    filled_at = datetime.now(timezone.utc).isoformat()
                    fill_price = outcome["price"]
                    fill_trade_id = outcome["trade_id"]
                    _record_snapshot(conn, price_cache)
                elif outcome["status"] == "not_marketable":
                    # Price moved off the limit between check and execution —
                    # undo the trade and rest the order as open instead.
                    conn.rollback()
                else:
                    # Validation failure (e.g. insufficient cash): store NOTHING.
                    conn.rollback()
                    return JSONResponse(
                        status_code=400, content={"error": outcome["error"]}
                    )

            conn.execute(
                """
                INSERT INTO orders (
                    id, user_id, ticker, side, quantity, limit_price,
                    status, reject_reason, created_at, filled_at, fill_price, fill_trade_id
                )
                VALUES (?, 'default', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    order_id, ticker, side, body.quantity, body.limit_price,
                    status, created_at, filled_at, fill_price, fill_trade_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception(
                "Unexpected error placing limit order %s %s %s @ %s",
                side, body.quantity, ticker, body.limit_price,
            )
            raise
        finally:
            conn.close()

        return {
            "order": _order_dict(
                order_id=order_id,
                ticker=ticker,
                side=side,
                quantity=body.quantity,
                limit_price=body.limit_price,
                status=status,
                created_at=created_at,
                filled_at=filled_at,
                fill_price=fill_price,
            )
        }

    @router.get("/orders")
    async def list_orders(status: str | None = None, limit: str | None = None) -> dict:
        """List orders, newest first (created_at DESC, rowid DESC tie-break).

        Query params:
            status: 'open' | 'filled' | 'cancelled' | 'rejected' | 'all'
                (default 'all'). Invalid values return HTTP 400.
            limit: maximum number of orders to return. Defaults to 50 and is
                clamped to the range 1..500. Non-integer values return HTTP 400.
        """
        status_value = (status if status is not None else "all").lower()
        if status_value != "all" and status_value not in ORDER_STATUSES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "status must be one of 'open', 'filled', 'cancelled', 'rejected', 'all'"
                },
            )

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

        query = """
            SELECT id, ticker, side, quantity, limit_price, status,
                   reject_reason, created_at, filled_at, fill_price
            FROM orders
            WHERE user_id = 'default'
        """
        params: list = []
        if status_value != "all":
            query += " AND status = ?"
            params.append(status_value)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit_value)

        conn = get_conn(db_path)
        try:
            rows = conn.execute(query, params).fetchall()
            return {"orders": [_order_row_to_dict(row) for row in rows]}
        finally:
            conn.close()

    @router.delete("/orders/{order_id}")
    async def cancel_order(order_id: str) -> dict:
        """Cancel an open order.

        Returns the cancelled order. Unknown ids return HTTP 404; orders that
        are not open (filled/cancelled/rejected) return HTTP 400. BEGIN
        IMMEDIATE serializes the status check against a concurrent fill.
        """
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, ticker, side, quantity, limit_price, status,
                       reject_reason, created_at, filled_at, fill_price
                FROM orders
                WHERE id = ? AND user_id = 'default'
                """,
                (order_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                return JSONResponse(status_code=404, content={"error": "Order not found"})
            if row["status"] != "open":
                conn.rollback()
                return JSONResponse(status_code=400, content={"error": "Order is not open"})

            conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        result = _order_row_to_dict(row)
        result["status"] = "cancelled"
        return {"order": result}

    return router
