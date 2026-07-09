"""API-key gateway for FinAlly (P3 §2-§5) — Bearer auth, rate limit, guardrails, audit.

``ApiKeyGatewayMiddleware`` is a PURE ASGI middleware (deliberately NOT
Starlette's ``BaseHTTPMiddleware``, which wraps responses in a streaming
adapter and would break the SSE price stream). Its contract:

- Requests WITHOUT an ``Authorization: Bearer`` header are passed through
  untouched — same scope, same receive, same send — so the cookie/anonymous
  path (all existing UI and E2E traffic) is byte-identical to a stack without
  this middleware.
- Bearer requests are resolved by sha256 hash lookup in ``api_keys``:
  unknown → 401 (logged, not audited); frozen → 403 + audit ``denied``
  (the audit write is throttled per key, the 403 itself never is);
  valid → ``request.state.api_user_id`` / ``api_key_id`` / constraint fields
  are injected and the request proceeds through rate limiting (§3),
  authorization guardrails (§4) and the audit ledger (§5).
- Key management (``/api/keys*``) only accepts cookie identity: a valid
  Bearer call is rejected 403 so a leaked key can never unfreeze itself,
  raise its own limits, or mint new keys.
- Responses are never buffered: the audit path only observes the
  ``http.response.start`` status via a send wrapper; body frames stream
  through untouched (SSE-safe). Request bodies ARE buffered — but only for
  mutating Bearer requests that need a payload digest / guardrail check —
  and replayed to the downstream app with the standard read-then-replay
  pattern.

Key material: ``fk_`` + ``secrets.token_urlsafe(32)`` (46 chars). Only the
sha256 hex digest is ever stored or compared; the display prefix is the
first 11 characters (``fk_XXXXXXXX``). The plaintext appears exactly once —
in the POST /api/keys creation response — and never in logs or audit rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

# --- Key material (§2) -------------------------------------------------------

KEY_PREFIX_LEN = 11  # "fk_" + 8 chars of the secret — shown in the key list.

# --- Rate limiting (§3): token bucket per key, in-process ---------------------

RATE_CAPACITY = 10.0
RATE_REFILL_PER_SEC = 5.0
RATE_AUDIT_THROTTLE_SECONDS = 10.0  # at most one rate_limited audit row / key / 10s
FROZEN_AUDIT_THROTTLE_SECONDS = 10.0  # at most one frozen-denied audit row / key / 10s
LAST_USED_THROTTLE_SECONDS = 60.0  # at most one last_used_at write / key / 60s

# --- Audit ledger (§5) --------------------------------------------------------

MUTATING_METHODS = frozenset({"POST", "PATCH", "DELETE"})
AUDITED_PREFIXES = (
    "/api/portfolio",
    "/api/rules",
    "/api/watchlist",
    "/api/chat",
    "/api/strategies",
    "/api/backtest/runs",
    "/api/season/reset",
)
# Guardrails (§4) apply to the two order-placing endpoints only.
TRADE_ENDPOINT = "/api/portfolio/trade"
ORDERS_ENDPOINT = "/api/portfolio/orders"
GUARDED_ENDPOINTS = frozenset({TRADE_ENDPOINT, ORDERS_ENDPOINT})
DIGEST_MAX_CHARS = 200


def generate_api_key() -> tuple[str, str, str]:
    """Mint a new API key. Returns ``(plaintext, sha256_hex, display_prefix)``.

    The caller (POST /api/keys) returns the plaintext to the user exactly
    once and persists only the hash + prefix.
    """
    plaintext = "fk_" + secrets.token_urlsafe(32)
    return plaintext, hash_api_key(plaintext), plaintext[:KEY_PREFIX_LEN]


def hash_api_key(plaintext: str) -> str:
    """sha256 hex digest of the full plaintext key — the only stored form."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def payload_digest(body: bytes | None) -> str | None:
    """Compact-JSON digest of a request body, truncated to 200 chars (§1).

    Non-JSON or empty bodies yield None — the digest is a human-readable
    audit hint, not a checksum, and must never contain key material (the
    Authorization header is not part of the body).
    """
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    return compact[:DIGEST_MAX_CHARS]


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp (the app-wide created_at format)."""
    return datetime.now(timezone.utc).isoformat()


def utc_midnight_iso() -> str:
    """ISO-8601 timestamp of today's UTC midnight — the daily-cap boundary.

    All api_audit.created_at values are written by this module in the same
    ``+00:00`` ISO format, so a lexicographic >= comparison is a correct
    same-UTC-day filter.
    """
    return (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


def write_audit(
    db_path: str,
    *,
    key_id: str,
    user_id: str,
    method: str,
    endpoint: str,
    result: str,
    status_code: int | None,
    digest: str | None = None,
) -> None:
    """Insert one api_audit row (own connection, committed immediately)."""
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO api_audit (id, key_id, user_id, method, endpoint, "
            "payload_digest, result, status_code, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                key_id,
                user_id,
                method,
                endpoint,
                digest,
                result,
                status_code,
                utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def count_todays_ok_orders(db_path: str, key_id: str) -> int:
    """Count today's (UTC) result='ok' audit rows on the two order endpoints.

    This is the daily_trade_cap counter (§4.3): only completed, successful
    placements count, and the window resets at UTC midnight.
    """
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM api_audit WHERE key_id = ? AND result = 'ok' "
            "AND endpoint IN (?, ?) AND created_at >= ?",
            (key_id, TRADE_ENDPOINT, ORDERS_ENDPOINT, utc_midnight_iso()),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


async def _send_json(send: Send, status: int, payload: dict) -> None:
    """Emit a complete JSON response from the middleware (rejection paths)."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _buffer_body(receive: Receive) -> tuple[bytes, Receive]:
    """Drain the request body and return ``(body, replay_receive)``.

    The downstream app sees a single ``http.request`` message with the full
    body — the standard read-then-replay pattern for body-inspecting pure
    ASGI middleware. After replay, further calls fall through to the real
    ``receive`` (e.g. ``http.disconnect``).
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            # http.disconnect mid-body: stop reading; downstream gets what we have.
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    body = b"".join(chunks)
    replayed = False

    async def replay_receive() -> Message:
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay_receive


class ApiKeyGatewayMiddleware:
    """Pure ASGI gateway: Bearer auth + rate limit + guardrails + audit (P3).

    Register with ``app.add_middleware(ApiKeyGatewayMiddleware, db_path=...)``
    (FastAPI instantiates it as ``cls(asgi_app, **options)`` when the
    middleware stack is built, before any request is served). ``db_path=None``
    resolves the ``DB_PATH`` env var per request — the same source main.py's
    lifespan reads — so the module-level app needs no import-order guarantee.

    ``now`` is an injectable monotonic clock (seconds) used by the token
    bucket and the write throttles; tests pass a fake.
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        db_path: str | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self._db_path = db_path
        self._now = now
        # key_id -> (tokens, last_refill_monotonic)
        self._buckets: dict[str, tuple[float, float]] = {}
        # key_id -> monotonic of the last last_used_at DB write
        self._last_used_written: dict[str, float] = {}
        # key_id -> monotonic of the last rate_limited audit row
        self._rate_audit_written: dict[str, float] = {}
        # key_id -> monotonic of the last frozen-denied audit row
        self._frozen_audit_written: dict[str, float] = {}

    @property
    def db_path(self) -> str:
        """Injected path, or the DB_PATH env fallback main.py uses."""
        return self._db_path or os.getenv("DB_PATH", "db/finally.db")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = self._bearer_token(scope)
        if token is None:
            # CORE INVARIANT: no Authorization: Bearer header → 100% passthrough.
            await self.app(scope, receive, send)
            return
        await self._handle_bearer(scope, receive, send, token)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _bearer_token(scope: Scope) -> str | None:
        """Extract the Bearer token, or None for non-Bearer/absent headers."""
        for name, value in scope.get("headers", ()):
            if name == b"authorization":
                header = value.decode("latin-1")
                if header[:7].lower() == "bearer ":
                    return header[7:].strip()
                return None  # other auth schemes are not ours — pass through
        return None

    def _load_key(self, db_path: str, key_hash: str):
        """Fetch the api_keys row by hash. Per-request read — freezing a key
        (or revoking it) takes effect on the very next request.

        The lookup is an indexed equality query over sha256 digests: the
        plaintext never reaches SQL, and because the comparison happens on
        the hash (not char-by-char on secret material) there is no usable
        timing side channel.
        """
        conn = get_conn(db_path)
        try:
            return conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
        finally:
            conn.close()

    def _audit(self, db_path: str, **kwargs: Any) -> None:
        """Best-effort audit write — a ledger failure must not break requests."""
        try:
            write_audit(db_path, **kwargs)
        except Exception:
            logger.exception(
                "Failed to write api_audit row for %s %s",
                kwargs.get("method"),
                kwargs.get("endpoint"),
            )

    def _audit_window_clear(
        self, registry: dict[str, float], key_id: str, window: float
    ) -> bool:
        """Per-key audit-write throttle: True (and stamp the registry) when at
        least ``window`` seconds passed since this key's last recorded write.
        Throttles ONLY the ledger row — never the response itself."""
        now = self._now()
        last = registry.get(key_id)
        if last is not None and now - last < window:
            return False
        registry[key_id] = now
        return True

    def _take_token(self, key_id: str) -> bool:
        """Token bucket (§3): capacity 10, refill 5/s, per key, in-process."""
        now = self._now()
        tokens, last_refill = self._buckets.get(key_id, (RATE_CAPACITY, now))
        tokens = min(RATE_CAPACITY, tokens + (now - last_refill) * RATE_REFILL_PER_SEC)
        if tokens >= 1.0:
            self._buckets[key_id] = (tokens - 1.0, now)
            return True
        self._buckets[key_id] = (tokens, now)
        return False

    def _touch_last_used(self, db_path: str, key_id: str) -> None:
        """Write last_used_at, throttled to one write per key per 60s (§2)."""
        now = self._now()
        last = self._last_used_written.get(key_id)
        if last is not None and now - last < LAST_USED_THROTTLE_SECONDS:
            return
        self._last_used_written[key_id] = now
        try:
            conn = get_conn(db_path)
            try:
                conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (utc_now_iso(), key_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.exception("Failed to update last_used_at for key %s", key_id)

    def _check_guardrails(self, db_path: str, key_row, body: bytes) -> str | None:
        """Apply the §4 guardrails; returns the denial message or None.

        An unparseable / non-object body passes through — the route itself
        rejects it with 400/422, which the ledger records as 'error'.
        """
        try:
            parsed = json.loads(body) if body else None
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None

        # 1. allowed_tickers — uppercase-normalized membership check.
        allowed_raw = key_row["allowed_tickers"]
        if allowed_raw:
            try:
                allowed = json.loads(allowed_raw)
            except ValueError:
                allowed = None
            if allowed:
                ticker = str(parsed.get("ticker") or "").strip().upper()
                if ticker not in {str(t).strip().upper() for t in allowed}:
                    return "Ticker not allowed for this key"

        # 2. max_order_qty — compare anything float() accepts, including
        #    numeric STRINGS: pydantic lax mode coerces "999" to 999.0 at the
        #    route, so skipping strings here would be a guardrail bypass.
        #    Values float() rejects can't execute anyway (the route 422s).
        max_qty = key_row["max_order_qty"]
        if max_qty is not None:
            quantity = parsed.get("quantity")
            try:
                quantity_f = None if isinstance(quantity, bool) else float(quantity)
            except (TypeError, ValueError):
                quantity_f = None
            if quantity_f is not None and quantity_f > float(max_qty):
                return "Quantity exceeds key limit"

        # 3. daily_trade_cap — today's (UTC) successful placements on the two
        #    order endpoints, counted from the audit ledger.
        cap = key_row["daily_trade_cap"]
        if cap is not None and count_todays_ok_orders(db_path, key_row["id"]) >= cap:
            return "Daily trade cap reached"

        return None

    # ------------------------------------------------------------- bearer path

    async def _handle_bearer(
        self, scope: Scope, receive: Receive, send: Send, token: str
    ) -> None:
        db_path = self.db_path
        method = str(scope.get("method", "GET")).upper()
        path = scope.get("path", "")

        key_row = self._load_key(db_path, hash_api_key(token))
        if key_row is None:
            # §2: unknown key — log only (the prefix is the same non-secret
            # fragment the key list displays; never log the token or a hash).
            logger.warning(
                "Rejected unknown API key (prefix %r) on %s %s",
                token[:KEY_PREFIX_LEN],
                method,
                path,
            )
            await _send_json(send, 401, {"error": "Invalid API key"})
            return

        key_id: str = key_row["id"]
        user_id: str = key_row["user_id"]

        # §2/§4: frozen is the kill switch — immediate 403, audited. The audit
        # write is throttled per key (mirrors the rate_limited throttle) so a
        # bot hammering a frozen key can't flood the ledger with identical
        # rows; every request still gets its 403.
        if key_row["frozen"]:
            if self._audit_window_clear(
                self._frozen_audit_written, key_id, FROZEN_AUDIT_THROTTLE_SECONDS
            ):
                self._audit(
                    db_path,
                    key_id=key_id,
                    user_id=user_id,
                    method=method,
                    endpoint=path,
                    result="denied",
                    status_code=403,
                )
            await _send_json(send, 403, {"error": "API key is frozen"})
            return

        # §6: keys cannot manage keys — the management surface is cookie-only.
        if path == "/api/keys" or path.startswith("/api/keys/"):
            await _send_json(send, 403, {"error": "Keys cannot manage keys"})
            return

        self._touch_last_used(db_path, key_id)

        # §3: rate limit every Bearer request (GETs included).
        if not self._take_token(key_id):
            if self._audit_window_clear(
                self._rate_audit_written, key_id, RATE_AUDIT_THROTTLE_SECONDS
            ):
                self._audit(
                    db_path,
                    key_id=key_id,
                    user_id=user_id,
                    method=method,
                    endpoint=path,
                    result="rate_limited",
                    status_code=429,
                )
            await _send_json(send, 429, {"error": "Rate limited"})
            return

        # §5: only mutating requests on the audited surface are logged (GETs
        # are rate-limited above but never audited). Their body is buffered
        # once for the digest (and §4 guardrails) then replayed downstream.
        audited = method in MUTATING_METHODS and path.startswith(AUDITED_PREFIXES)
        body: bytes = b""
        if audited:
            body, receive = await _buffer_body(receive)

        # §4: authorization guardrails on the two order-placing endpoints.
        if method == "POST" and path in GUARDED_ENDPOINTS:
            denial = self._check_guardrails(db_path, key_row, body)
            if denial is not None:
                self._audit(
                    db_path,
                    key_id=key_id,
                    user_id=user_id,
                    method=method,
                    endpoint=path,
                    result="denied",
                    status_code=403,
                    digest=payload_digest(body),
                )
                await _send_json(send, 403, {"error": denial})
                return

        # §2: inject the resolved identity + constraint fields for downstream
        # routes (get_current_user_id prefers api_user_id over any cookie).
        # Deliberately NOT the raw row: key_hash must never leave this module.
        state = scope.setdefault("state", {})
        state["api_user_id"] = user_id
        state["api_key_id"] = key_id
        state["api_key_constraints"] = {
            "label": key_row["label"],
            "prefix": key_row["prefix"],
            "allowed_tickers": key_row["allowed_tickers"],
            "max_order_qty": key_row["max_order_qty"],
            "daily_trade_cap": key_row["daily_trade_cap"],
        }

        if not audited:
            # No response inspection needed — hand over untouched (SSE-safe).
            await self.app(scope, receive, send)
            return

        # Audited path: observe the response status only. Body frames are
        # forwarded as-is — nothing is buffered or rewritten.
        status_holder: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            self._audit(
                db_path,
                key_id=key_id,
                user_id=user_id,
                method=method,
                endpoint=path,
                result="error",
                status_code=status_holder.get("status", 500),
                digest=payload_digest(body),
            )
            raise
        status = status_holder.get("status", 500)
        result = "ok" if 200 <= status < 300 else "error"
        self._audit(
            db_path,
            key_id=key_id,
            user_id=user_id,
            method=method,
            endpoint=path,
            result=result,
            status_code=status,
            digest=payload_digest(body),
        )
