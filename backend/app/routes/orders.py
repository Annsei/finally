"""Order API routes and fill engine for FinAlly (limit / stop / stop-limit).

Provides:
- POST   /api/portfolio/orders            — place an order (marketable limit
  orders fill immediately; stop/stop_limit orders always rest until triggered)
- GET    /api/portfolio/orders            — list orders (status filter, newest first)
- DELETE /api/portfolio/orders/{order_id} — cancel an open order

plus the background fill engine:
- ``process_open_orders_once(db_path, price_cache, commission_bps)`` — one
  scan pass over open orders (synchronous, unit-testable): expires day orders,
  triggers stops, and fills marketable orders
- ``orders_fill_loop(price_cache, db_path, interval, commission_bps)`` —
  asyncio background task wired in main.py's lifespan, calling the pass every
  ~1 second

and the shared placement helper:
- ``place_order_on_conn(conn, price_cache, *, ...)`` — validates and places
  one order on an open connection without committing; returns the full order
  JSON (status 'open'/'filled') or a ``{"status": "failed", ...}`` dict and
  never raises on validation failure. Used by both the POST route (which maps
  failures to HTTP 400) and the chat auto-execution pipeline (M2.1, which
  treats failures as non-fatal outcomes inside its single-commit turn).

Routes are created via the factory function ``create_orders_router`` which
closes over the shared ``PriceCache`` instance, the database path, and the
commission rate, mirroring the other routers.

Order kinds (M1, PLATFORM_ROADMAP §M1):
- ``limit``: limit_price required. A buy is marketable when the current ask is
  at or below the limit price and fills at the ask; a sell is marketable when
  the current bid is at or above the limit price and fills at the bid. On a
  zero spread (bid == ask), the last price is used for both, matching the
  market-order fill path. Marketable limit orders fill at placement time.
- ``stop``: stop_price required, market-on-trigger. A BUY stop triggers when
  the ask rises to/above stop_price (breakout entry); a SELL stop triggers
  when the bid falls to/below stop_price (stop-loss). On trigger the fill
  loop executes a market fill at the ask/bid.
- ``stop_limit``: both prices required. On trigger, ``triggered_at`` is
  stamped and the order becomes a resting limit order (normal limit
  semantics thereafter).

Wrong-side stops are rejected at placement: a SELL stop must be below the
current bid, a BUY stop above the current ask (otherwise it would trigger
instantly and it is really a market order).

Time-in-force: ``gtc`` (default) never expires; ``day`` sets expires_at to
created_at + 24h. The fill loop marks open orders past expires_at as status
``expired``. With the session clock active (M3.1), open EQUITY day orders are
additionally expired at session close by the settlement hook
(``app.settlement.settle_session_close``), which supersedes the 24h TTL for
them; crypto day orders keep the 24h behavior (crypto trades 24/7).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.models import PriceUpdate
from app.routes.portfolio import _record_snapshot, execute_trade_on_conn

logger = logging.getLogger(__name__)

ORDER_STATUSES = {"open", "filled", "cancelled", "rejected", "expired"}
ORDER_KINDS = {"limit", "stop", "stop_limit"}
TIME_IN_FORCE_VALUES = {"day", "gtc"}

# 'day' orders live at most 24 hours from placement. In session mode (M3.1)
# equity day orders are expired earlier — at session close — by the settlement
# hook; this TTL remains the backstop for crypto and for 24/7 mode.
DAY_ORDER_TTL = timedelta(hours=24)


class PlaceOrderRequest(BaseModel):
    ticker: str
    quantity: float
    side: str  # "buy" or "sell"
    kind: str = "limit"  # "limit" | "stop" | "stop_limit"
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "gtc"  # "day" | "gtc"


def _order_dict(
    *,
    order_id: str,
    ticker: str,
    side: str,
    quantity: float,
    kind: str,
    limit_price: float | None,
    stop_price: float | None,
    time_in_force: str,
    expires_at: str | None,
    triggered_at: str | None = None,
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
        "kind": kind,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "time_in_force": time_in_force,
        "expires_at": expires_at,
        "triggered_at": triggered_at,
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
        kind=row["kind"],
        limit_price=row["limit_price"],
        stop_price=row["stop_price"],
        time_in_force=row["time_in_force"],
        expires_at=row["expires_at"],
        triggered_at=row["triggered_at"],
        status=row["status"],
        reject_reason=row["reject_reason"],
        created_at=row["created_at"],
        filled_at=row["filled_at"],
        fill_price=row["fill_price"],
    )


_ORDER_SELECT_COLUMNS = (
    "id, ticker, side, quantity, kind, limit_price, stop_price, "
    "time_in_force, expires_at, triggered_at, status, reject_reason, "
    "created_at, filled_at, fill_price"
)


def _quote_bid_ask(quote: PriceUpdate) -> tuple[float, float]:
    """Return (bid, ask), falling back to the last price on a zero/absent spread.

    Mirrors ``execute_trade_on_conn``'s fill-price selection so trigger and
    marketability checks gate on exactly the price a fill would execute at.
    """
    if quote.bid is not None and quote.ask is not None and quote.bid != quote.ask:
        return quote.bid, quote.ask
    return quote.price, quote.price


def _marketable_price(quote: PriceUpdate, side: str, limit_price: float) -> float | None:
    """Return the executable fill price when a limit order is marketable, else None.

    Buys fill at the ask, sells at the bid (zero-spread fallback to the last
    price). A buy is marketable when that price is <= limit_price; a sell when
    it is >= limit_price.
    """
    bid, ask = _quote_bid_ask(quote)
    price = ask if side == "buy" else bid
    if side == "buy":
        return price if price <= limit_price else None
    return price if price >= limit_price else None


def _stop_triggered(quote: PriceUpdate, side: str, stop_price: float) -> bool:
    """True when a stop condition has fired.

    BUY stop: triggers when the ask rises to/above stop_price (breakout entry).
    SELL stop: triggers when the bid falls to/below stop_price (stop-loss).
    """
    bid, ask = _quote_bid_ask(quote)
    if side == "buy":
        return ask >= stop_price
    return bid <= stop_price


def _is_expired(expires_at: str, now: datetime) -> bool:
    """True when the ISO timestamp ``expires_at`` is at/before ``now``.

    Unparseable timestamps are logged and treated as non-expiring (the order
    stays open rather than being destroyed by bad data).
    """
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        logger.warning("Order has unparseable expires_at %r — treating as GTC", expires_at)
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return now >= expiry


def _execute_fill(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    limit_price: float | None,
    commission_bps: float,
) -> dict:
    """Execute the trade for a triggered/marketable order within the caller's transaction.

    Thin guard around ``execute_trade_on_conn``: for limit-priced orders
    (limit / triggered stop_limit), if the cache ticked between the
    marketability check and execution and the executed price would violate the
    limit, returns ``{"status": "not_marketable"}`` — the caller must roll back
    and leave the order open. Plain stop orders (limit_price is None) fill at
    whatever the market gives (market-on-trigger). Does NOT commit; the caller
    owns the transaction boundary.
    """
    outcome = execute_trade_on_conn(
        conn, price_cache, ticker, side, quantity, commission_bps=commission_bps
    )
    if outcome["status"] == "executed" and limit_price is not None:
        price = outcome["price"]
        if (side == "buy" and price > limit_price) or (side == "sell" and price < limit_price):
            return {"status": "not_marketable", "ticker": ticker}
    return outcome


def _apply_fill_outcome(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    order: sqlite3.Row,
    outcome: dict,
) -> str:
    """Apply a trade outcome to the order row. Returns 'filled', 'rejected', or 'skipped'.

    Transaction semantics: commits on fill/reject, rolls back otherwise. The
    trade, its portfolio snapshot, and the order-status update land in the SAME
    commit. For stop/stop_limit orders, triggered_at is stamped alongside the
    terminal status (COALESCE keeps an earlier stamp); it stays NULL for limit
    orders.
    """
    if outcome["status"] == "executed":
        _record_snapshot(conn, price_cache)
        filled_at = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """
            UPDATE orders
            SET status = 'filled', filled_at = ?, fill_price = ?, fill_trade_id = ?,
                triggered_at = CASE WHEN kind = 'limit' THEN triggered_at
                                    ELSE COALESCE(triggered_at, ?) END
            WHERE id = ? AND status = 'open'
            """,
            (filled_at, outcome["price"], outcome["trade_id"], filled_at, order["id"]),
        )
        if cur.rowcount == 0:
            # Order was cancelled between the scan and taking the write lock —
            # undo the trade.
            conn.rollback()
            return "skipped"
        conn.commit()
        logger.info(
            "Order %s (%s) filled: %s %s %s @ %s",
            order["id"], order["kind"], order["side"], order["quantity"],
            order["ticker"], outcome["price"],
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
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        UPDATE orders
        SET status = 'rejected', reject_reason = ?,
            triggered_at = CASE WHEN kind = 'limit' THEN triggered_at
                                ELSE COALESCE(triggered_at, ?) END
        WHERE id = ? AND status = 'open'
        """,
        (error, now, order["id"]),
    )
    conn.commit()
    if cur.rowcount == 0:
        return "skipped"  # Cancelled concurrently — nothing to reject.
    logger.info("Order %s rejected at fill time: %s", order["id"], error)
    return "rejected"


