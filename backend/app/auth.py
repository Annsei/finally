"""Lightweight cookie identity for FinAlly (M4.1) — stdlib only.

Session model: the ``finally_session`` cookie carries
``{user_id}.{hmac_sha256_hex(secret, user_id)}`` where the secret is generated
once at first boot and stored in ``app_meta['session_secret']`` (see
``app.db.connection._ensure_arena_state``). No passwords — it's a sim; the
signature only prevents trivially forging someone else's id.

CRITICAL COMPATIBILITY RULE: anonymous requests (no cookie, malformed cookie,
or a bad signature) resolve to ``user_id='default'`` (display name "Guest"),
which is exactly the pre-M4 single-user behavior — every route that scopes by
``get_current_user_id`` behaves identically to the old hardcoded ``'default'``
until a client actually logs in.

``get_current_user_id(request, db_path)`` is the shared dependency used by all
user-facing routes. Routers pass their closed-over ``db_path`` (the same
factory-injection pattern the rest of the app uses); when omitted it falls
back to the ``DB_PATH`` environment variable like ``main.py`` does.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

from fastapi import Request

from app.db.connection import get_conn

logger = logging.getLogger(__name__)

# Cookie contract (fixed — frontend built in parallel).
COOKIE_NAME = "finally_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days

# Session secrets cached per db_path so request handling never re-reads the
# DB after the first resolution (one process, one secret per database file).
_secret_cache: dict[str, str] = {}


def _default_db_path() -> str:
    """Mirror main.py's DB path resolution (env var with the same fallback)."""
    return os.getenv("DB_PATH", "db/finally.db")


def get_session_secret(db_path: str | None = None) -> str:
    """Return the app's HMAC session secret, creating it if absent.

    ``init_db`` normally seeds ``app_meta['session_secret']`` at startup; the
    lazy INSERT here is a belt-and-braces fallback so a database that somehow
    missed it (or a bare test DB) still gets a stable secret.
    """
    path = db_path or _default_db_path()
    cached = _secret_cache.get(path)
    if cached is not None:
        return cached

    conn = get_conn(path)
    try:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'session_secret'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT OR IGNORE INTO app_meta (key, value) VALUES ('session_secret', ?)",
                (secrets.token_hex(32),),
            )
            conn.commit()
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'session_secret'"
            ).fetchone()
        secret: str = row["value"]
    finally:
        conn.close()

    _secret_cache[path] = secret
    return secret


def sign_user_id(user_id: str, db_path: str | None = None) -> str:
    """HMAC-SHA256 hex signature of ``user_id`` under the app session secret."""
    secret = get_session_secret(db_path)
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()


def session_cookie_value(user_id: str, db_path: str | None = None) -> str:
    """Build the ``finally_session`` cookie payload for ``user_id``."""
    return f"{user_id}.{sign_user_id(user_id, db_path)}"


def get_current_user_id(request: Request, db_path: str | None = None) -> str:
    """Resolve the requesting user from the ``finally_session`` cookie.

    Returns the signed user id when the cookie verifies; falls back to
    ``'default'`` (the anonymous Guest user) on a missing, malformed, or
    forged cookie — never raises.
    """
    # getattr keeps handler-level unit tests (stub request doubles without
    # .cookies) on the anonymous path — same fallback as a missing cookie.
    cookies = getattr(request, "cookies", None)
    raw = cookies.get(COOKIE_NAME) if cookies else None
    if not raw or "." not in raw:
        return "default"
    # User ids are lowercased [a-z0-9_-]+ (no dots), but rpartition keeps the
    # split unambiguous even for garbage input.
    user_id, _, signature = raw.rpartition(".")
    if not user_id or not signature:
        return "default"
    expected = sign_user_id(user_id, db_path)
    if not hmac.compare_digest(expected, signature):
        return "default"
    return user_id
