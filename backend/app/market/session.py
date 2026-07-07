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
# Fine-grained phases for the CN midday-break cycle (CN-2 §5).
PHASE_AM = "am"
PHASE_MIDDAY = "midday"
PHASE_PM = "pm"
# Phases in which the market accepts equity market orders. ``is_open`` and the
# coarse ``state`` derive from membership here.
_OPEN_PHASES = frozenset({STATE_OPEN, PHASE_AM, PHASE_PM})


class SessionClock:
    """Thread-safe trading-session state machine with injectable time.

    Two shapes, selected by ``midday_break_seconds``:

    - Two-phase (default): OPEN --open_seconds--> CLOSED --break_seconds--> OPEN.
      ``phase`` equals the coarse ``state`` ('open'|'closed'); the CN-2 midday
      machinery is fully dormant, so behavior is byte-for-byte the pre-CN-2
      clock.
    - Four-phase (``midday_break_seconds > 0`` and not 24/7): one trading day is
      AM (open_seconds/2) -> MIDDAY (midday_break_seconds) -> PM (open_seconds/2)
      -> CLOSED (break_seconds) -> AM (next day). The midday break is a *pause*:
      it emits no settlement events and does not roll prev_close or unlock T+1.

    Args:
        open_seconds: Total open length per day. None or <= 0 => 24/7 mode.
        break_seconds: Length of the CLOSED phase. None or <= 0 => 24/7 mode.
        now: Time source returning Unix seconds (injectable for tests).
        midday_break_seconds: Lunch-break length (CN-2 §5). <= 0 (default) keeps
            the two-phase cycle unchanged; > 0 (only when not 24/7) enables the
            four-phase day.
    """

    def __init__(
        self,
        open_seconds: float | None = None,
        break_seconds: float | None = None,
        now: Callable[[], float] = time.time,
        midday_break_seconds: float = 0.0,
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
        self._midday_break_seconds = (
            float(midday_break_seconds) if midday_break_seconds and midday_break_seconds > 0 else 0.0
        )
        # Four-phase mode requires both a real session cycle and a midday break.
        self._midday_enabled = (not always_open) and self._midday_break_seconds > 0
        self._lock = Lock()
        self._state = PHASE_AM if self._midday_enabled else STATE_OPEN
        self._session_id = 1
        self._state_since = self._now()

    # --- Read API (thread-safe) ---

    @property
    def always_open(self) -> bool:
        """True when the clock runs in 24/7 mode (never transitions)."""
        return self._open_seconds is None

    @property
    def is_open(self) -> bool:
        """True when equity market orders are accepted (always True in 24/7 mode)."""
        with self._lock:
            return self._state in _OPEN_PHASES

    @property
    def state(self) -> str:
        """Coarse state: 'open' (open/am/pm) or 'closed' (closed/midday)."""
        with self._lock:
            return STATE_OPEN if self._state in _OPEN_PHASES else STATE_CLOSED

    @property
    def phase(self) -> str:
        """Fine-grained phase (CN-2 §5).

        'open' (24/7 or two-phase open) | 'am' | 'midday' | 'pm' | 'closed'.
        """
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
             "next_transition_at": float|None, "now": float} — plus a ``phase``
            key ONLY in four-phase mode (CN midday), so the two-phase/us payload
            shape is unchanged (existing exact-shape tests stay green).
        """
        with self._lock:
            snap = {
                "state": STATE_OPEN if self._state in _OPEN_PHASES else STATE_CLOSED,
                "session_id": self._session_id,
                "state_since": self._state_since,
                "next_transition_at": self._next_transition_at_locked(),
                "now": self._now(),
            }
            if self._midday_enabled:
                snap["phase"] = self._state
            return snap

    # --- Transition API (driven by the background loop) ---

    def tick(self) -> list[str]:
        """Advance through every transition whose deadline has passed.

        Returns the emitted transition events in order. Two-phase mode emits
        'close'/'open' exactly as before. Four-phase mode additionally emits
        'midday' (AM->MIDDAY) and 'resume' (MIDDAY->PM) — the loop ignores
        those, so the midday break runs no settlement hooks. Only PM->CLOSED
        emits 'close' and only CLOSED->AM emits 'open' (session_id++). Deadlines
        are stamped with the *scheduled* boundary so the cycle never drifts;
        always [] in 24/7 mode.
        """
        events: list[str] = []
        with self._lock:
            if self.always_open:
                return events
            now = self._now()
            deadline = self._next_transition_at_locked()
            while deadline is not None and now >= deadline:
                next_state, event = self._advance_phase_locked()
                self._state = next_state
                if event == "open":
                    self._session_id += 1
                events.append(event)
                self._state_since = deadline
                deadline = self._next_transition_at_locked()
        return events

    # --- Internals ---

    def _advance_phase_locked(self) -> tuple[str, str]:
        """Return the (next_state, event) for the current phase.

        Must be called with self._lock held.
        """
        if self._midday_enabled:
            if self._state == PHASE_AM:
                return PHASE_MIDDAY, "midday"
            if self._state == PHASE_MIDDAY:
                return PHASE_PM, "resume"
            if self._state == PHASE_PM:
                return STATE_CLOSED, "close"
            return PHASE_AM, "open"  # CLOSED -> AM (new trading day)
        if self._state == STATE_OPEN:
            return STATE_CLOSED, "close"
        return STATE_OPEN, "open"

    def _phase_duration_locked(self, phase: str) -> float | None:
        """Length of ``phase`` in seconds (None in 24/7 mode)."""
        if self._open_seconds is None or self._break_seconds is None:
            return None
        if phase == STATE_CLOSED:
            return self._break_seconds
        if phase == PHASE_MIDDAY:
            return self._midday_break_seconds
        if phase in (PHASE_AM, PHASE_PM):
            return self._open_seconds / 2.0
        return self._open_seconds  # STATE_OPEN (two-phase)

    def _next_transition_at_locked(self) -> float | None:
        """Next transition deadline. Must be called with self._lock held."""
        duration = self._phase_duration_locked(self._state)
        if duration is None:
            return None
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
    (day-state roll). Midday events ('midday'/'resume', CN-2 §5) run NO hook —
    the lunch break neither settles nor rolls prev_close nor unlocks T+1. Hooks
    are synchronous and must be short (they run on the event loop, like the
    snapshot loop's DB work).

    Runs indefinitely until cancelled via ``asyncio.CancelledError`` (clean
    cancellation). Hook/tick errors are logged and the loop continues.
    """
    while True:
        try:
            for event in clock.tick():
                logger.info(
                    "Session clock: market %s (session %d)", event, clock.session_id
                )
                if event == "close":
                    hook = on_close
                elif event == "open":
                    hook = on_open
                else:
                    hook = None  # midday break — pause, no settlement/roll
                if hook is not None:
                    hook()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Session clock loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)
