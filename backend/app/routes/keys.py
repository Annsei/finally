"""API key management routes for FinAlly (P3 §6) — cookie identity ONLY.

Endpoints (all under /api/keys):
- POST   ``''``                → 201 {"key": "<plaintext, shown exactly once>",
  "info": {...}} — mint a key (≤10 per user, 400 over the limit).
- GET    ``''``                → {"keys": [info, ...]} (never hashes/plaintext).
- PATCH  ``/{key_id}``         → info — label / frozen / constraint edits;
  an EXPLICIT null clears a constraint (absent field = unchanged).
- DELETE ``/{key_id}``         → {"status": "ok"} — audit rows are kept.
- GET    ``/{key_id}/audit``   → {"entries": [...], "has_more": bool} —
  newest-first ledger page (limit default 50 clamped 1..200, ``before`` is a
  created_at cursor).

Privilege boundary (core invariant): these routes only accept the cookie
session — Bearer calls get 403 "Keys cannot manage keys" so a leaked key can
never unfreeze itself, lift its own limits, mint new keys, or read its
ledger. The gateway middleware rejects valid Bearer calls before routing;
the check here keeps the boundary even in apps that mount this router
without the middleware. Cross-user access is always 404 (existence is not
revealed). Guest ('default') may create keys — single-user mode works as-is.

Factory ``create_keys_router(db_path)`` mirrors the other routers.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api_gateway import generate_api_key, utc_now_iso
from app.auth import get_current_user_id
from app.db.connection import get_conn

logger = logging.getLogger(__name__)

MAX_KEYS_PER_USER = 10
LABEL_MIN_LEN = 1
LABEL_MAX_LEN = 40
AUDIT_DEFAULT_LIMIT = 50
AUDIT_MAX_LIMIT = 200

# Sentinel distinguishing "field absent" from "explicit null" in PATCH bodies.
_ABSENT = object()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _bearer_rejection(request: Request) -> JSONResponse | None:
    """403 for any Bearer-authenticated call (keys cannot manage keys).

    Belt and braces: the gateway middleware already rejects valid Bearer
    calls to /api/keys*, and raw-header Bearer requests that somehow bypass
    it (e.g. a router mounted without the middleware) are rejected here too.
    """
    if getattr(request.state, "api_key_id", None) is not None:
        return _error(403, "Keys cannot manage keys")
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        return _error(403, "Keys cannot manage keys")
    return None


def _key_info(row) -> dict:
    """Public shape of an api_keys row — never includes key_hash."""
    allowed_raw = row["allowed_tickers"]
    return {
        "id": row["id"],
        "label": row["label"],
        "prefix": row["prefix"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "frozen": bool(row["frozen"]),
        "allowed_tickers": json.loads(allowed_raw) if allowed_raw else None,
        "max_order_qty": row["max_order_qty"],
        "daily_trade_cap": row["daily_trade_cap"],
    }


def _validate_label(value: Any) -> tuple[str | None, str | None]:
    """Return (normalized_label, error)."""
    if not isinstance(value, str):
        return None, "Label must be 1-40 characters"
    label = value.strip()
    if not (LABEL_MIN_LEN <= len(label) <= LABEL_MAX_LEN):
        return None, "Label must be 1-40 characters"
    return label, None


def _validate_allowed_tickers(value: Any) -> tuple[str | None, str | None]:
    """Return (json_text_or_None, error). None (or []) means unrestricted."""
    if value is None:
        return None, None
    if not isinstance(value, list):
        return None, "allowed_tickers must be a list of ticker symbols"
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None, "allowed_tickers must be a list of ticker symbols"
        normalized.append(item.strip().upper())
    if not normalized:
        # An empty list places no restriction (§4 only applies to non-empty
        # lists) — store NULL so the semantics are explicit.
        return None, None
    return json.dumps(normalized, separators=(",", ":")), None


def _validate_max_order_qty(value: Any) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None, "max_order_qty must be a positive number"
    return float(value), None


def _validate_daily_trade_cap(value: Any) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None, "daily_trade_cap must be a positive integer"
    return value, None


async def _read_json_object(request: Request) -> dict | None:
    """Parse the request body as a JSON object; None if it isn't one."""
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def create_keys_router(db_path: str) -> APIRouter:
    """Factory: build the /api/keys APIRouter with the injected database path."""
    router = APIRouter(prefix="/api/keys", tags=["keys"])

    def _load_own_key(conn, key_id: str, user_id: str):
        """Fetch a key scoped to its owner — cross-user rows resolve to None
        so foreign ids are indistinguishable from unknown ones (404)."""
        return conn.execute(
            "SELECT * FROM api_keys WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        ).fetchone()

    @router.post("")
    async def create_key(request: Request) -> JSONResponse:
        """Mint an API key. The plaintext appears ONLY in this response."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection
        user_id = get_current_user_id(request, db_path)

        payload = await _read_json_object(request)
        if payload is None:
            return _error(400, "Invalid JSON body")

        label, err = _validate_label(payload.get("label"))
        if err is not None:
            return _error(400, err)
        allowed_json, err = _validate_allowed_tickers(payload.get("allowed_tickers"))
        if err is not None:
            return _error(400, err)
        max_qty, err = _validate_max_order_qty(payload.get("max_order_qty"))
        if err is not None:
            return _error(400, err)
        cap, err = _validate_daily_trade_cap(payload.get("daily_trade_cap"))
        if err is not None:
            return _error(400, err)

        plaintext, key_hash, prefix = generate_api_key()
        key_id = str(uuid.uuid4())
        now = utc_now_iso()

        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute(
                "SELECT COUNT(*) FROM api_keys WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            if count >= MAX_KEYS_PER_USER:
                conn.rollback()
                return _error(400, f"API key limit reached ({MAX_KEYS_PER_USER} per user)")
            conn.execute(
                "INSERT INTO api_keys (id, user_id, label, key_hash, prefix, "
                "created_at, last_used_at, frozen, allowed_tickers, "
                "max_order_qty, daily_trade_cap) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?)",
                (key_id, user_id, label, key_hash, prefix, now, allowed_json, max_qty, cap),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # Log only non-secret identifiers — never the plaintext or its hash.
        logger.info("API key %s (%s) created for user %r", key_id, prefix, user_id)
        return JSONResponse(
            status_code=201, content={"key": plaintext, "info": _key_info(row)}
        )

    @router.get("")
    async def list_keys(request: Request):
        """List the caller's keys, newest first. No hashes, no plaintext."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id = ? "
                "ORDER BY created_at DESC, rowid DESC",
                (user_id,),
            ).fetchall()
        finally:
            conn.close()
        return {"keys": [_key_info(row) for row in rows]}

    @router.patch("/{key_id}")
    async def update_key(key_id: str, request: Request):
        """Edit label / frozen / constraints. Explicit null clears a constraint."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection
        user_id = get_current_user_id(request, db_path)

        payload = await _read_json_object(request)
        if payload is None:
            return _error(400, "Invalid JSON body")

        updates: dict[str, Any] = {}

        if "label" in payload:
            label, err = _validate_label(payload["label"])
            if err is not None:
                return _error(400, err)
            updates["label"] = label
        if "frozen" in payload:
            if not isinstance(payload["frozen"], bool):
                return _error(400, "frozen must be a boolean")
            updates["frozen"] = int(payload["frozen"])
        if "allowed_tickers" in payload:
            allowed_json, err = _validate_allowed_tickers(payload["allowed_tickers"])
            if err is not None:
                return _error(400, err)
            updates["allowed_tickers"] = allowed_json
        if "max_order_qty" in payload:
            max_qty, err = _validate_max_order_qty(payload["max_order_qty"])
            if err is not None:
                return _error(400, err)
            updates["max_order_qty"] = max_qty
        if "daily_trade_cap" in payload:
            cap, err = _validate_daily_trade_cap(payload["daily_trade_cap"])
            if err is not None:
                return _error(400, err)
            updates["daily_trade_cap"] = cap

        conn = get_conn(db_path)
        try:
            row = _load_own_key(conn, key_id, user_id)
            if row is None:
                return _error(404, "Key not found")
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE api_keys SET {assignments} WHERE id = ?",  # noqa: S608
                    (*updates.values(), key_id),
                )
                conn.commit()
                row = _load_own_key(conn, key_id, user_id)
        finally:
            conn.close()
        return _key_info(row)

    @router.delete("/{key_id}")
    async def delete_key(key_id: str, request: Request):
        """Revoke a key immediately. Its audit rows are kept (append-only)."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            deleted = conn.execute(
                "DELETE FROM api_keys WHERE id = ? AND user_id = ?",
                (key_id, user_id),
            ).rowcount
            conn.commit()
        finally:
            conn.close()
        if deleted == 0:
            return _error(404, "Key not found")
        logger.info("API key %s revoked by user %r", key_id, user_id)
        return {"status": "ok"}

    @router.get("/{key_id}/audit")
    async def key_audit(
        key_id: str,
        request: Request,
        limit: str | None = None,
        before: str | None = None,
    ):
        """Page through a key's audit ledger, newest first (own keys only)."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection
        user_id = get_current_user_id(request, db_path)

        if limit is None:
            limit_value = AUDIT_DEFAULT_LIMIT
        else:
            try:
                limit_value = int(limit)
            except ValueError:
                return _error(400, "limit must be an integer")
        limit_value = max(1, min(AUDIT_MAX_LIMIT, limit_value))

        conn = get_conn(db_path)
        try:
            if _load_own_key(conn, key_id, user_id) is None:
                return _error(404, "Key not found")
            query = (
                "SELECT id, method, endpoint, payload_digest, result, "
                "status_code, created_at FROM api_audit WHERE key_id = ?"
            )
            params: list[Any] = [key_id]
            if before is not None:
                query += " AND created_at < ?"
                params.append(before)
            query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
            params.append(limit_value + 1)
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        has_more = len(rows) > limit_value
        entries = [
            {
                "id": row["id"],
                "method": row["method"],
                "endpoint": row["endpoint"],
                "payload_digest": row["payload_digest"],
                "result": row["result"],
                "status_code": row["status_code"],
                "created_at": row["created_at"],
            }
            for row in rows[:limit_value]
        ]
        return {"entries": entries, "has_more": has_more}

    return router
