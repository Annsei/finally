"""Central runtime and security settings for the FinAlly backend.

The application deliberately supports two modes:

``local-demo``
    Single-process development on a loopback interface. Name-only login stays
    available for the existing demo workflow.

``classroom-server``
    A shared, single-replica SQLite deployment. Startup requires explicit
    login/admin secrets and acknowledgement of the single-replica constraint.

This is not a production/multi-replica configuration system. Its purpose is
to make the currently supported boundary explicit and fail closed when a
shared deployment is missing the minimum security controls.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

LOCAL_DEMO = "local-demo"
CLASSROOM_SERVER = "classroom-server"
SUPPORTED_RUNTIME_MODES = frozenset({LOCAL_DEMO, CLASSROOM_SERVER})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class RuntimeSettings:
    """Validated runtime settings injected into security-sensitive routers."""

    mode: str = LOCAL_DEMO
    bind_host: str = "127.0.0.1"
    server_auth_secret: str | None = None
    admin_token: str | None = "local-demo-admin"
    single_replica: bool = True
    max_bearer_body_bytes: int = 65_536
    quote_max_age_seconds: float = 45.0

    @property
    def is_server(self) -> bool:
        return self.mode == CLASSROOM_SERVER

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        mode = os.getenv("FINALLY_RUNTIME_MODE", LOCAL_DEMO).strip().lower()
        default_host = "127.0.0.1" if mode == LOCAL_DEMO else "0.0.0.0"
        admin_token = os.getenv("FINALLY_ADMIN_TOKEN", "").strip() or (
            "local-demo-admin" if mode == LOCAL_DEMO else None
        )
        single_replica_raw = os.getenv("FINALLY_SINGLE_REPLICA", "").strip().lower()
        return cls(
            mode=mode,
            bind_host=os.getenv("FINALLY_HOST", default_host).strip() or default_host,
            server_auth_secret=os.getenv("FINALLY_SERVER_AUTH_SECRET", "").strip() or None,
            admin_token=admin_token,
            single_replica=(
                mode == LOCAL_DEMO
                if not single_replica_raw
                else single_replica_raw in {"1", "true", "yes"}
            ),
            max_bearer_body_bytes=_positive_int_env(
                "FINALLY_MAX_BEARER_BODY_BYTES", 65_536
            ),
            quote_max_age_seconds=_positive_float_env(
                "FINALLY_QUOTE_MAX_AGE_SECONDS", 45.0
            ),
        )

    def validate(self, *, db_path: str | None = None) -> RuntimeSettings:
        if self.mode not in SUPPORTED_RUNTIME_MODES:
            allowed = ", ".join(sorted(SUPPORTED_RUNTIME_MODES))
            raise ValueError(f"FINALLY_RUNTIME_MODE must be one of: {allowed}")
        if self.mode == LOCAL_DEMO and self.bind_host not in LOOPBACK_HOSTS:
            raise ValueError(
                "local-demo may only bind to loopback; use "
                "FINALLY_RUNTIME_MODE=classroom-server for shared access"
            )
        if self.max_bearer_body_bytes <= 0:
            raise ValueError("FINALLY_MAX_BEARER_BODY_BYTES must be positive")
        if not math.isfinite(self.quote_max_age_seconds) or self.quote_max_age_seconds <= 0:
            raise ValueError("FINALLY_QUOTE_MAX_AGE_SECONDS must be positive and finite")
        if self.is_server:
            if not self.server_auth_secret or len(self.server_auth_secret) < 16:
                raise ValueError(
                    "classroom-server requires FINALLY_SERVER_AUTH_SECRET "
                    "with at least 16 characters"
                )
            if not self.admin_token or len(self.admin_token) < 16:
                raise ValueError(
                    "classroom-server requires FINALLY_ADMIN_TOKEN "
                    "with at least 16 characters"
                )
            if not self.single_replica:
                raise ValueError(
                    "classroom-server requires FINALLY_SINGLE_REPLICA=true; "
                    "multi-replica in-memory market state is not supported"
                )
            if db_path == ":memory:":
                raise ValueError("classroom-server requires a persistent SQLite DB_PATH")
        return self

    def effective_config(self) -> dict:
        """Non-secret configuration safe to log at startup."""
        return {
            "mode": self.mode,
            "bind_host": self.bind_host,
            "single_replica": self.single_replica,
            "max_bearer_body_bytes": self.max_bearer_body_bytes,
            "quote_max_age_seconds": self.quote_max_age_seconds,
            "server_auth_configured": bool(self.server_auth_secret),
            "admin_token_configured": bool(self.admin_token),
        }