def _try_fill_order(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    order: sqlite3.Row,
    commission_bps: float,
) -> str:
    """Process one open order. Returns 'filled', 'rejected', 'skipped', or 'expired'.

    Pipeline per pass: expire (TIF) -> trigger (stop/stop_limit) -> fill.
    A stop_limit whose trigger fires is stamped triggered_at in its own commit
    and then evaluated with normal limit semantics in the same pass. Orders
    whose ticker has no quote (removed from the cache) are skipped and stay
    open — the ticker may come back — but expiry applies even without a quote.
    """
    ticker: str = order["ticker"]
    side: str = order["side"]
    quantity: float = order["quantity"]
    kind: str = order["kind"]
    limit_price: float | None = order["limit_price"]
    stop_price: float | None = order["stop_price"]
    triggered_at: str | None = order["triggered_at"]

    # Time-in-force: day orders past their expiry become 'expired' (terminal).
    expires_at: str | None = order["expires_at"]
    if expires_at is not None and _is_expired(expires_at, datetime.now(timezone.utc)):
        cur = conn.execute(
            "UPDATE orders SET status = 'expired' WHERE id = ? AND status = 'open'",
            (order["id"],),
        )
        conn.commit()
        if cur.rowcount == 0:
            return "skipped"  # Cancelled/filled concurrently.
        logger.info("Order %s expired (time_in_force=%s)", order["id"], order["time_in_force"])
        return "expired"

    quote = price_cache.get(ticker)
    if quote is None:
        return "skipped"  # No quote (e.g. removed from cache) — leave open.

    if kind == "stop":
        if stop_price is None or not _stop_triggered(quote, side, stop_price):
            return "skipped"  # Untriggered — stays open.
        # Market-on-trigger: fill at the ask/bid with no limit guard.
        outcome = _execute_fill(
            conn, price_cache, ticker, side, quantity, None, commission_bps
        )
        return _apply_fill_outcome(conn, price_cache, order, outcome)

    if kind == "stop_limit" and triggered_at is None:
        if stop_price is None or not _stop_triggered(quote, side, stop_price):
            return "skipped"  # Untriggered — stays open.
        # Trigger fired: stamp triggered_at in its own commit, then the order
        # is a plain resting limit order from here on (including this pass).
        cur = conn.execute(
            "UPDATE orders SET triggered_at = ? "
            "WHERE id = ? AND status = 'open' AND triggered_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), order["id"]),
        )
        conn.commit()
        if cur.rowcount == 0:
            return "skipped"  # Cancelled (or already stamped) concurrently.
        logger.info("Stop-limit order %s triggered at stop %s", order["id"], stop_price)

    # Limit semantics: kind == 'limit', or a stop_limit that has triggered.
    if limit_price is None or _marketable_price(quote, side, limit_price) is None:
        return "skipped"  # Not marketable yet.

    outcome = _execute_fill(
        conn, price_cache, ticker, side, quantity, limit_price, commission_bps
    )
    return _apply_fill_outcome(conn, price_cache, order, outcome)


