"""Light validation for examples/finally_bot.py (P3 contract §9).

Loads the bot module straight from the top-level ``examples/`` directory via
importlib (it is not a package, so ``sys.path`` tricks are unnecessary) and
unit-tests the pure ``crossed()`` signal function against known sequences.
No network I/O ever happens: importing the module only reads environment
variables, and the import test proves it by booby-trapping ``requests``.
"""

from __future__ import annotations

import importlib.util
import os
from collections import deque
from unittest import mock

import pytest

BOT_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "examples", "finally_bot.py"
)
BOT_ENV_KEYS = ("FINALLY_URL", "FINALLY_API_KEY", "BOT_TICKER", "BOT_QTY")


def _load_bot(env: dict[str, str] | None = None):
    """Execute examples/finally_bot.py with a controlled environment.

    Bot-specific variables are stripped first so host settings can't leak
    into assertions; ``env`` overlays any values a test wants to pin.
    """
    clean = {k: v for k, v in os.environ.items() if k not in BOT_ENV_KEYS}
    clean.update(env or {})
    with mock.patch.dict(os.environ, clean, clear=True):
        spec = importlib.util.spec_from_file_location("finally_bot_under_test", BOT_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bot():
    """The bot module loaded once with default (unset) bot env vars."""
    return _load_bot()


class TestImport:
    def test_import_makes_no_network_calls(self, monkeypatch):
        """Executing the module must not touch the network (contract §9)."""

        def bomb(*args, **kwargs):  # pragma: no cover - fails the test if hit
            raise AssertionError("finally_bot performed network I/O at import time")

        for attr in ("request", "get", "post"):
            monkeypatch.setattr(f"requests.{attr}", bomb)
        module = _load_bot()
        assert callable(module.crossed)
        assert callable(module.main)

    def test_env_defaults(self, bot):
        assert bot.BASE == "http://localhost:8000"
        assert bot.API_KEY == ""
        assert bot.TICKER == "NVDA"
        assert bot.QTY == 2.0
        assert (bot.FAST, bot.SLOW) == (5, 20)

    def test_env_overrides(self):
        module = _load_bot(
            {
                "FINALLY_URL": "http://example.test:9000/",
                "FINALLY_API_KEY": "fk_secret",
                "BOT_TICKER": "aapl",
                "BOT_QTY": "3.5",
            }
        )
        assert module.BASE == "http://example.test:9000"  # trailing slash stripped
        assert module.API_KEY == "fk_secret"
        assert module.TICKER == "AAPL"  # uppercased
        assert module.QTY == 3.5


class TestCrossed:
    def test_insufficient_history_returns_none(self, bot):
        # Needs slow + 1 prices: one full slow window before AND after the tick.
        assert bot.crossed([], 2, 3) is None
        assert bot.crossed([10.0, 11.0, 12.0], 2, 3) is None
        assert bot.crossed([100.0] * 20, 5, 20) is None

    def test_golden_cross_small_windows(self, bot):
        # fast SMA2 jumps from 10 -> 15 while slow SMA3 only reaches ~13.3.
        assert bot.crossed([10.0, 10.0, 10.0, 20.0], 2, 3) == "golden"

    def test_death_cross_small_windows(self, bot):
        # Before: SMA2=25 > SMA3=20. After the crash tick: SMA2=17.5 < SMA3~18.3.
        assert bot.crossed([10.0, 20.0, 30.0, 5.0], 2, 3) == "death"

    def test_no_signal_when_already_above_and_rising(self, bot):
        # Fast stays above slow on both sides of the tick -> no new cross.
        assert bot.crossed([10.0, 20.0, 30.0, 40.0], 2, 3) is None

    def test_flat_series_returns_none(self, bot):
        assert bot.crossed([10.0] * 10, 2, 3) is None
        assert bot.crossed([100.0] * 30, 5, 20) is None

    def test_golden_cross_with_bot_default_windows(self, bot):
        # 20 flat prices then a spike: SMA5=120 crosses above SMA20=105.
        assert bot.crossed([100.0] * 20 + [200.0], bot.FAST, bot.SLOW) == "golden"

    def test_accepts_deque_input(self, bot):
        window = deque([10.0, 10.0, 10.0, 20.0], maxlen=4)
        assert bot.crossed(window, 2, 3) == "golden"

    def test_does_not_mutate_input(self, bot):
        series = [10.0, 10.0, 10.0, 20.0]
        bot.crossed(series, 2, 3)
        assert series == [10.0, 10.0, 10.0, 20.0]

    def test_streamed_feed_emits_golden_then_death_exactly_once(self, bot):
        # Simulate the bot's polling loop: ramp up then sell-off produces one
        # buy signal followed by one liquidation signal, and nothing else.
        window: deque[float] = deque(maxlen=bot.SLOW + 1)
        signals = []
        for price in [100.0] * 20 + [110.0] * 10 + [90.0] * 15:
            window.append(price)
            signal = bot.crossed(window, bot.FAST, bot.SLOW)
            if signal is not None:
                signals.append(signal)
        assert signals == ["golden", "death"]

    def test_sma_helper(self, bot):
        assert bot.sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5
        assert bot.sma([1.0, 2.0, 3.0, 4.0], 4) == 2.5
