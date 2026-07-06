"""Simulated trading-session clock (M3.1).

A ``SessionClock`` models an accelerated open/close market cycle:

    OPEN --(open_seconds elapse)--> CLOSED --(break_seconds elapse)--> OPEN ...

The clock starts OPEN at app startup with ``session_id`` 1; the id increments
each time a new session opens. State does not advance by itself — a background
asyncio task (``session_clock_loop``, wired in main.py's lifespan) calls
``tick()`` at a ~1s cadence and runs the settlement hooks on each transition.

24/7 mode: constructing the clock without durations (or with any duration
<= 0) makes it *always open* — ``tick()`` never transitions, ``is_open`` is
always True, and ``next_transition_at`` is None. This is the default for
tests, for real market data (MASSIVE_API_KEY), and for invalid env config.

Time is injectable (``now`` callable) so transitions are unit-testable
without sleeping. All reads are thread-safe (the trade path and background
loops may touch the clock from different threads).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from threading import Lock

logger = logging.getLogger(__name__)

STATE_OPEN = "open"
STATE_CLOSED = "closed"


class SessionClock:
    """Thread-safe open/closed session state machine with injectable time.

    Args:
        open_seconds: Length of the OPEN phase. None or <= 0 => 24/7 mode.
        break_seconds: Length of the CLOSED phase. None or <= 0 => 24/7 mode.
        now: Time source returning Unix seconds (injectable for tests).
    """

    def __init__(
        self,
        open_seconds: float | None = None,
        break_seconds: float | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._now = now
        always_open = (
            open_seconds is None
            or break_seconds is None
            or open_seconds <= 0
            or break_seconds <= 0
        )
        self._open_seconds: float | None = None if always_open else float(open_seconds)
        self._break_seconds: float | None = None if always_open else float(break_seconds)
        self._lock = Lock()
        self._state = STATE_OPEN
        self._session_id = 1
        self._state_since = self._now()

    # --- Read API (thread-safe) ---

    @property
    def always_open(self) -> bool:
        """True when the clock runs in 24/7 mode (never transitions)."""
        return self._open_seconds is None

    @property
    def is_open(self) -> bool:
        """True when the market is open (always True in 24/7 mode)."""
        with self._lock:
            return self._state == STATE_OPEN

    @property
    def state(self) -> str:
        """Current state: 'open' or 'closed'."""
        with self._lock:
            return self._state

    @property
    def session_id(self) -> int:
        """Current session number, starting at 1; increments on each reopen."""
        with self._lock:
            return self._session_id

    @property
    def state_since(self) -> float:
        """Unix timestamp the current state was entered at."""
        with self._lock:
            return self._state_since

    def next_transition_at(self) -> float | None:
        """Unix timestamp of the next scheduled transition (None in 24/7 mode)."""
        with self._lock:
            return self._next_transition_at_locked()

    def snapshot(self) -> dict:
        """One consistent view of the clock — the /api/market/session payload.

        Returns:
            {"state": "open"|"closed", "session_id": int, "state_since": float,
             "next_transition_at": float|None, "now": float}
        """
        with self._lock:
            return {
                "state": self._state,
                "session_id": self._session_id,
                "state_since": self._state_since,
                "next_transition_at": self._next_transition_at_locked(),
                "now": self._now(),
            }

    # --- Transition API (driven by the background loop) ---

    def tick(self) -> list[str]:
        """Advance through every transition whose deadline has passed.

        Returns the emitted transition events in order — 'close' and/or
        'open' — so the caller can run settlement hooks. Normally at most one
        event per call (1s cadence vs. multi-second phases), but a delayed
        loop or a large injected-time jump catches up deterministically:
        ``state_since`` is stamped with the *scheduled* boundary, not the
        observed time, so the cycle never drifts. Always [] in 24/7 mode.
        """
        events: list[str] = []
        with self._lock:
            if self.always_open:
                return events
            now = self._now()
            deadline = self._next_transition_at_locked()
            while deadline is not None and now >= deadline:
                if self._state == STATE_OPEN:
                    self._state = STATE_CLOSED
                    events.append("close")
                else:
                    self._state = STATE_OPEN
                    self._session_id += 1
                    events.append("open")
                self._state_since = deadline
                deadline = self._next_transition_at_locked()
        return events

    # --- Internals ---

    def _next_transition_at_locked(self) -> float | None:
        """Next transition deadline. Must be called with self._lock held."""
        if self._open_seconds is None or self._break_seconds is None:
            return None
        duration = (
            self._open_seconds if self._state == STATE_OPEN else self._break_seconds
        )
        return self._state_since + duration


async def session_clock_loop(
    clock: SessionClock,
    *,
    on_close: Callable[[], None] | None = None,
    on_open: Callable[[], None] | None = None,
    interval: float = 1.0,
) -> None:
    """Background task: drive session transitions every ``interval`` seconds.

    Calls ``clock.tick()`` and runs the matching hook for each emitted event:
    ``on_close`` at session close (settlement) and ``on_open`` at reopen
    (day-state roll). Hooks are synchronous and must be short (they run on
    the event loop, like the snapshot loop's DB work).

    Runs indefinitely until cancelled via ``asyncio.CancelledError`` (clean
    cancellation). Hook/tick errors are logged and the loop continues.
    """
    while True:
        try:
            for event in clock.tick():
                logger.info(
                    "Session clock: market %s (session %d)", event, clock.session_id
                )
                hook = on_close if event == "close" else on_open
                if hook is not None:
                    hook()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Session clock loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)