def process_open_orders_once(
    db_path: str, price_cache: PriceCache, commission_bps: float = 0.0
) -> dict[str, int]:
    """One scan pass over all open orders: expire, trigger, and fill.

    Opens (and always closes) its own connection. Each order is processed in
    its own transaction — one bad order (or a DB lock error on it) is logged
    and does not stop the pass. Orders are processed oldest-first.

    Returns:
        Counts for observability/tests:
        {"filled": n, "rejected": n, "skipped": n, "expired": n}.
        A stop_limit that triggers but does not fill counts as "skipped"
        (it remains open).
    """
    counts = {"filled": 0, "rejected": 0, "skipped": 0, "expired": 0}
    conn = get_conn(db_path)
    try:
        open_rows = conn.execute(
            f"""
            SELECT {_ORDER_SELECT_COLUMNS}
            FROM orders
            WHERE user_id = 'default' AND status = 'open'
            ORDER BY created_at ASC, rowid ASC
            """
        ).fetchall()
        for row in open_rows:
            try:
                result = _try_fill_order(conn, price_cache, row, commission_bps)
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
    price_cache: PriceCache,
    db_path: str,
    interval: float = 1.0,
    commission_bps: float = 0.0,
) -> None:
    """Background task: process open orders every ``interval`` seconds.

    Runs indefinitely until cancelled via ``asyncio.CancelledError``. Any other
    exception (bad order, DB lock, etc.) is logged and the loop continues.
    """
    while True:
        try:
            process_open_orders_once(db_path, price_cache, commission_bps)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Orders fill loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)


