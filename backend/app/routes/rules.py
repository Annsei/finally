"""Standing rules engine for FinAlly (M2.2 — durable, user-authored agency).

A rule is a one-shot automation: "when <ticker>'s <trigger> crosses
<threshold>, execute a market <side> of <quantity> shares". Rules are created
via REST or by the AI through the chat structured-output pipeline, evaluated
every ~1 second against live quotes, and consumed on firing (status moves to
'fired' — even when the resulting trade fails validation). The user re-arms a
fired rule via PATCH {"status": "active"}.

Provides:
- GET    /api/rules            — list rules (status filter, newest first)
- POST   /api/rules            — create a rule
- PATCH  /api/rules/{rule_id}  — set status to 'active' (re-arm) or 'paused'
- DELETE /api/rules/{rule_id}  — hard-delete a rule

plus the background evaluator:
- ``process_rules_once(db_path, price_cache, commission_bps)`` — one scan
  pass over active rules (synchronous, unit-testable): fires triggered rules,
  executes their market trades, and documents each activation as an assistant
  chat message
- ``rules_eval_loop(price_cache, db_path, interval, commission_bps)`` —
  asyncio background task wired in main.py's lifespan, calling the pass every
  ~1 second

and the shared creation helper:
- ``create_rule_on_conn(conn, price_cache, *, ...)`` — validates and inserts
  one rule on an open connection without committing; returns
  ``{"status": "created", "rule": {...}}`` or ``{"status": "failed", ...}``
  and never raises on validation failure. Used by both the POST route (which
  maps failures to HTTP 400) and the chat auto-execution pipeline.

Trigger semantics (evaluated against the cached quote):
- ``price_above``:           fires when price >= threshold
- ``price_below``:           fires when price <= threshold
- ``day_change_pct_above``:  fires when day_change_percent >= threshold
- ``day_change_pct_below``:  fires when day_change_percent <= threshold

Thresholds for price_* triggers must be positive (they are dollar prices);
day_change_pct_* thresholds may be negative (e.g. -3 = "drops 3% on the day").
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.models import PriceUpdate
from app.routes.portfolio import _record_snapshot, execute_trade_on_conn

logger = logging.getLogger(__name__)

TRIGGER_TYPES = {
    "price_above",
    "price_below",
    "day_change_pct_above",
    "day_change_pct_below",
}
RULE_STATUSES = {"active", "paused", "fired"}
# Statuses a client may set via PATCH ('fired' is only ever set by the evaluator).
PATCHABLE_STATUSES = {"active", "paused"}

_RULE_SELECT_COLUMNS = (
    "id, user_id, ticker, description, trigger_type, threshold, side, quantity, "
    "status, created_at, last_fired_at, fire_count"
)


class CreateRuleRequest(BaseModel):
    ticker: str
    trigger_type: str
    threshold: float
    side: str  # "buy" or "sell"
    quantity: float
    description: str | None = None


class UpdateRuleRequest(BaseModel):
    status: str  # "active" (re-arm) or "paused"


def _rule_row_to_dict(row: sqlite3.Row) -> dict:
    """Serialize a ``rules`` table row to the public JSON shape."""
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "description": row["description"],
        "trigger_type": row["trigger_type"],
        "threshold": row["threshold"],
        "side": row["side"],
        "quantity": row["quantity"],
        "status": row["status"],
        "created_at": row["created_at"],
        "last_fired_at": row["last_fired_at"],
        "fire_count": row["fire_count"],
    }


def generate_rule_description(
    side: str, quantity: float, ticker: str, trigger_type: str, threshold: float
) -> str:
    """Build a human-readable summary, e.g. "Buy 5 NVDA when day change <= -3%"."""
    action = f"{side.capitalize()} {quantity:g} {ticker}"
    if trigger_type == "price_above":
        return f"{action} when price >= ${threshold:g}"
    if trigger_type == "price_below":
        return f"{action} when price <= ${threshold:g}"
    if trigger_type == "day_change_pct_above":
        return f"{action} when day change >= {threshold:g}%"
    return f"{action} when day change <= {threshold:g}%"


def create_rule_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    *,
    ticker: str,
    trigger_type: str,
    threshold: float,
    side: str,
    quantity: float,
    description: str | None = None,
    user_id: str = "default",
) -> dict:
    """Validate and insert one rule on an open SQLite connection.

    Shared creation path for the POST /api/rules route and the chat
    auto-execution pipeline (M2.2). All validation failures return
    ``{"status": "failed", "ticker": T, "error": msg}`` and never raise. On
    success returns ``{"status": "created", "rule": {...}}`` with the full
    public rule JSON (status 'active', fire_count 0).

    Transaction semantics: does NOT commit — the caller owns the transaction
    boundary (mirroring ``execute_trade_on_conn``/``place_order_on_conn``), so
    the chat flow can batch rule creation with its other writes atomically.

    Args:
        conn: An open SQLite connection (caller manages lifecycle/commit).
        price_cache: Live price cache — the ticker must have a cached quote.
        ticker: Ticker symbol (normalized with .strip().upper() internally).
        trigger_type: One of TRIGGER_TYPES (normalized to lowercase).
        threshold: Trigger threshold. Must be > 0 for price_* triggers
            (dollar prices); day_change_pct_* thresholds may be negative.
        side: "buy" or "sell" (normalized to lowercase internally).
        quantity: Number of shares to trade on fire (must be > 0).
        description: Optional human summary; None/blank gets a generated one.
        user_id: Owner of the rule — evaluator fires execute on this user's
            portfolio (M4).
    """
    ticker = ticker.strip().upper()
    side = side.lower()
    trigger_type = trigger_type.strip().lower()

    if price_cache.get(ticker) is None:
        return {"status": "failed", "ticker": ticker, "error": "Ticker not found in price cache"}
    if side not in {"buy", "sell"}:
        return {"status": "failed", "ticker": ticker, "error": "Side must be 'buy' or 'sell'"}
    if quantity <= 0:
        return {"status": "failed", "ticker": ticker, "error": "Quantity must be greater than 0"}
    if trigger_type not in TRIGGER_TYPES:
        return {
            "status": "failed",
            "ticker": ticker,
            "error": (
                "trigger_type must be one of 'price_above', 'price_below', "
                "'day_change_pct_above', 'day_change_pct_below'"
            ),
        }
    if trigger_type in {"price_above", "price_below"} and threshold <= 0:
        return {
            "status": "failed",
            "ticker": ticker,
            "error": "Threshold must be greater than 0 for price triggers",
        }

    description = (description or "").strip()
    if not description:
        description = generate_rule_description(
            side, quantity, ticker, trigger_type, threshold
        )

    rule_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO rules (id, user_id, ticker, description, trigger_type,
            threshold, side, quantity, status, created_at, last_fired_at,
            fire_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL, 0)
        """,
        (rule_id, user_id, ticker, description, trigger_type, threshold, side, quantity,
         created_at),
    )
    return {
        "status": "created",
        "rule": {
            "id": rule_id,
            "ticker": ticker,
            "description": description,
            "trigger_type": trigger_type,
            "threshold": threshold,
            "side": side,
            "quantity": quantity,
            "status": "active",
            "created_at": created_at,
            "last_fired_at": None,
            "fire_count": 0,
        },
    }


