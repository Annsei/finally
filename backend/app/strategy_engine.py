"""Live strategy engine for FinAlly (P2 §3 — the strategies table's evaluator).

Every ~1 second the background loop scans ALL users' ``status='live'``
strategies (the rules-engine pattern) and, per strategy:

- **Holding (open_qty > 0)** — exit checks against the live quote, in the
  contract-fixed priority ``stop_loss -> trailing_stop -> take_profit ->
  max_holding_days``. The trailing stop measures the pullback from the
  persisted ``high_water`` mark, which is raised to the current price each
  pass (after the exit checks — backtest parity) and written back. Under T+1
  (``profile.t_plus > 0``) every exit is skipped while ``opened_at`` falls on
  the current UTC calendar day (aligned with the backtest's synthetic-day
  deferral; the calendar-day reading is the same convention
  ``max_holding_days`` uses). A triggered exit market-sells the whole
  ``open_qty`` with ``strategy_id`` attribution.
- **Flat (open_qty == 0)** — entry evaluation through the shared
  ``app.indicators`` condition evaluator: the ticker's 1-second ring buffer
  is aggregated to completed one-minute bars (memoized per ticker within a
  pass) and the live ``PriceUpdate`` is the quote. On a hit, sizing resolves
  the quantity — ``fixed_qty`` as-is; ``cash_pct`` buys
  ``floor(cash * pct% / ask)`` whole shares, floored to whole board lots
  (整手) on lot-sized profiles — and a market buy executes with
  ``strategy_id`` attribution.

State discipline (contract §3):

- Every executed entry/exit commits, in ONE transaction: the trade, the
  strategy-row state update, a portfolio snapshot, and an assistant
  ``chat_messages`` row with ``kind='strategy'`` and
  ``actions={"trades": [outcome], "strategy_id": id}`` (the rules fire-message
  pattern, so the frontend's trade badges render).
- ``cooldown_until`` (REAL epoch): a failed entry trade (insufficient cash,
  market closed, ...) or a cash_pct sizing that cannot afford one share/lot
  sets ``now + 60s``; evaluation is skipped until it expires and a successful
  entry clears it. This is the anti-thrash guard against 1-second retries.
- A sell rejected for insufficient shares (the user manually sold out from
  under the strategy) CLEARS the open state and leaves a ``kind='strategy'``
  chat note — otherwise the exit would dead-loop forever. Any other sell
  rejection (e.g. "Market closed", the T+1 lock) skips and retries next pass
  with no state change.
- ``paused`` strategies are fully frozen (not scanned — neither entries nor
  exits run) and ``archived`` rows had their open state cleared at archive
  time; only ``status='live'`` rows are ever evaluated.
- Per-strategy exception isolation: one bad row rolls back, logs, and never
  stops the pass (rules-loop discipline).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import time
import uuid
from datetime import date, datetime, timezone

from app.db.connection import get_conn
from app.indicators import aggregate_minute_bars, evaluate_condition_group
from app.market.cache import PriceCache
from app.market.models import PriceUpdate
from app.market.profiles import MarketProfile
from app.market.session import SessionClock

# Bind the strategy-attributing trade impl to the legacy module name (the
# rules-engine convention): strategy fills must carry ``strategy_id`` and a
# market ``profile``, and tests monkeypatch
# ``strategy_engine.execute_trade_on_conn`` for isolation scenarios. The
# public 8-parameter wrapper stays frozen in app.routes.portfolio.
from app.routes.portfolio import _execute_trade_impl as execute_trade_on_conn
from app.routes.portfolio import _record_snapshot

logger = logging.getLogger(__name__)

# Anti-thrash pause after a failed/unaffordable entry (contract §3).
COOLDOWN_SECONDS = 60.0

# The exact rejection _execute_trade_impl returns when the position no longer
# covers the strategy's open quantity — the one sell failure that must CLEAR
# the strategy's open state instead of retrying forever.
INSUFFICIENT_SHARES_ERROR = "Insufficient shares to sell"

_STRATEGY_COLUMNS = (
    "id, user_id, name, ticker, status, entry, exits, sizing, template, "
    "created_at, deployed_at, open_qty, open_price, opened_at, high_water, "
    "cooldown_until, entered_count, exited_count, last_fired_at"
)


def _load_json(value) -> dict:
    """Parse a strategy JSON TEXT column; malformed/NULL data becomes {}."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _iso_date(iso_timestamp: str) -> date | None:
    """Calendar date of an ISO timestamp string; None when unparsable."""
    try:
        return date.fromisoformat(iso_timestamp[:10])
    except (TypeError, ValueError):
        return None