def _validate_order_request(
    *,
    side: str,
    kind: str,
    time_in_force: str,
    quantity: float,
    limit_price: float | None,
    stop_price: float | None,
    quote: PriceUpdate | None,
) -> str | None:
    """Validate a placement request. Returns an error message or None if valid.

    Covers the kind/tif enums, the required-fields matrix per kind, price
    positivity, and the wrong-side stop checks against the live quote.
    """
    if quote is None:
        return "Ticker not found in price cache"
    if side not in {"buy", "sell"}:
        return "Side must be 'buy' or 'sell'"
    if quantity <= 0:
        return "Quantity must be greater than 0"
    if kind not in ORDER_KINDS:
        return "kind must be one of 'limit', 'stop', 'stop_limit'"
    if time_in_force not in TIME_IN_FORCE_VALUES:
        return "time_in_force must be 'day' or 'gtc'"

    # Required-fields matrix per kind
    if kind == "limit":
        if limit_price is None:
            return "limit_price is required for kind 'limit'"
        if stop_price is not None:
            return "stop_price is not allowed for kind 'limit'"
    elif kind == "stop":
        if stop_price is None:
            return "stop_price is required for kind 'stop'"
        if limit_price is not None:
            return "limit_price is not allowed for kind 'stop'"
    else:  # stop_limit
        if limit_price is None or stop_price is None:
            return "kind 'stop_limit' requires both limit_price and stop_price"

    # Price positivity
    if limit_price is not None and limit_price <= 0:
        return "Limit price must be greater than 0"
    if stop_price is not None and stop_price <= 0:
        return "Stop price must be greater than 0"

    # Wrong-side stops rejected: a stop that is already on the triggering side
    # of the market would fire instantly (it is really a market order).
    if kind in {"stop", "stop_limit"}:
        bid, ask = _quote_bid_ask(quote)
        if side == "sell" and stop_price >= bid:
            return "Stop price must be below the market"
        if side == "buy" and stop_price <= ask:
            return "Stop price must be above the market"

    return None


