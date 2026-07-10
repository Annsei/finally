"""Portfolio API routes for FinAlly.

Provides:
- GET /api/portfolio — current positions, cash, and total portfolio value
- POST /api/portfolio/trade — market order execution (buy/sell)
- GET /api/portfolio/trades — trade blotter (executed trades, newest first)
- GET /api/portfolio/history — portfolio value snapshots over time
- GET /api/portfolio/analytics — trading analytics summary (M3.4): win rate,
  realized P&L, max drawdown, Sharpe, best/worst trade, sector allocation

All routes are created via the factory function ``create_portfolio_router`` which
closes over the shared ``PriceCache`` instance and the database path.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, FiniteFloat

from app.auth import get_current_user_id
from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.profiles import MarketProfile
from app.market.seed_prices import asset_class_for, sector_for
from app.market.session import SessionClock
from app.mechanics import (
    compute_fee,
    lot_size_error,
    market_closed_message,
    t1_applies,
    t1_sell_error,
)

logger = logging.getLogger(__name__)

# Error message for market orders on equities while the session is closed
# (M3.1). Contract fixed — the frontend and chat tests match on this string.
# Localized per profile via mechanics.market_closed_message (CN-2 §5: 休市中).
MARKET_CLOSED_ERROR = "Market closed"


class TradeRequest(BaseModel):
    ticker: str
    quantity: FiniteFloat
    side: str  # "buy" or "sell"


def _record_snapshot(
    conn: sqlite3.Connection, price_cache: PriceCache, user_id: str = "default"
) -> None:
    """Compute a user's current total portfolio value and insert a snapshot row.

    Does NOT commit — the caller owns the transaction boundary and must
    commit (or roll back) the inserted row.

    Args:
        conn: An open SQLite connection (caller manages lifecycle and commit).
        price_cache: Live price cache for current market prices.
        user_id: The user to snapshot (M4 — defaults to the anonymous user).
    """
    row = conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
    ).fetchone()
    cash_balance: float = row["cash_balance"] if row else 0.0

    positions = conn.execute(
        "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ?", (user_id,)
    ).fetchall()

    total_value = cash_balance + sum(
        p["quantity"] * (price_cache.get_price(p["ticker"]) or p["avg_cost"])
        for p in positions
    )

    conn.execute(
        "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, total_value, datetime.now(timezone.utc).isoformat()),
    )


def record_snapshots_for_all_users(
    conn: sqlite3.Connection, price_cache: PriceCache
) -> int:
    """Insert one portfolio snapshot per user profile (M4 snapshot task).

    Does NOT commit — the caller owns the transaction boundary. Returns the
    number of users snapshotted.
    """
    user_ids = [
        row["id"] for row in conn.execute("SELECT id FROM users_profile ORDER BY id")
    ]
    for user_id in user_ids:
        _record_snapshot(conn, price_cache, user_id)
    return len(user_ids)


# Sharpe needs a minimally meaningful equity curve (M3.4 contract: null below
# this many snapshots).
MIN_SHARPE_SNAPSHOTS = 10


def _max_drawdown_pct(values: list[float]) -> float | None:
    """Maximum peak-to-trough drawdown of an equity curve, as a positive %.

    Walks the curve tracking the running peak; the drawdown at each point is
    (peak - value) / peak * 100. Returns the maximum seen (0.0 for a curve
    that never falls below its running peak), or None for fewer than 2
    points. Non-positive peaks are skipped (no meaningful percentage).
    """
    if len(values) < 2:
        return None
    peak = values[0]
    max_dd = 0.0
    for value in values[1:]:
        if value > peak:
            peak = value
        elif peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100.0)
    return round(max_dd, 4)


def _sharpe(values: list[float]) -> float | None:
    """Sharpe-style ratio from an equity curve (M3.4).

    mean / population-std of snapshot-to-snapshot returns, scaled by
    sqrt(number of returns). Returns None when the curve has fewer than
    ``MIN_SHARPE_SNAPSHOTS`` points or the returns have (near-)zero std —
    a flat curve has no meaningful risk-adjusted return.
    """
    if len(values) < MIN_SHARPE_SNAPSHOTS:
        return None
    returns = [
        (curr - prev) / prev for prev, curr in zip(values, values[1:]) if prev > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance)
    if std < 1e-12:
        return None
    return round(mean / std * math.sqrt(len(returns)), 4)


def execute_trade_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    commission_bps: float = 0.0,
    session_clock: SessionClock | None = None,
    user_id: str = "default",
) -> dict:
    """Legacy market-order entry point — the pre-CN-2 public signature, frozen.

    This 8-parameter form is the stable contract every pre-CN-2 caller (and the
    signature regression tests) depends on; it delegates to the profile-aware
    :func:`_execute_trade_on_conn` with the neutral (us/None) profile, so its
    behavior is byte-identical to the pre-CN-2 path. CN-2 callers that need
    A-share mechanics call ``_execute_trade_on_conn`` directly with a profile
    (the single additive hook — CN2 contract §0), keeping this signature
    untouched.
    """
    return _execute_trade_on_conn(
        conn,
        price_cache,
        ticker,
        side,
        quantity,
        commission_bps=commission_bps,
        session_clock=session_clock,
        user_id=user_id,
        profile=None,
    )


def _execute_trade_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    commission_bps: float = 0.0,
    session_clock: SessionClock | None = None,
    user_id: str = "default",
    profile: MarketProfile | None = None,
) -> dict:
    """Profile-aware market-order entry point — the CN-2 signature, frozen.

    This 9-parameter form (the 8 legacy parameters plus the trailing
    ``profile`` hook) is pinned by the CN-2 signature regression tests and
    is what rules/chat/orders bind to — it must not grow parameters. It
    delegates to :func:`_execute_trade_impl` with no strategy attribution
    (``strategy_id=None``), byte-identical to the pre-P2 behavior.

    P2 callers that attribute fills to a strategy (the strategy engine)
    call ``_execute_trade_impl`` directly with the keyword-only
    ``strategy_id`` — the single additive hook, mirroring how CN-2 added
    ``profile`` on this sibling without touching the public
    :func:`execute_trade_on_conn`.
    """
    return _execute_trade_impl(
        conn,
        price_cache,
        ticker,
        side,
        quantity,
        commission_bps=commission_bps,
        session_clock=session_clock,
        user_id=user_id,
        profile=profile,
        strategy_id=None,
    )


def _execute_trade_impl(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    commission_bps: float = 0.0,
    session_clock: SessionClock | None = None,
    user_id: str = "default",
    profile: MarketProfile | None = None,
    *,
    strategy_id: str | None = None,
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
        user_id: The user whose cash/positions/trades this trade touches
            (M4 — defaults to the anonymous 'default' user).
        profile: Active market profile (CN-2). None or a neutral profile (us:
            lot_size 1, t_plus 0, min_commission/stamp 0, locale en-US) is
            behavior-identical to the pre-CN-2 path — every A-share check below
            is driven purely by profile field values. When it carries A-share
            values, buys must be whole board lots (整手), the T+1 lock blocks
            same-session resale of shares bought today, the commission floor and
            sell-side stamp tax apply, and rejection messages are Chinese.
        strategy_id: Strategy attribution (P2, keyword-only). Written to
            ``trades.strategy_id`` on the fill row; None (every non-strategy
            caller) keeps the column NULL — the pre-P2 semantics.

    A-share mechanics (all no-ops for None/us — CN-2 §1-§3):
        - 整手 (lot): buy ``quantity`` must be a multiple of ``profile.lot_size``.
        - T+1: active when ``profile.t_plus > 0`` and the session clock cycles
          (disabled in 24/7 mode — locked shares would never unlock). A buy adds
          its quantity to ``positions.t1_locked``; a sell may only dispose of
          ``quantity - t1_locked`` shares.
        - Fee: ``max(min_commission, notional*bps/1e4)`` plus a sell-only stamp
          tax of ``notional*stamp_tax_bps_sell/1e4``, folded into the same
          ``trades.commission`` column and P&L math as before.

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

    # CN-2 §3: A-share buys must be whole board lots (整手). No-op for us/None
    # (lot_size <= 1) and for sells (odd-lot sells are legal).
    lot_error = lot_size_error(profile, side, quantity)
    if lot_error is not None:
        return {"status": "failed", "ticker": ticker, "error": lot_error}

    # M3.1: reject equity market orders while the session is closed — the
    # quote is frozen at the close, so a fill would execute at a stale price.
    # Crypto trades 24/7; in 24/7 mode (no sessions) is_open is always True.
    # CN-2 §5: the rejection message is localized (休市中) via the profile.
    if (
        session_clock is not None
        and not session_clock.is_open
        and asset_class_for(ticker) == "equity"
    ):
        return {
            "status": "failed",
            "ticker": ticker,
            "error": market_closed_message(profile),
        }

    if not price_cache.is_fresh(ticker):
        price_cache.warn_stale_rejection(ticker)
        return {"status": "failed", "ticker": ticker, "error": "Quote is stale"}

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
        "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
    ).fetchone()
    cash_balance: float = user_row["cash_balance"] if user_row else 0.0

    trade_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    cost = quantity * current_price  # notional at the fill price
    # Total fee on the notional, rounded to cents (CN-2 §1). With commission_bps
    # 0 and a None/us profile this reduces exactly to 0.0, and with a nonzero
    # bps and no floor/stamp it reduces to the pre-CN-2 round(notional*bps/1e4)
    # math — legacy behavior stays bit-identical. cn adds the ¥5 floor and, on
    # sells, the stamp tax.
    commission = compute_fee(cost, side, commission_bps, profile)
    realized_pnl: float | None = None

    # T+1 is active only with a positive t_plus AND a cycling session clock;
    # 24/7 mode disables it (locked shares would never unlock). No-op for us.
    t1_active = t1_applies(profile, session_clock)

    if side == "buy":
        if cash_balance < cost + commission:
            return {"status": "failed", "ticker": ticker, "error": "Insufficient cash"}

        # Deduct cash: notional plus commission
        conn.execute(
            "UPDATE users_profile SET cash_balance = cash_balance - ? WHERE id = ?",
            (cost + commission, user_id),
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
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, ticker) DO UPDATE SET
                avg_cost = (avg_cost * quantity + excluded.avg_cost * excluded.quantity)
                           / (quantity + excluded.quantity),
                quantity = quantity + excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (position_id, user_id, ticker, quantity, effective_cost, now),
        )

        # CN-2 §2: lock today's bought shares until the next session. Runs only
        # when T+1 is active, so us/None never touches t1_locked.
        if t1_active:
            conn.execute(
                "UPDATE positions SET t1_locked = t1_locked + ? "
                "WHERE user_id = ? AND ticker = ?",
                (quantity, user_id, ticker),
            )

    else:  # sell
        pos_row = conn.execute(
            "SELECT quantity, avg_cost, t1_locked FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        current_qty: float = pos_row["quantity"] if pos_row else 0.0
        avg_cost_at_sale: float = pos_row["avg_cost"] if pos_row else 0.0

        if current_qty < quantity:
            return {"status": "failed", "ticker": ticker, "error": "Insufficient shares to sell"}

        # CN-2 §2: only shares NOT bought today may be sold. sellable =
        # held - locked; a request above that is a T+1 rejection (any positive
        # sell quantity, including odd lots, is otherwise legal).
        if t1_active:
            locked: float = pos_row["t1_locked"] if pos_row else 0.0
            sellable = current_qty - locked
            if quantity > sellable:
                return {
                    "status": "failed",
                    "ticker": ticker,
                    "error": t1_sell_error(profile, sellable),
                }

        # Add cash proceeds: notional minus commission
        conn.execute(
            "UPDATE users_profile SET cash_balance = cash_balance + ? WHERE id = ?",
            (cost - commission, user_id),
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
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?",
                (user_id, ticker),
            )
        else:
            conn.execute(
                "UPDATE positions SET quantity = ?, updated_at = ? WHERE user_id = ? AND ticker = ?",
                (new_qty, now, user_id, ticker),
            )

    # Insert trade log entry (commission always stored; realized_pnl NULL on
    # buys; strategy_id NULL except for strategy-engine fills — P2)
    conn.execute(
        "INSERT INTO trades (id, user_id, ticker, side, quantity, price, commission,"
        " realized_pnl, strategy_id, executed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            trade_id,
            user_id,
            ticker,
            side,
            quantity,
            current_price,
            commission,
            realized_pnl,
            strategy_id,
            now,
        ),
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
    profile: MarketProfile | None = None,
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
        profile: Active market profile (CN-2) — drives the 整手/T+1/fee/locale
            mechanics in ``execute_trade_on_conn``. None or a neutral (us)
            profile is behavior-identical to the pre-CN-2 route.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

    @router.get("/")
    async def get_portfolio(request: Request) -> dict:
        """Return cash balance, positions, total value, and lifetime realized P&L."""
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            user_row = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
            ).fetchone()
            cash_balance: float = user_row["cash_balance"] if user_row else 0.0

            realized_row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0.0) AS total FROM trades "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            realized_pnl: float = round(realized_row["total"] or 0.0, 2)

            position_rows = conn.execute(
                "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ?",
                (user_id,),
            ).fetchall()

            positions = []
            position_market_value = 0.0
            for row in position_rows:
                ticker: str = row["ticker"]
                quantity: float = row["quantity"]
                avg_cost: float = row["avg_cost"]
                live_price = price_cache.get_price(ticker)
                quote_stale = live_price is None or not price_cache.is_fresh(ticker)
                current_price: float = avg_cost if live_price is None else live_price

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
                        "quote_stale": quote_stale,
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
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            outcome = _execute_trade_on_conn(
                conn, price_cache, body.ticker, body.side, body.quantity,
                commission_bps=commission_bps, session_clock=session_clock,
                user_id=user_id, profile=profile,
            )
            if outcome["status"] == "executed":
                # Record portfolio snapshot immediately after the trade and
                # commit both atomically (spec §7).
                _record_snapshot(conn, price_cache, user_id)
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
    async def get_trades(
        request: Request, limit: str | None = None, ticker: str | None = None
    ) -> dict:
        """Return the trade blotter: executed trades, newest first.

        Query params:
            limit: maximum number of trades to return. Defaults to 50 and is
                clamped to the range 1..500. Non-integer values return
                HTTP 400 with ``{"error": "message"}``.
            ticker: optional ticker filter (P1 §3.5) — uppercase-normalized
                exact match. Blank values are treated as absent; when absent
                the SQL and response are byte-identical to the pre-P1 shape.

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

        ticker_value = ticker.strip().upper() if ticker is not None and ticker.strip() else None

        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            # Default (no ticker) keeps the pre-P1 SQL byte-for-byte.
            query = """
                SELECT id, ticker, side, quantity, price, commission,
                       realized_pnl, executed_at
                FROM trades
                WHERE user_id = ?
                ORDER BY executed_at DESC, rowid DESC
                LIMIT ?
                """
            params: tuple = (user_id, limit_value)
            if ticker_value is not None:
                query = """
                SELECT id, ticker, side, quantity, price, commission,
                       realized_pnl, executed_at
                FROM trades
                WHERE user_id = ? AND ticker = ?
                ORDER BY executed_at DESC, rowid DESC
                LIMIT ?
                """
                params = (user_id, ticker_value, limit_value)
            rows = conn.execute(query, params).fetchall()
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
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                """
                SELECT total_value, recorded_at
                FROM portfolio_snapshots
                WHERE user_id = ?
                ORDER BY recorded_at ASC
                LIMIT 500
                """,
                (user_id,),
            ).fetchall()
            return {
                "snapshots": [
                    {"total_value": row["total_value"], "recorded_at": row["recorded_at"]}
                    for row in rows
                ]
            }
        finally:
            conn.close()

    @router.get("/analytics")
    async def get_analytics(request: Request) -> dict:
        """Trading analytics summary (M3.4). Contract fixed — frontend built
        in parallel:

            {"total_trades": int,
             "sell_trades": int,
             "win_rate": float | null,        # wins / sells with realized_pnl
             "realized_pnl": float,           # lifetime sum
             "max_drawdown_pct": float | null,  # positive %; null if < 2 snapshots
             "sharpe": float | null,          # null if < 10 snapshots or zero std
             "best_trade": {...} | null,      # max realized_pnl sell
             "worst_trade": {...} | null,     # min realized_pnl sell
             "sector_allocation": [{"sector", "value", "weight"}]}

        best_trade/worst_trade carry {"ticker", "side", "quantity", "price",
        "realized_pnl", "executed_at"}. sector_allocation values positions at
        the live cache price, grouped by ``sector_for``, plus a "cash" entry
        (always present); weights sum to ~1.0 of total portfolio value and
        rows are sorted by value descending. Cheap to compute per request —
        no caching.
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            totals = conn.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END), 0) AS sells, "
                "COALESCE(SUM(realized_pnl), 0.0) AS realized "
                "FROM trades WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            pnl_sells = conn.execute(
                "SELECT ticker, side, quantity, price, realized_pnl, executed_at "
                "FROM trades WHERE user_id = ? "
                "AND side = 'sell' AND realized_pnl IS NOT NULL",
                (user_id,),
            ).fetchall()

            win_rate: float | None = None
            best_trade: dict | None = None
            worst_trade: dict | None = None
            if pnl_sells:
                wins = sum(1 for row in pnl_sells if row["realized_pnl"] > 0)
                win_rate = round(wins / len(pnl_sells), 4)

                def _trade_payload(row: sqlite3.Row) -> dict:
                    return {
                        "ticker": row["ticker"],
                        "side": row["side"],
                        "quantity": row["quantity"],
                        "price": row["price"],
                        "realized_pnl": row["realized_pnl"],
                        "executed_at": row["executed_at"],
                    }

                best_trade = _trade_payload(
                    max(pnl_sells, key=lambda row: row["realized_pnl"])
                )
                worst_trade = _trade_payload(
                    min(pnl_sells, key=lambda row: row["realized_pnl"])
                )

            snapshot_values = [
                row["total_value"]
                for row in conn.execute(
                    "SELECT total_value FROM portfolio_snapshots "
                    "WHERE user_id = ? ORDER BY recorded_at ASC, rowid ASC",
                    (user_id,),
                )
            ]

            user_row = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
            ).fetchone()
            cash_balance: float = user_row["cash_balance"] if user_row else 0.0

            sector_values: dict[str, float] = {"cash": cash_balance}
            for row in conn.execute(
                "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ?",
                (user_id,),
            ):
                sector = sector_for(row["ticker"])
                value = row["quantity"] * (
                    price_cache.get_price(row["ticker"]) or row["avg_cost"]
                )
                sector_values[sector] = sector_values.get(sector, 0.0) + value

            total_value = sum(sector_values.values())
            sector_allocation = [
                {
                    "sector": sector,
                    "value": round(value, 2),
                    "weight": round(value / total_value, 6) if total_value > 0 else 0.0,
                }
                for sector, value in sorted(
                    sector_values.items(), key=lambda item: item[1], reverse=True
                )
            ]

            return {
                "total_trades": totals["total"] or 0,
                "sell_trades": totals["sells"] or 0,
                "win_rate": win_rate,
                "realized_pnl": round(totals["realized"] or 0.0, 2),
                "max_drawdown_pct": _max_drawdown_pct(snapshot_values),
                "sharpe": _sharpe(snapshot_values),
                "best_trade": best_trade,
                "worst_trade": worst_trade,
                "sector_allocation": sector_allocation,
            }
        finally:
            conn.close()

    return router
