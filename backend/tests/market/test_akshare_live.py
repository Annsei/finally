"""Tests for AkshareLiveSource (D2 contract §1/§6) — injected fake fetcher.

Zero-network invariant: every test drives the source through the injected
``fetch_fn`` (or a monkeypatched ``_import_akshare`` hook) — akshare is
never imported and no request is ever made. Covers: 东财 row parsing and
field mapping, tracked-set (universe) filtering, cumulative-volume
differencing (first frame 0, negative delta clamped to 0, NaN 0), 停牌/NaN
row handling, bid/ask mapped-when-present, closed-session frozen frames
(no re-stamp → freshness gate can engage), poll-failure resilience (loop
never dies) with throttled consecutive-failure warnings, add/remove ticker,
and the start/stop lifecycle mirrored from MassiveDataSource.
"""

from __future__ import annotations

import asyncio
import logging
import math
from types import SimpleNamespace

import pytest

import app.market.akshare_live as akshare_live
from app.market.akshare_live import (
    FAILURE_WARN_INTERVAL_SECONDS,
    AkshareLiveSource,
)
from app.market.cache import PriceCache


def _row(
    code: str,
    price: object,
    prev_close: object = None,
    high: object = None,
    low: object = None,
    volume: object = None,
    bid: object = None,
    ask: object = None,
) -> dict:
    """One 东财 spot row. None values leave the column ABSENT (like the
    endpoint omitting 买一/卖一), so fallback paths are exercised by default."""
    row: dict = {"代码": code, "最新价": price, "名称": "测试"}
    if prev_close is not None:
        row["昨收"] = prev_close
    if high is not None:
        row["最高"] = high
    if low is not None:
        row["最低"] = low
    if volume is not None:
        row["成交量"] = volume
    if bid is not None:
        row["买一"] = bid
    if ask is not None:
        row["卖一"] = ask
    return row


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_source(
    tickers: list[str] | None = None, start: float = 1_000.0
) -> tuple[PriceCache, AkshareLiveSource, FakeClock, dict]:
    """Source wired to a mutable rows holder and a deterministic clock."""
    cache = PriceCache()
    clock = FakeClock(start)
    holder: dict = {"rows": []}
    source = AkshareLiveSource(
        price_cache=cache,
        poll_interval=60.0,  # Long interval so the loop never auto-polls
        fetch_fn=lambda: holder["rows"],
        now=clock,
    )
    source._tickers = list(tickers or ["600519"])
    return cache, source, clock, holder