def place_order_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    *,
    ticker: str,
    side: str,
    quantity: float,
    kind: str,
    limit_price: float | None,
    stop_price: float | None,
    time_in_force: str | None,
    commission_bps: float = 0.0,
) -> dict:
    """Place an order (limit / stop / stop_limit) on an open SQLite connection.

    Shared placement path for the POST /api/portfolio/orders route and the
    chat auto-execution pipeline (M2.1). All validation failures — including a
    marketable limit fill failing trade validation (e.g. insufficient cash) —
    return ``{"status": "failed", "ticker": T, "error": msg}`` and NEVER
    raise. On success returns the full public order JSON (``_order_dict``
    shape) with status ``'open'`` (resting) or ``'filled'`` (immediately
    marketable limit; the trade, its portfolio snapshot, and the order row all
    land on the caller's connection).

    Transaction semantics: does NOT commit — the caller owns the transaction
    boundary (mirroring ``execute_trade_on_conn``). If no transaction is open,
    a ``BEGIN IMMEDIATE`` is issued first (same TOCTOU discipline as trades).
    All writes happen inside a SAVEPOINT so a mid-placement failure (price
    racing off the limit, or the fill failing validation) unwinds only THIS
    order's writes — sibling writes already on the caller's transaction (e.g.
    the chat flow's earlier trades) are untouched. A failed placement leaves
    the connection exactly as it was found (plus, possibly, the BEGIN
    IMMEDIATE this function issued — callers roll back or commit as usual).

    Args:
        conn: An open SQLite connection (caller manages lifecycle/commit).
        price_cache: Live price cache for validation and marketable fills.
        ticker: Ticker symbol (normalized with .strip().upper() internally).
        side: "buy" or "sell" (normalized to lowercase internally).
        quantity: Number of shares (must be > 0).
        kind: "limit" | "stop" | "stop_limit" (normalized to lowercase).
        limit_price: Required for limit/stop_limit; forbidden for stop.
        stop_price: Required for stop/stop_limit; forbidden for limit.
        time_in_force: "day" | "gtc"; None/empty defaults to "gtc" (LLM
            structured outputs may emit null for optional fields).
        commission_bps: Commission in basis points applied to immediate fills.
    """
    ticker = ticker.strip().upper()
    side = side.lower()
    kind = kind.lower()
    time_in_force = (time_in_force or "gtc").lower()

    quote = price_cache.get(ticker)
    error = _validate_order_request(
        side=side,
        kind=kind,
        time_in_force=time_in_force,
        quantity=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        quote=quote,
    )
    if error is not None:
        return {"status": "failed", "ticker": ticker, "error": error}

    order_id = str(uuid.uuid4())
    created_dt = datetime.now(timezone.utc)
    created_at = created_dt.isoformat()
    # 'day' orders expire 24 hours from placement until M3's session clock
    # provides a real session close; 'gtc' orders never expire.
    expires_at = (
        (created_dt + DAY_ORDER_TTL).isoformat() if time_in_force == "day" else None
    )
    # Only limit orders can fill at placement. Stops never fill immediately:
    # the wrong-side check above guarantees they are untriggered right now.
    marketable = (
        kind == "limit" and _marketable_price(quote, side, limit_price) is not None
    )

    # Take the write lock up front when we own the transaction (TOCTOU
    # discipline, mirroring execute_trade_on_conn), then scope this order's
    # writes to a savepoint so failures unwind without touching sibling
    # writes on a caller-owned transaction (the chat batch).
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT place_order")

    status = "open"
    filled_at: str | None = None
    fill_price: float | None = None
    fill_trade_id: str | None = None
    try:
        if marketable:
            outcome = _execute_fill(
                conn, price_cache, ticker, side, quantity, limit_price, commission_bps
            )
            if outcome["status"] == "executed":
                status = "filled"
                filled_at = datetime.now(timezone.utc).isoformat()
                fill_price = outcome["price"]
                fill_trade_id = outcome["trade_id"]
                # Spec §7: snapshot immediately after trade execution — joins
                # the caller's transaction alongside the fill.
                _record_snapshot(conn, price_cache)
            elif outcome["status"] == "not_marketable":
                # Price moved off the limit between check and execution —
                # undo the trade writes and rest the order as open instead.
                conn.execute("ROLLBACK TO SAVEPOINT place_order")
            else:
                # Fill failed validation (e.g. insufficient cash): store
                # NOTHING for this order and report the failure.
                conn.execute("ROLLBACK TO SAVEPOINT place_order")
                conn.execute("RELEASE SAVEPOINT place_order")
                return {"status": "failed", "ticker": ticker, "error": outcome["error"]}

        conn.execute(
            """
            INSERT INTO orders (
                id, user_id, ticker, side, quantity, kind, limit_price,
                stop_price, time_in_force, expires_at, triggered_at,
                status, reject_reason, created_at, filled_at, fill_price,
                fill_trade_id
            )
            VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)
            """,
            (
                order_id, ticker, side, quantity, kind,
                limit_price, stop_price, time_in_force,
                expires_at, status, created_at, filled_at, fill_price,
                fill_trade_id,
            ),
        )
    except Exception:
        # Unexpected error: unwind this order's writes, keep the caller's
        # transaction (and any sibling writes) intact, and re-raise.
        conn.execute("ROLLBACK TO SAVEPOINT place_order")
        conn.execute("RELEASE SAVEPOINT place_order")
        raise
    conn.execute("RELEASE SAVEPOINT place_order")

    return _order_dict(
        order_id=order_id,
        ticker=ticker,
        side=side,
        quantity=quantity,
        kind=kind,
        limit_price=limit_price,
        stop_price=stop_price,
        time_in_force=time_in_force,
        expires_at=expires_at,
        triggered_at=None,
        status=status,
        created_at=created_at,
        filled_at=filled_at,
        fill_price=fill_price,
    )