# ---------------------------------------------------------------------------
# Evaluator (background rules engine)
# ---------------------------------------------------------------------------


def _rule_triggered(quote: PriceUpdate, trigger_type: str, threshold: float) -> bool:
    """True when the rule's trigger condition holds for the current quote."""
    if trigger_type == "price_above":
        return quote.price >= threshold
    if trigger_type == "price_below":
        return quote.price <= threshold
    if trigger_type == "day_change_pct_above":
        return quote.day_change_percent >= threshold
    if trigger_type == "day_change_pct_below":
        return quote.day_change_percent <= threshold
    return False  # Unknown trigger_type (bad data) — never fires.


def _fire_rule_if_triggered(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    rule: sqlite3.Row,
    commission_bps: float,
) -> str:
    """Evaluate one active rule. Returns 'fired', 'trade_failed', or 'skipped'.

    On trigger, ONE commit covers: the market trade (buy fills at ask / sell
    at bid via ``execute_trade_on_conn``), the rule's status='fired' +
    last_fired_at + fire_count update, the post-trade portfolio snapshot (on
    executed trades), and an assistant chat_messages row (kind='rule')
    documenting the activation. The rule is one-shot: a trade validation failure (e.g.
    insufficient cash) still consumes it — the failure is documented in the
    chat message and its actions JSON.

    The stored actions JSON is ``{"trades": [<trade outcome>], "rule_id": id}``
    so the frontend's existing trade badges render on rule activations.
    """
    quote = price_cache.get(rule["ticker"])
    if quote is None:
        return "skipped"  # No quote (e.g. removed from cache) — stays active.
    if not _rule_triggered(quote, rule["trigger_type"], rule["threshold"]):
        return "skipped"

    # Market trade at the current quote, on the RULE'S OWN user (M4).
    # execute_trade_on_conn issues BEGIN IMMEDIATE (no transaction is open
    # here) and never raises on validation failure; validation failures write
    # nothing.
    outcome = execute_trade_on_conn(
        conn,
        price_cache,
        rule["ticker"],
        rule["side"],
        rule["quantity"],
        commission_bps=commission_bps,
        user_id=rule["user_id"],
    )

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        UPDATE rules
        SET status = 'fired', last_fired_at = ?, fire_count = fire_count + 1
        WHERE id = ? AND status = 'active'
        """,
        (now, rule["id"]),
    )
    if cur.rowcount == 0:
        # Rule was paused/deleted between the scan and taking the write lock —
        # undo the trade and leave everything untouched.
        conn.rollback()
        return "skipped"

    if outcome["status"] == "executed":
        # Spec §7: snapshot immediately after trade execution — same commit.
        _record_snapshot(conn, price_cache, rule["user_id"])
        content = (
            f"Rule fired: {rule['description']} — executed at ${outcome['price']:.2f}."
        )
    else:
        content = f"Rule fired: {rule['description']} — trade failed: {outcome['error']}."

    actions = json.dumps({"trades": [outcome], "rule_id": rule["id"]})
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, actions, kind, created_at) "
        "VALUES (?, ?, 'assistant', ?, ?, 'rule', ?)",
        (str(uuid.uuid4()), rule["user_id"], content, actions, now),
    )
    conn.commit()

    if outcome["status"] == "executed":
        logger.info(
            "Rule %s fired: %s %s %s @ %s",
            rule["id"], rule["side"], rule["quantity"], rule["ticker"], outcome["price"],
        )
        return "fired"
    logger.info("Rule %s fired but trade failed: %s", rule["id"], outcome["error"])
    return "trade_failed"


def process_rules_once(
    db_path: str, price_cache: PriceCache, commission_bps: float = 0.0
) -> dict[str, int]:
    """One scan pass over ALL users' active rules: fire triggered rules.

    Opens (and always closes) its own connection. Each rule is processed in
    its own transaction — one bad rule (or a DB lock error on it) is logged
    and does not stop the pass (same isolation discipline as the orders fill
    loop). Rules are processed oldest-first. Fired trades, snapshots, and the
    activation chat message land on each rule's own user (M4).

    Returns:
        Counts for observability/tests:
        {"fired": n, "trade_failed": n, "skipped": n}. Both "fired" and
        "trade_failed" consume the rule (status='fired'); "skipped" rules
        stay active (untriggered, no quote, or concurrently paused/deleted).
    """
    counts = {"fired": 0, "trade_failed": 0, "skipped": 0}
    conn = get_conn(db_path)
    try:
        active_rows = conn.execute(
            f"""
            SELECT {_RULE_SELECT_COLUMNS}
            FROM rules
            WHERE status = 'active'
            ORDER BY created_at ASC, rowid ASC
            """
        ).fetchall()
        for row in active_rows:
            try:
                result = _fire_rule_if_triggered(conn, price_cache, row, commission_bps)
            except Exception:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                logger.exception(
                    "Rules loop: error processing rule %s — continuing", row["id"]
                )
                continue
            counts[result] += 1
    finally:
        conn.close()
    return counts


async def rules_eval_loop(
    price_cache: PriceCache,
    db_path: str,
    interval: float = 1.0,
    commission_bps: float = 0.0,
) -> None:
    """Background task: evaluate active rules every ``interval`` seconds.

    Runs indefinitely until cancelled via ``asyncio.CancelledError``. Any other
    exception (bad rule, DB lock, etc.) is logged and the loop continues.
    """
    while True:
        try:
            process_rules_once(db_path, price_cache, commission_bps)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Rules eval loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_rules_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the rules APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/rules", tags=["rules"])

    @router.get("")
    async def list_rules(request: Request, status: str | None = None) -> dict:
        """List rules, newest first (created_at DESC, rowid DESC tie-break).

        Query params:
            status: 'active' | 'paused' | 'fired' | 'all' (default 'all').
                Invalid values return HTTP 400.
        """
        status_value = (status if status is not None else "all").lower()
        if status_value != "all" and status_value not in RULE_STATUSES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "status must be one of 'active', 'paused', 'fired', 'all'"
                },
            )

        user_id = get_current_user_id(request, db_path)
        query = f"""
            SELECT {_RULE_SELECT_COLUMNS}
            FROM rules
            WHERE user_id = ?
        """
        params: list = [user_id]
        if status_value != "all":
            query += " AND status = ?"
            params.append(status_value)
        query += " ORDER BY created_at DESC, rowid DESC"

        conn = get_conn(db_path)
        try:
            rows = conn.execute(query, params).fetchall()
            return {"rules": [_rule_row_to_dict(row) for row in rows]}
        finally:
            conn.close()

    @router.post("")
    async def create_rule(body: CreateRuleRequest, request: Request) -> dict:
        """Create a standing rule (status 'active').

        Thin HTTP wrapper over ``create_rule_on_conn``. Validation failures
        return HTTP 400 with ``{"error": "message"}`` and store nothing. When
        ``description`` is omitted a summary like "Buy 5 NVDA when day change
        <= -3%" is generated.
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            result = create_rule_on_conn(
                conn,
                price_cache,
                ticker=body.ticker,
                trigger_type=body.trigger_type,
                threshold=body.threshold,
                side=body.side,
                quantity=body.quantity,
                description=body.description,
                user_id=user_id,
            )
            if result["status"] == "failed":
                conn.rollback()
                return JSONResponse(status_code=400, content={"error": result["error"]})
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception(
                "Unexpected error creating rule %s %s %s (%s %s)",
                body.side, body.quantity, body.ticker, body.trigger_type, body.threshold,
            )
            raise
        finally:
            conn.close()

        return {"rule": result["rule"]}

    @router.patch("/{rule_id}")
    async def update_rule(rule_id: str, body: UpdateRuleRequest, request: Request) -> dict:
        """Set a rule's status to 'active' (re-arm a fired/paused rule) or 'paused'.

        Unknown ids (including another user's rules) return HTTP 404;
        statuses other than 'active'/'paused' return HTTP 400 ('fired' is set
        only by the evaluator).
        """
        new_status = body.status.lower()
        if new_status not in PATCHABLE_STATUSES:
            return JSONResponse(
                status_code=400, content={"error": "status must be 'active' or 'paused'"}
            )

        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT {_RULE_SELECT_COLUMNS} FROM rules "
                "WHERE id = ? AND user_id = ?",
                (rule_id, user_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return JSONResponse(status_code=404, content={"error": "Rule not found"})

            conn.execute(
                "UPDATE rules SET status = ? WHERE id = ?", (new_status, rule_id)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        result = _rule_row_to_dict(row)
        result["status"] = new_status
        return {"rule": result}

    @router.delete("/{rule_id}")
    async def delete_rule(rule_id: str, request: Request) -> dict:
        """Hard-delete a rule. Returns the deleted rule; unknown ids return 404."""
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT {_RULE_SELECT_COLUMNS} FROM rules "
                "WHERE id = ? AND user_id = ?",
                (rule_id, user_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return JSONResponse(status_code=404, content={"error": "Rule not found"})

            conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return {"rule": _rule_row_to_dict(row)}

    return router