def _ask_price(quote: PriceUpdate) -> float:
    """The price a market buy would fill at — the same selection as the fill
    path in ``_execute_trade_impl`` (ask when a real spread is quoted, else
    the last price)."""
    if quote.bid is not None and quote.ask is not None and quote.bid != quote.ask:
        return quote.ask
    return quote.price


def entry_quantity(
    sizing: dict, cash: float, ask: float, profile: MarketProfile | None
) -> float:
    """Resolve the sizing mode to a share quantity (contract §3).

    ``fixed_qty`` returns the configured quantity as-is. ``cash_pct`` buys
    whole shares of ``cash * pct%`` at the ask, floored to whole board lots
    (整手) when the profile is lot-sized — the same math the backtest's
    cash_pct path uses. Returns 0.0 when the budget cannot afford one
    share/lot (the caller skips and sets the cooldown).
    """
    if sizing.get("mode") == "fixed_qty":
        return float(sizing["qty"])
    if ask <= 0:
        return 0.0
    qty = float(math.floor(cash * float(sizing["pct"]) / 100.0 / ask))
    lot_size = profile.lot_size if profile is not None else 1
    if lot_size > 1:
        qty = float(math.floor(qty / lot_size) * lot_size)
    return qty


def exit_reason(
    exits: dict,
    open_price: float,
    high_water: float | None,
    price: float,
    opened_at: str | None,
    max_holding_ref: date | None = None,
) -> str | None:
    """First triggered exit in the contract-fixed priority, or None.

    Priority (P2 §3, identical to the backtest's intrabar order):
    ``stop_loss -> trailing_stop -> take_profit -> max_holding_days``. The
    trailing level is measured from the PERSISTED high-water mark (raised
    after the checks each pass — backtest parity: the current pass's own
    price never feeds the level it is checked against).
    ``max_holding_days`` compares UTC calendar days between ``opened_at``
    and today (``max_holding_ref`` overrides "today" for tests). The
    calendar-day reading differs from the backtest's synthetic days by
    design — contract-registered, not a defect.
    """
    stop_loss_pct = exits.get("stop_loss_pct")
    if stop_loss_pct and price <= open_price * (1.0 - float(stop_loss_pct) / 100.0):
        return "stop_loss"
    trailing_stop_pct = exits.get("trailing_stop_pct")
    if (
        trailing_stop_pct
        and high_water is not None
        and price <= high_water * (1.0 - float(trailing_stop_pct) / 100.0)
    ):
        return "trailing_stop"
    take_profit_pct = exits.get("take_profit_pct")
    if take_profit_pct and price >= open_price * (1.0 + float(take_profit_pct) / 100.0):
        return "take_profit"
    max_holding_days = exits.get("max_holding_days")
    if max_holding_days is not None and opened_at:
        opened_date = _iso_date(opened_at)
        today = max_holding_ref if max_holding_ref is not None else _utc_today()
        if opened_date is not None and (today - opened_date).days >= int(
            max_holding_days
        ):
            return "max_holding_days"
    return None