def create_orders_router(
    price_cache: PriceCache, db_path: str, commission_bps: float = 0.0
) -> APIRouter:
    """Factory: build the orders APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.
        commission_bps: Commission in basis points of notional applied to every
            fill (FINALLY_COMMISSION_BPS, read once at app startup in main.py).

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/portfolio", tags=["orders"])

    @router.post("/orders")
    async def place_order(body: PlaceOrderRequest) -> dict:
        """Place an order (limit, stop, or stop_limit).

        Thin HTTP wrapper over ``place_order_on_conn``. Marketable LIMIT
        orders (buy: ask <= limit_price; sell: bid >= limit_price) execute
        immediately through the same transactional path as market orders —
        positions/cash/trades row/snapshot and the 'filled' order row commit
        atomically. If placement fails validation (including the immediate
        fill failing, e.g. insufficient cash), HTTP 400 is returned and
        NOTHING is stored. Non-marketable limit orders and all
        stop/stop_limit orders are stored as status 'open' for the fill loop.
        time_in_force 'day' stamps expires_at = created_at + 24h; equity day
        orders additionally expire at session close in session mode (M3.1).
        """
        conn = get_conn(db_path)
        try:
            result = place_order_on_conn(
                conn,
                price_cache,
                ticker=body.ticker,
                side=body.side,
                quantity=body.quantity,
                kind=body.kind,
                limit_price=body.limit_price,
                stop_price=body.stop_price,
                time_in_force=body.time_in_force,
                commission_bps=commission_bps,
            )
            if result["status"] == "failed":
                # place_order_on_conn already unwound its own writes; the
                # rollback just releases any BEGIN IMMEDIATE it took.
                conn.rollback()
                return JSONResponse(status_code=400, content={"error": result["error"]})
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception(
                "Unexpected error placing %s order %s %s %s (limit=%s stop=%s)",
                body.kind, body.side, body.quantity, body.ticker,
                body.limit_price, body.stop_price,
            )
            raise
        finally:
            conn.close()

        return {"order": result}

    @router.get("/orders")
    async def list_orders(status: str | None = None, limit: str | None = None) -> dict:
        """List orders, newest first (created_at DESC, rowid DESC tie-break).

        Query params:
            status: 'open' | 'filled' | 'cancelled' | 'rejected' | 'expired'
                | 'all' (default 'all'). Invalid values return HTTP 400.
            limit: maximum number of orders to return. Defaults to 50 and is
                clamped to the range 1..500. Non-integer values return HTTP 400.
        """
        status_value = (status if status is not None else "all").lower()
        if status_value != "all" and status_value not in ORDER_STATUSES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        "status must be one of 'open', 'filled', 'cancelled', "
                        "'rejected', 'expired', 'all'"
                    )
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

        query = f"""
            SELECT {_ORDER_SELECT_COLUMNS}
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
        are not open (filled/cancelled/rejected/expired) return HTTP 400.
        BEGIN IMMEDIATE serializes the status check against a concurrent fill.
        """
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""
                SELECT {_ORDER_SELECT_COLUMNS}
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