@pytest.mark.asyncio
class TestAksharePolling:
    async def test_poll_maps_spot_fields_into_cache(self):
        cache, source, clock, holder = _make_source(["600519", "000858"])
        holder["rows"] = [
            _row("600519", 1701.55, prev_close=1690.0, high=1710.0, low=1688.0, volume=12345.0),
            _row("000858", 141.02, prev_close=140.0, high=142.5, low=139.1, volume=999.0),
        ]
        await source._poll_once()

        update = cache.get("600519")
        assert update is not None
        assert update.price == 1701.55
        assert update.prev_close == 1690.0
        assert update.day_high == 1710.0
        assert update.day_low == 1688.0
        assert update.timestamp == clock.now  # Stamped with the poll time
        assert cache.get_price("000858") == 141.02

    async def test_full_market_snapshot_filtered_to_tracked_set(self):
        """Untracked codes in the full-market snapshot never reach the cache."""
        cache, source, _clock, holder = _make_source(["600519"])
        holder["rows"] = [
            _row("999999", 1.23, volume=1.0),
            _row("600519", 1700.0, volume=10.0),
            _row("000001", 11.0, volume=2.0),
        ]
        await source._poll_once()

        assert cache.get_price("600519") == 1700.0
        assert cache.get("999999") is None
        assert cache.get("000001") is None
        assert len(cache) == 1

    async def test_tracked_ticker_missing_from_snapshot_keeps_last_frame(self):
        cache, source, _clock, holder = _make_source(["600519"])
        holder["rows"] = [_row("600519", 1700.0, volume=10.0)]
        await source._poll_once()
        holder["rows"] = [_row("000858", 140.0, volume=5.0)]  # 600519 absent
        await source._poll_once()

        assert cache.get_price("600519") == 1700.0  # previous frame retained

    async def test_first_frame_volume_delta_is_zero(self):
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=1_000_000.0)]
        await source._poll_once()
        assert cache.get("600519").volume == 0.0

    async def test_cumulative_volume_differenced_across_polls(self):
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=10_000.0)]
        await source._poll_once()
        holder["rows"] = [_row("600519", 1701.0, volume=12_500.0)]
        await source._poll_once()
        assert cache.get("600519").volume == 2_500.0

    async def test_negative_cumulative_delta_clamped_to_zero(self):
        """A cumulative decrease (session reset) clamps the delta at 0 and
        the lower cumulative becomes the new baseline."""
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=10_000.0)]
        await source._poll_once()
        holder["rows"] = [_row("600519", 1701.0, volume=4_000.0)]
        await source._poll_once()
        assert cache.get("600519").volume == 0.0
        holder["rows"] = [_row("600519", 1702.0, volume=4_300.0)]
        await source._poll_once()
        assert cache.get("600519").volume == 300.0

    async def test_nan_volume_records_zero_and_never_poisons_baseline(self):
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=float("nan"))]
        await source._poll_once()
        assert cache.get("600519").volume == 0.0
        holder["rows"] = [_row("600519", 1701.0, volume=500.0)]
        await source._poll_once()
        assert cache.get("600519").volume == 0.0  # first real cumulative = baseline
        holder["rows"] = [_row("600519", 1702.0, volume=800.0)]
        await source._poll_once()
        assert cache.get("600519").volume == 300.0

    async def test_suspended_row_nan_or_placeholder_price_keeps_last_frame(self):
        """停牌 rows carry NaN (or '-' strings) — the quote is not clobbered."""
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=10.0)]
        await source._poll_once()

        for bad_price in (float("nan"), "-", 0.0, -5.0, None):
            holder["rows"] = [_row("600519", bad_price, volume=20.0)]
            await source._poll_once()
            assert cache.get_price("600519") == 1700.0

    async def test_bid_ask_mapped_when_columns_present(self):
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=1.0, bid=1699.9, ask=1700.1)]
        await source._poll_once()
        update = cache.get("600519")
        assert update.bid == 1699.9
        assert update.ask == 1700.1

    async def test_absent_bid_ask_columns_fall_back_to_price(self):
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=1.0)]
        await source._poll_once()
        update = cache.get("600519")
        assert update.bid == update.ask == 1700.0

    async def test_malformed_row_skipped_others_processed(self):
        cache, source, _clock, holder = _make_source(["600519", "000858"])
        holder["rows"] = [
            "not-a-mapping",
            _row("600519", 1700.0, volume=1.0),
        ]
        await source._poll_once()
        assert cache.get_price("600519") == 1700.0

    async def test_empty_tickers_skips_poll(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return []

        cache = PriceCache()
        source = AkshareLiveSource(price_cache=cache, fetch_fn=fetch)
        await source._poll_once()
        assert calls["n"] == 0


@pytest.mark.asyncio
class TestAkshareFrozenFrames:
    async def test_unchanged_closing_frame_is_not_restamped(self):
        """Closed session: same price + no new volume → quote stays frozen at
        its last active stamp so the trade freshness gate can engage."""
        cache, source, clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=10_000.0)]
        await source._poll_once()
        frozen_ts = cache.get("600519").timestamp

        clock.advance(15.0)
        await source._poll_once()  # identical frame (cumulative unchanged)
        clock.advance(15.0)
        await source._poll_once()
        assert cache.get("600519").timestamp == frozen_ts

    async def test_volume_activity_at_same_price_still_updates(self):
        cache, source, clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=10_000.0)]
        await source._poll_once()
        clock.advance(15.0)
        holder["rows"] = [_row("600519", 1700.0, volume=10_500.0)]
        await source._poll_once()
        update = cache.get("600519")
        assert update.timestamp == clock.now
        assert update.volume == 500.0

    async def test_price_change_resumes_stamping(self):
        cache, source, clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=10_000.0)]
        await source._poll_once()
        clock.advance(15.0)
        await source._poll_once()  # frozen
        clock.advance(15.0)
        holder["rows"] = [_row("600519", 1700.5, volume=10_000.0)]
        await source._poll_once()
        assert cache.get("600519").price == 1700.5
        assert cache.get("600519").timestamp == clock.now