def _insert_strategy_note(
    conn: sqlite3.Connection,
    strategy: sqlite3.Row,
    content: str,
    outcome: dict,
    now: str,
) -> None:
    """Insert the kind='strategy' assistant chat row documenting an action.

    actions is ``{"trades": [outcome], "strategy_id": id}`` (contract §3 —
    the rules fire-message pattern) so the existing trade badges render.
    """
    actions = json.dumps({"trades": [outcome], "strategy_id": strategy["id"]})
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, actions, kind, created_at) "
        "VALUES (?, ?, 'assistant', ?, ?, 'strategy', ?)",
        (str(uuid.uuid4()), strategy["user_id"], content, actions, now),
    )


def _set_cooldown(conn: sqlite3.Connection, strategy_id: str, until: float) -> None:
    """Stamp cooldown_until (only while still live) and commit."""
    conn.execute(
        "UPDATE strategies SET cooldown_until = ? WHERE id = ? AND status = 'live'",
        (until, strategy_id),
    )
    conn.commit()


def _process_exit(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    strategy: sqlite3.Row,
    quote: PriceUpdate,
    commission_bps: float,
    profile: MarketProfile | None,
    session_clock: SessionClock | None,
) -> str:
    """Exit evaluation for a holding strategy. Returns a counts key."""
    exits = _load_json(strategy["exits"])
    open_price = strategy["open_price"]
    high_water = strategy["high_water"]
    price = quote.price
    currency = profile.currency_symbol if profile is not None else "$"

    def _raise_high_water() -> None:
        # The trailing reference rises with the live price every pass —
        # including T+1-gated entry-day passes (backtest parity). Only
        # written when a trailing stop actually reads it.
        if exits.get("trailing_stop_pct") and (high_water is None or price > high_water):
            conn.execute(
                "UPDATE strategies SET high_water = ? WHERE id = ? AND status = 'live'",
                (price, strategy["id"]),
            )
            conn.commit()

    # T+1 (contract §3): no exit on the entry's UTC calendar day. main.py
    # neutralizes t_plus on the loop's profile in 24/7 mode, so this gate is
    # active only when a next trading day actually exists.
    if (
        profile is not None
        and profile.t_plus > 0
        and strategy["opened_at"]
        and _iso_date(strategy["opened_at"]) == _utc_today()
    ):
        _raise_high_water()
        return "skipped"

    if open_price is None or open_price <= 0:
        # Unreadable open state (hand-edited row) — nothing sane to compare
        # against; leave it alone rather than guessing an exit.
        return "skipped"

    reason = exit_reason(exits, open_price, high_water, price, strategy["opened_at"])
    if reason is None:
        _raise_high_water()
        return "skipped"

    outcome = execute_trade_on_conn(
        conn,
        price_cache,
        strategy["ticker"],
        "sell",
        strategy["open_qty"],
        commission_bps=commission_bps,
        session_clock=session_clock,
        user_id=strategy["user_id"],
        profile=profile,
        strategy_id=strategy["id"],
    )
    now = datetime.now(timezone.utc).isoformat()

    if outcome["status"] == "executed":
        cur = conn.execute(
            """
            UPDATE strategies
            SET open_qty = 0, open_price = NULL, opened_at = NULL,
                high_water = NULL, cooldown_until = NULL,
                exited_count = exited_count + 1, last_fired_at = ?
            WHERE id = ? AND status = 'live'
            """,
            (now, strategy["id"]),
        )
        if cur.rowcount == 0:
            # Paused/archived/deleted between the scan and the write lock —
            # undo the trade and leave everything untouched.
            conn.rollback()
            return "skipped"
        _record_snapshot(conn, price_cache, strategy["user_id"])
        content = (
            f"Strategy exit ({reason}): '{strategy['name']}' sold "
            f"{strategy['open_qty']:g} {strategy['ticker']} @ "
            f"{currency}{outcome['price']:.2f}."
        )
        _insert_strategy_note(conn, strategy, content, outcome, now)
        conn.commit()
        logger.info(
            "Strategy %s exited (%s): sold %s %s @ %s",
            strategy["id"], reason, strategy["open_qty"], strategy["ticker"],
            outcome["price"],
        )
        return "exited"

    if outcome["error"] == INSUFFICIENT_SHARES_ERROR:
        # The user manually sold the shares out from under the strategy —
        # clear the phantom open state (or the exit would retry forever) and
        # leave a note. The real shares stay wherever the user put them.
        cur = conn.execute(
            """
            UPDATE strategies
            SET open_qty = 0, open_price = NULL, opened_at = NULL,
                high_water = NULL, cooldown_until = NULL
            WHERE id = ? AND status = 'live'
            """,
            (strategy["id"],),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return "skipped"
        content = (
            f"Strategy '{strategy['name']}' tried to exit ({reason}) but could "
            f"not sell {strategy['open_qty']:g} {strategy['ticker']}: "
            f"{outcome['error']}. Position tracking cleared."
        )
        _insert_strategy_note(conn, strategy, content, outcome, now)
        conn.commit()
        logger.info(
            "Strategy %s exit failed (%s) — open state cleared",
            strategy["id"], outcome["error"],
        )
        return "trade_failed"

    # Market closed, T+1 lock, ... — transient: no state change, the next
    # pass retries (contract §3).
    conn.rollback()
    return "skipped"


def _process_entry(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    strategy: sqlite3.Row,
    quote: PriceUpdate,
    bars_1m: list[dict],
    commission_bps: float,
    profile: MarketProfile | None,
    session_clock: SessionClock | None,
) -> str:
    """Entry evaluation for a flat strategy. Returns a counts key."""
    entry = _load_json(strategy["entry"])
    if not evaluate_condition_group(entry, bars_1m, quote):
        return "skipped"

    sizing = _load_json(strategy["sizing"])
    user_row = conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = ?",
        (strategy["user_id"],),
    ).fetchone()
    cash = user_row["cash_balance"] if user_row else 0.0
    quantity = entry_quantity(sizing, cash, _ask_price(quote), profile)
    now_epoch = time.time()
    if quantity <= 0:
        # cash_pct cannot afford one share/lot — cooldown, retry in 60s.
        _set_cooldown(conn, strategy["id"], now_epoch + COOLDOWN_SECONDS)
        return "skipped"

    outcome = execute_trade_on_conn(
        conn,
        price_cache,
        strategy["ticker"],
        "buy",
        quantity,
        commission_bps=commission_bps,
        session_clock=session_clock,
        user_id=strategy["user_id"],
        profile=profile,
        strategy_id=strategy["id"],
    )
    if outcome["status"] != "executed":
        # Insufficient cash / market closed / ... — cooldown so the 1-second
        # loop does not hammer the same rejection (contract §3).
        conn.rollback()
        _set_cooldown(conn, strategy["id"], now_epoch + COOLDOWN_SECONDS)
        logger.info(
            "Strategy %s entry failed: %s", strategy["id"], outcome["error"]
        )
        return "trade_failed"

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        UPDATE strategies
        SET open_qty = ?, open_price = ?, opened_at = ?, high_water = ?,
            cooldown_until = NULL, entered_count = entered_count + 1,
            last_fired_at = ?
        WHERE id = ? AND status = 'live'
        """,
        (quantity, outcome["price"], now, outcome["price"], now, strategy["id"]),
    )
    if cur.rowcount == 0:
        conn.rollback()
        return "skipped"
    _record_snapshot(conn, price_cache, strategy["user_id"])
    currency = profile.currency_symbol if profile is not None else "$"
    content = (
        f"Strategy fired: '{strategy['name']}' bought {quantity:g} "
        f"{strategy['ticker']} @ {currency}{outcome['price']:.2f}."
    )
    _insert_strategy_note(conn, strategy, content, outcome, now)
    conn.commit()
    logger.info(
        "Strategy %s entered: bought %s %s @ %s",
        strategy["id"], quantity, strategy["ticker"], outcome["price"],
    )
    return "entered"


def process_strategies_once(
    db_path: str,
    price_cache: PriceCache,
    commission_bps: float = 0.0,
    profile: MarketProfile | None = None,
    *,
    session_clock: SessionClock | None = None,
) -> dict[str, int]:
    """One scan pass over ALL users' live strategies (contract §3).

    Opens (and always closes) its own connection; a single query selects
    ``status='live'`` across every user (the rules-engine pattern), oldest
    first. Each strategy is processed in its own transaction with exception
    isolation — one bad row rolls back, logs, and the pass continues. The
    1-second ring-buffer history is aggregated to completed one-minute bars
    at most ONCE per ticker per pass (memoized), shared by every flat
    strategy watching that ticker.

    Args:
        db_path: Path to the SQLite database file.
        price_cache: Live price cache (quotes + 1-second bar history).
        commission_bps: Commission applied to strategy fills (main.py's
            startup value — the same friction as every other execution path).
        profile: Active market profile — main.py passes the 24/7-neutralized
            ``trading_profile`` so T+1 only gates when a next trading day
            exists. Drives 整手 sizing, fees, and the T+1 entry-day exit skip.
        session_clock: Session clock (keyword-only). Threaded into the trade
            helper so entries/exits attempted while the market is closed are
            rejected ("Market closed") instead of filling at frozen quotes —
            a closed-market exit rejection skips and retries next pass.

    Returns:
        Counts for observability/tests:
        ``{"entered": n, "exited": n, "skipped": n, "trade_failed": n}``.
        "skipped" covers no-quote, cooldown, unmet conditions, T+1-gated or
        transiently-rejected exits, and concurrent status flips;
        "trade_failed" covers failed entry trades (cooldown stamped) and the
        insufficient-shares exit (open state cleared).
    """
    counts = {"entered": 0, "exited": 0, "skipped": 0, "trade_failed": 0}
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT {_STRATEGY_COLUMNS}
            FROM strategies
            WHERE status = 'live'
            ORDER BY created_at ASC, rowid ASC
            """
        ).fetchall()
        bars_by_ticker: dict[str, list[dict]] = {}
        now_epoch = time.time()
        for row in rows:
            try:
                quote = price_cache.get(row["ticker"])
                if quote is None:
                    counts["skipped"] += 1
                    continue
                if row["open_qty"] > 0:
                    result = _process_exit(
                        conn, price_cache, row, quote,
                        commission_bps, profile, session_clock,
                    )
                else:
                    cooldown = row["cooldown_until"]
                    if cooldown is not None and now_epoch < cooldown:
                        counts["skipped"] += 1
                        continue
                    ticker = row["ticker"]
                    if ticker not in bars_by_ticker:
                        bars_by_ticker[ticker] = aggregate_minute_bars(
                            price_cache.get_history(ticker)
                        )
                    result = _process_entry(
                        conn, price_cache, row, quote, bars_by_ticker[ticker],
                        commission_bps, profile, session_clock,
                    )
            except Exception:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                logger.exception(
                    "Strategy loop: error processing strategy %s — continuing",
                    row["id"],
                )
                continue
            counts[result] += 1
    finally:
        conn.close()
    return counts


async def strategies_eval_loop(
    price_cache: PriceCache,
    db_path: str,
    interval: float = 1.0,
    commission_bps: float = 0.0,
    profile: MarketProfile | None = None,
    session_clock: SessionClock | None = None,
) -> None:
    """Background task: evaluate live strategies every ``interval`` seconds.

    Runs indefinitely until cancelled via ``asyncio.CancelledError``; any
    other exception is logged and the loop continues (rules-loop discipline).
    main.py registers this in the lifespan with the 24/7-neutralized
    ``trading_profile`` and the app session clock, and lists the task in
    ``background_tasks`` for shutdown cancellation (contract §3).
    """
    while True:
        try:
            process_strategies_once(
                db_path,
                price_cache,
                commission_bps,
                profile,
                session_clock=session_clock,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Strategies eval loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)
