"""HistoryProvider unit tests (D1 contract §1/§6) — ZERO network.

Every real-provider path runs against an INJECTED fake ``fetch_fn`` or a
monkeypatched lazy-import hook; nothing here can reach Yahoo/Eastmoney.
Covers: the sample provider reading the committed CSVs (window slicing,
missing tickers), yfinance normal/empty/exception/import-failure paths,
and akshare retry-with-backoff semantics (fail-fail-succeed, permanent
failure, empty results, import failure, injected sleep — no real waits).
"""

from __future__ import annotations

from datetime import date

import pytest

import app.market.history as history_mod
from app.market.history import (
    AkshareProvider,
    DailyBar,
    HistoryFetchError,
    SampleProvider,
    YFinanceProvider,
    build_default_providers,
)

START = date(2023, 7, 1)
END = date(2026, 7, 1)


def _bar(day: str, price: float = 100.0) -> DailyBar:
    return DailyBar(
        date=day, open=price, high=price + 1, low=price - 1, close=price, volume=1000.0
    )


class TestSampleProvider:
    def test_reads_committed_bars_ascending(self):
        provider = SampleProvider("us")
        bars = provider.fetch_daily("AAPL", START, END)
        assert len(bars) > 700
        dates = [bar.date for bar in bars]
        assert dates == sorted(dates)
        last = bars[-1]
        assert last.date == "2026-06-30"
        assert last.low <= min(last.open, last.close) <= last.close <= last.high
        assert last.close == pytest.approx(190.0)

    def test_cn_market_and_lowercase_normalization(self):
        bars = SampleProvider("cn").fetch_daily(" 600519 ", START, END)
        assert bars[-1].close == pytest.approx(1700.0)

    def test_window_is_a_duration(self):
        """years=1 style window -> ~252 trailing bars (contract: 永远可用)."""
        provider = SampleProvider("us")
        one_year = provider.fetch_daily("AAPL", date(2030, 1, 1), date(2031, 1, 1))
        assert 240 <= len(one_year) <= 260
        # The tail always ends at the newest committed bar.
        assert one_year[-1].date == "2026-06-30"

    def test_unknown_ticker_clear_error(self):
        with pytest.raises(HistoryFetchError) as exc:
            SampleProvider("us").fetch_daily("NOPE", START, END)
        assert "No sample data for NOPE" in str(exc.value)

    def test_wrong_market_ticker_errors(self):
        with pytest.raises(HistoryFetchError):
            SampleProvider("us").fetch_daily("600519", START, END)


class TestYFinanceProvider:
    def test_injected_fetch_passthrough(self):
        calls = []

        def fake_fetch(ticker, start, end):
            calls.append((ticker, start, end))
            return [_bar("2026-01-02"), _bar("2026-01-03")]

        provider = YFinanceProvider(fetch_fn=fake_fetch)
        bars = provider.fetch_daily("aapl", START, END)
        assert [b.date for b in bars] == ["2026-01-02", "2026-01-03"]
        assert calls == [("AAPL", START, END)]  # uppercase-normalized

    def test_empty_result_is_a_clear_error(self):
        provider = YFinanceProvider(fetch_fn=lambda *a: [])
        with pytest.raises(HistoryFetchError) as exc:
            provider.fetch_daily("AAPL", START, END)
        assert "no daily bars for AAPL" in str(exc.value)

    def test_exception_is_wrapped_not_raw(self):
        def boom(*a):
            raise ConnectionError("dns down")

        with pytest.raises(HistoryFetchError) as exc:
            YFinanceProvider(fetch_fn=boom).fetch_daily("AAPL", START, END)
        assert "yfinance fetch for AAPL failed" in str(exc.value)
        assert "dns down" in str(exc.value)

    def test_import_failure_degrades_to_source_unavailable(self, monkeypatch):
        def import_fails():
            raise ImportError("No module named 'yfinance'")

        monkeypatch.setattr(history_mod, "_import_yfinance", import_fails)
        with pytest.raises(HistoryFetchError) as exc:
            YFinanceProvider().fetch_daily("AAPL", START, END)
        assert "'yfinance' is unavailable" in str(exc.value)


class TestAkshareProvider:
    def test_retries_then_succeeds(self):
        sleeps: list[float] = []
        attempts = {"n": 0}

        def flaky(ticker, start, end):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TimeoutError(f"transient {attempts['n']}")
            return [_bar("2026-01-02")]

        provider = AkshareProvider(
            fetch_fn=flaky, retries=2, backoff_seconds=0.5, sleep=sleeps.append
        )
        bars = provider.fetch_daily("600519", START, END)
        assert len(bars) == 1
        assert attempts["n"] == 3  # initial try + 2 retries
        assert sleeps == [0.5, 1.0]  # linear backoff, injected sleep

    def test_permanent_failure_reports_attempts(self):
        sleeps: list[float] = []

        def always_down(*a):
            raise ConnectionError("blocked")

        provider = AkshareProvider(
            fetch_fn=always_down, retries=2, sleep=sleeps.append
        )
        with pytest.raises(HistoryFetchError) as exc:
            provider.fetch_daily("600519", START, END)
        assert "failed after 3 attempts" in str(exc.value)
        assert "blocked" in str(exc.value)
        assert len(sleeps) == 2  # no sleep after the final attempt

    def test_empty_results_also_retry_then_error(self):
        calls = {"n": 0}

        def empty(*a):
            calls["n"] += 1
            return []

        provider = AkshareProvider(fetch_fn=empty, retries=2, sleep=lambda _s: None)
        with pytest.raises(HistoryFetchError) as exc:
            provider.fetch_daily("600519", START, END)
        assert calls["n"] == 3
        assert "no daily bars for 600519" in str(exc.value)

    def test_import_failure_degrades_to_source_unavailable(self, monkeypatch):
        def import_fails():
            raise ImportError("No module named 'akshare'")

        monkeypatch.setattr(history_mod, "_import_akshare", import_fails)
        provider = AkshareProvider(retries=0)
        with pytest.raises(HistoryFetchError) as exc:
            provider.fetch_daily("600519", START, END)
        assert "'akshare' is unavailable" in str(exc.value)


class TestDefaultProviderSet:
    def test_build_default_providers_shape(self):
        providers = build_default_providers("us")
        assert set(providers) == {"sample", "yfinance", "akshare"}
        assert providers["sample"].name == "sample"
        assert providers["yfinance"].name == "yfinance"
        assert providers["akshare"].name == "akshare"

    def test_construction_never_imports_optional_packages(self):
        """Lazy-import red line: building providers must not load yf/ak."""
        import sys

        build_default_providers("us")
        build_default_providers("cn")
        assert "yfinance" not in sys.modules
        assert "akshare" not in sys.modules