@pytest.mark.asyncio
class TestAkshareFailureHandling:
    @staticmethod
    def _failing_source(clock: FakeClock) -> tuple[PriceCache, AkshareLiveSource]:
        cache = PriceCache()

        def fetch():
            raise RuntimeError("东财 endpoint down")

        source = AkshareLiveSource(
            price_cache=cache, poll_interval=60.0, fetch_fn=fetch, now=clock
        )
        source._tickers = ["600519"]
        return cache, source

    async def test_fetch_error_keeps_previous_frame_and_does_not_raise(self):
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=10.0)]
        await source._poll_once()

        def boom():
            raise RuntimeError("network error")

        source._fetch_fn = boom
        await source._poll_once()  # must not raise
        assert cache.get_price("600519") == 1700.0

    async def test_consecutive_failures_warn_throttled(self, caplog):
        clock = FakeClock()
        _cache, source = self._failing_source(clock)
        with caplog.at_level(logging.WARNING, logger="app.market.akshare_live"):
            await source._poll_once()  # 1st failure → warns
            clock.advance(15.0)
            await source._poll_once()  # inside window → suppressed
            clock.advance(15.0)
            await source._poll_once()  # still inside → suppressed
        warnings = [r for r in caplog.records if "AKShare spot poll failed" in r.message]
        assert len(warnings) == 1
        assert "(1 consecutive)" in warnings[0].message

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.market.akshare_live"):
            clock.advance(FAILURE_WARN_INTERVAL_SECONDS)
            await source._poll_once()  # window elapsed → warns with the count
        warnings = [r for r in caplog.records if "AKShare spot poll failed" in r.message]
        assert len(warnings) == 1
        assert "(4 consecutive)" in warnings[0].message

    async def test_success_resets_failure_streak(self, caplog):
        cache, source, clock, holder = _make_source()

        def boom():
            raise RuntimeError("hiccup")

        rows = [_row("600519", 1700.0, volume=10.0)]
        with caplog.at_level(logging.WARNING, logger="app.market.akshare_live"):
            source._fetch_fn = boom
            await source._poll_once()  # failure #1 → warns
            source._fetch_fn = lambda: rows
            clock.advance(1.0)
            await source._poll_once()  # success → streak reset
            assert cache.get_price("600519") == 1700.0
            source._fetch_fn = boom
            clock.advance(1.0)
            await source._poll_once()  # NEW streak → warns immediately
        warnings = [r for r in caplog.records if "AKShare spot poll failed" in r.message]
        assert len(warnings) == 2
        assert "(1 consecutive)" in warnings[-1].message

    async def test_poll_loop_survives_repeated_failures(self):
        """The background loop keeps running through back-to-back failures."""
        cache = PriceCache()

        def boom():
            raise RuntimeError("perma-down")

        source = AkshareLiveSource(price_cache=cache, poll_interval=0.01, fetch_fn=boom)
        await source.start(["600519"])
        try:
            await asyncio.sleep(0.05)  # several failing cycles
            assert source._task is not None
            assert not source._task.done()  # loop is alive, not crashed
        finally:
            await source.stop()
        assert source._task is None


