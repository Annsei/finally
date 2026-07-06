"""Tests for the SessionClock state machine and its background loop (M3.1)."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.market.session import SessionClock, session_clock_loop
from tests.conftest import FakeTime

# All session-mode clocks in this file: 30s open, 10s break, starting at t=1000.
OPEN = 30.0
BREAK = 10.0
T0 = 1_000.0


def make_clock() -> tuple[SessionClock, FakeTime]:
    fake = FakeTime(T0)
    return SessionClock(OPEN, BREAK, now=fake), fake


class TestSessionClockTransitions:
    """State machine driven by injected time — no sleeping."""

    def test_starts_open_with_session_1(self):
        clock, _ = make_clock()
        assert clock.state == "open"
        assert clock.is_open is True
        assert clock.session_id == 1
        assert clock.state_since == T0
        assert clock.always_open is False

    def test_next_transition_is_open_deadline(self):
        clock, _ = make_clock()
        assert clock.next_transition_at() == T0 + OPEN

    def test_no_transition_before_deadline(self):
        clock, fake = make_clock()
        fake.advance(OPEN - 0.001)
        assert clock.tick() == []
        assert clock.state == "open"

    def test_open_to_closed_at_deadline(self):
        clock, fake = make_clock()
        fake.advance(OPEN)
        assert clock.tick() == ["close"]
        assert clock.state == "closed"
        assert clock.is_open is False
        assert clock.session_id == 1  # id bumps on reopen, not on close
        assert clock.state_since == T0 + OPEN  # scheduled boundary, no drift
        assert clock.next_transition_at() == T0 + OPEN + BREAK

    def test_reopen_increments_session_id(self):
        clock, fake = make_clock()
        fake.advance(OPEN)
        clock.tick()
        fake.advance(BREAK)
        assert clock.tick() == ["open"]
        assert clock.state == "open"
        assert clock.is_open is True
        assert clock.session_id == 2
        assert clock.state_since == T0 + OPEN + BREAK

    def test_tick_is_idempotent_between_deadlines(self):
        clock, fake = make_clock()
        fake.advance(OPEN)
        assert clock.tick() == ["close"]
        assert clock.tick() == []  # already transitioned

    def test_large_time_jump_catches_up_deterministically(self):
        """A delayed loop replays every missed transition in order."""
        clock, fake = make_clock()
        fake.advance(2 * (OPEN + BREAK))  # two full cycles
        assert clock.tick() == ["close", "open", "close", "open"]
        assert clock.state == "open"
        assert clock.session_id == 3
        assert clock.state_since == T0 + 2 * (OPEN + BREAK)


class TestSessionClock247Mode:
    """24/7 mode: always open, never transitions."""

    @pytest.mark.parametrize(
        "open_seconds,break_seconds",
        [
            (None, None),
            (None, 120),
            (1800, None),
            (0, 120),
            (1800, 0),
            (-5, 120),
            (1800, -1),
        ],
    )
    def test_always_open(self, open_seconds, break_seconds):
        fake = FakeTime(T0)
        clock = SessionClock(open_seconds, break_seconds, now=fake)
        assert clock.always_open is True
        assert clock.is_open is True
        assert clock.state == "open"
        assert clock.next_transition_at() is None
        fake.advance(1_000_000)
        assert clock.tick() == []
        assert clock.state == "open"
        assert clock.session_id == 1

    def test_default_construction_is_247(self):
        clock = SessionClock()
        assert clock.always_open is True
        assert clock.is_open is True

    def test_default_time_source_is_wall_clock(self):
        before = time.time()
        clock = SessionClock()
        assert before <= clock.state_since <= time.time()


class TestSessionClockSnapshot:
    """snapshot() is the exact /api/market/session payload."""

    EXPECTED_KEYS = {"state", "session_id", "state_since", "next_transition_at", "now"}

    def test_shape_in_session_mode(self):
        clock, fake = make_clock()
        snap = clock.snapshot()
        assert set(snap.keys()) == self.EXPECTED_KEYS
        assert snap["state"] == "open"
        assert snap["session_id"] == 1
        assert snap["state_since"] == T0
        assert snap["next_transition_at"] == T0 + OPEN
        assert snap["now"] == T0
        fake.advance(OPEN)
        clock.tick()
        snap = clock.snapshot()
        assert snap["state"] == "closed"
        assert snap["next_transition_at"] == T0 + OPEN + BREAK
        assert snap["now"] == T0 + OPEN

    def test_shape_in_247_mode(self):
        snap = SessionClock(now=FakeTime(T0)).snapshot()
        assert set(snap.keys()) == self.EXPECTED_KEYS
        assert snap["state"] == "open"
        assert snap["session_id"] == 1
        assert snap["next_transition_at"] is None


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll ``predicate`` until true or fail the test after ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    pytest.fail("condition not met within timeout")


class TestSessionClockLoop:
    """Background driver: runs hooks on transitions, cancels cleanly."""

    async def test_runs_close_and_open_hooks(self):
        clock, fake = make_clock()
        events: list[str] = []
        task = asyncio.create_task(
            session_clock_loop(
                clock,
                on_close=lambda: events.append("close"),
                on_open=lambda: events.append("open"),
                interval=0.005,
            )
        )
        try:
            fake.advance(OPEN)
            await _wait_for(lambda: events == ["close"])
            assert clock.state == "closed"
            fake.advance(BREAK)
            await _wait_for(lambda: events == ["close", "open"])
            assert clock.state == "open"
            assert clock.session_id == 2
        finally:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_survives_hook_errors(self):
        clock, fake = make_clock()
        opened: list[str] = []

        def bad_close() -> None:
            raise RuntimeError("settlement boom")

        task = asyncio.create_task(
            session_clock_loop(
                clock,
                on_close=bad_close,
                on_open=lambda: opened.append("open"),
                interval=0.005,
            )
        )
        try:
            fake.advance(OPEN)
            await _wait_for(lambda: clock.state == "closed")
            fake.advance(BREAK)
            await _wait_for(lambda: opened == ["open"])  # loop kept running
        finally:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_clean_cancellation_without_transitions(self):
        clock, _ = make_clock()
        task = asyncio.create_task(session_clock_loop(clock, interval=0.005))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert clock.state == "open"  # nothing transitioned
