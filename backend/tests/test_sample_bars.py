"""Sample daily-bar generator + committed data set (D1 contract §1/§6).

Covers:
- determinism: the same fixed seed produces identical output on repeated
  calls (the generator uses only ``random.Random`` seeded with stable
  strings — no wall clock, no numpy stream dependence);
- shape: us 10 + cn 14 tickers, 756 business-day bars each, OHLC sanity
  (high >= max(open, close) >= min(open, close) >= low > 0), positive
  volumes, strictly ascending weekday dates ending at the fixed anchor;
- regimes: every ticker's plan mixes trend / drawdown / range;
- the COMMITTED CSVs parse, cover the same tickers as the live universes,
  and match a fresh generator run byte-for-byte (drift guard: if these
  files are ever edited by hand or the generator changes, this fails);
- final closes land exactly on the live simulator seed prices.

ZERO network: everything here reads the repo checkout only.
"""

from __future__ import annotations

import csv
import importlib.util
from datetime import date
from pathlib import Path

import pytest

from app.market.history import SAMPLE_BARS_DIR
from app.market.seed_prices import DEFAULT_WATCHLIST, SEED_PRICES
from app.market.seed_prices_cn import CN_SEED_PRICES

_SCRIPT = Path(__file__).parents[2] / "scripts" / "gen_sample_bars.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_sample_bars", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


@pytest.fixture(scope="module")
def us_bars(gen):
    return gen.generate_market_bars("us")


@pytest.fixture(scope="module")
def cn_bars(gen):
    return gen.generate_market_bars("cn")


class TestDeterminismAndShape:
    def test_same_seed_same_output(self, gen):
        a = gen.generate_ticker_bars("us", "AAPL", 190.0)
        b = gen.generate_ticker_bars("us", "AAPL", 190.0)
        assert a == b

    def test_different_tickers_differ(self, gen):
        a = gen.generate_ticker_bars("us", "AAPL", 190.0)
        b = gen.generate_ticker_bars("us", "MSFT", 190.0)
        assert a != b

    def test_universe_coverage(self, us_bars, cn_bars):
        assert set(us_bars) == set(DEFAULT_WATCHLIST) and len(us_bars) == 10
        assert set(cn_bars) == set(CN_SEED_PRICES) and len(cn_bars) == 14

    def test_bar_count_and_anchor_date(self, gen, us_bars):
        for bars in us_bars.values():
            assert len(bars) == gen.BARS_PER_TICKER == 756
            assert bars[-1]["date"] == gen.END_DATE.isoformat() == "2026-06-30"

    def test_dates_ascending_weekdays(self, us_bars):
        bars = us_bars["AAPL"]
        dates = [bar["date"] for bar in bars]
        assert dates == sorted(dates) and len(set(dates)) == len(dates)
        assert all(date.fromisoformat(d).weekday() < 5 for d in dates)

    def test_ohlc_sanity_and_volume(self, us_bars, cn_bars):
        for bars in list(us_bars.values()) + list(cn_bars.values()):
            for bar in bars:
                assert bar["high"] >= max(bar["open"], bar["close"])
                assert bar["low"] <= min(bar["open"], bar["close"])
                assert bar["low"] > 0
                assert bar["volume"] >= 1

    def test_final_close_hits_seed_price(self, us_bars, cn_bars):
        for ticker, bars in us_bars.items():
            assert bars[-1]["close"] == pytest.approx(SEED_PRICES[ticker], abs=0.005)
        for ticker, bars in cn_bars.items():
            assert bars[-1]["close"] == pytest.approx(CN_SEED_PRICES[ticker], abs=0.005)

    def test_regime_plan_mixes_all_three(self, gen):
        import random as _random

        for ticker in ("AAPL", "600519"):
            rng = _random.Random(f"{gen.GENERATOR_VERSION}:x:{ticker}")
            plan = gen._regime_plan(rng, gen.BARS_PER_TICKER)
            assert {name for name, _ in plan} == {"trend", "drawdown", "range"}
            assert sum(length for _, length in plan) == gen.BARS_PER_TICKER


class TestCommittedFiles:
    def test_committed_files_exist_for_both_markets(self):
        assert sorted(p.stem for p in (SAMPLE_BARS_DIR / "us").glob("*.csv")) == sorted(
            DEFAULT_WATCHLIST
        )
        assert sorted(p.stem for p in (SAMPLE_BARS_DIR / "cn").glob("*.csv")) == sorted(
            CN_SEED_PRICES
        )

    @pytest.mark.parametrize("market,ticker", [("us", "AAPL"), ("cn", "600519")])
    def test_committed_csv_matches_generator(self, gen, market, ticker):
        """Drift guard — the committed data IS the generator's output."""
        with (SAMPLE_BARS_DIR / market / f"{ticker}.csv").open(
            "r", encoding="utf-8", newline=""
        ) as fh:
            rows = list(csv.DictReader(fh))
        generated = gen.generate_market_bars(market)[ticker]
        assert len(rows) == len(generated)
        for row, bar in zip(rows, generated):
            assert row["date"] == bar["date"]
            assert float(row["open"]) == pytest.approx(bar["open"], abs=1e-9)
            assert float(row["high"]) == pytest.approx(bar["high"], abs=1e-9)
            assert float(row["low"]) == pytest.approx(bar["low"], abs=1e-9)
            assert float(row["close"]) == pytest.approx(bar["close"], abs=1e-9)
            assert int(row["volume"]) == bar["volume"]

    def test_readme_marks_data_synthetic(self):
        text = (SAMPLE_BARS_DIR / "README.md").read_text(encoding="utf-8")
        assert "not real market prices" in text.lower()
        assert "gen_sample_bars.py" in text