@pytest.mark.asyncio
class TestAkshareLifecycle:
    async def test_start_polls_immediately_and_stop_cancels(self):
        cache = PriceCache()
        rows = [_row("600519", 1700.0, volume=10.0)]
        source = AkshareLiveSource(price_cache=cache, poll_interval=60.0, fetch_fn=lambda: rows)
        await source.start(["600519"])
        assert cache.get_price("600519") == 1700.0  # immediate first poll
        assert source._task is not None and not source._task.done()

        await source.stop()
        assert source._task is None
        await source.stop()  # idempotent

    async def test_add_ticker_normalizes_and_appears_on_next_poll(self):
        cache, source, _clock, holder = _make_source(["600519"])
        await source.add_ticker("  000858  ")
        await source.add_ticker("000858")  # duplicate is a no-op
        assert source.get_tickers() == ["600519", "000858"]

        holder["rows"] = [
            _row("600519", 1700.0, volume=1.0),
            _row("000858", 140.0, volume=2.0),
        ]
        await source._poll_once()
        assert cache.get_price("000858") == 140.0

    async def test_remove_ticker_clears_cache_and_volume_baseline(self):
        cache, source, _clock, holder = _make_source(["600519"])
        holder["rows"] = [_row("600519", 1700.0, volume=10_000.0)]
        await source._poll_once()

        await source.remove_ticker("600519")
        assert source.get_tickers() == []
        assert cache.get("600519") is None

        source._tickers = ["600519"]
        holder["rows"] = [_row("600519", 1701.0, volume=12_000.0)]
        await source._poll_once()
        # Treated as a first poll again — no delta against the old baseline.
        assert cache.get("600519").volume == 0.0


@pytest.mark.asyncio
class TestAkshareLazyImport:
    async def test_default_fetch_uses_lazy_import_hook(self, monkeypatch):
        """No injected fetcher → the akshare import happens INSIDE the poll
        via the module hook, and the DataFrame is converted to records."""
        rows = [_row("600519", 1700.0, volume=10.0)]
        fake_frame = SimpleNamespace(to_dict=lambda orient: list(rows))
        fake_akshare = SimpleNamespace(stock_zh_a_spot_em=lambda: fake_frame)
        calls = {"n": 0}

        def fake_import():
            calls["n"] += 1
            return fake_akshare

        monkeypatch.setattr(akshare_live, "_import_akshare", fake_import)
        cache = PriceCache()
        source = AkshareLiveSource(price_cache=cache, poll_interval=60.0)
        assert calls["n"] == 0  # constructing the source imports nothing
        source._tickers = ["600519"]
        await source._poll_once()
        assert calls["n"] == 1
        assert cache.get_price("600519") == 1700.0

    async def test_missing_akshare_degrades_to_throttled_warning(self, monkeypatch, caplog):
        """A broken/missing akshare install can never crash the loop."""

        def fake_import():
            raise ImportError("No module named 'akshare'")

        monkeypatch.setattr(akshare_live, "_import_akshare", fake_import)
        cache = PriceCache()
        source = AkshareLiveSource(price_cache=cache, poll_interval=60.0)
        source._tickers = ["600519"]
        with caplog.at_level(logging.WARNING, logger="app.market.akshare_live"):
            await source._poll_once()  # must not raise
        assert cache.get("600519") is None
        assert any("AKShare spot poll failed" in r.message for r in caplog.records)

    async def test_injected_fetcher_never_touches_the_import_hook(self, monkeypatch):
        monkeypatch.setattr(
            akshare_live,
            "_import_akshare",
            lambda: (_ for _ in ()).throw(AssertionError("akshare imported")),
        )
        cache, source, _clock, holder = _make_source()
        holder["rows"] = [_row("600519", 1700.0, volume=1.0)]
        await source._poll_once()
        assert cache.get_price("600519") == 1700.0


class TestPositiveFloatGuard:
    @pytest.mark.parametrize(
        "value", [None, "-", "1700", True, False, float("nan"), float("inf"), 0.0, -1.0]
    )
    def test_rejects_non_positive_and_non_numeric(self, value):
        assert akshare_live._positive_float(value) is None

    def test_accepts_positive_finite(self):
        assert akshare_live._positive_float(1700) == 1700.0
        assert akshare_live._positive_float(0.01) == 0.01
        assert math.isclose(akshare_live._positive_float(1e-9), 1e-9)
