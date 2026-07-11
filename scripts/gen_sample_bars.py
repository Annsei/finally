#!/usr/bin/env python3
"""Deterministic sample daily-bar generator for FinAlly (D1 contract §1).

Generates the OFFLINE SAMPLE data set committed at
``backend/app/market/sample_bars/{us,cn}/<TICKER>.csv`` — the always-available
``sample`` history source used by tests, CI, E2E, and offline installs.

IMPORTANT: the output is a SYNTHETIC, NON-REAL price series. It is generated
from a fixed seed (no market data is downloaded, bundled, or redistributed)
purely so the app works without network access and tests never touch
Yahoo/Eastmoney. Do not use it for any real-world analysis.

Design (contract §1):
- us: the 10 default-watchlist equities; cn: the 14 A-share universe codes.
- ~3 years of business-day daily bars (Mon-Fri, no holiday calendar) ending
  at a FIXED anchor date, so output never depends on the wall clock.
- Three regimes mixed per ticker — trend (drift up), drawdown (drift down),
  and range (flat drift, choppier) — every ticker gets at least one segment
  of each, in a seed-shuffled order with seed-drawn lengths.
- Fully deterministic: only ``random.Random`` seeded with a stable string
  per (market, ticker); same seed -> byte-identical CSV output.
- The whole close path is rescaled so the FINAL close equals the market's
  live seed price, keeping sample history coherent with the simulator.

Regenerate (rarely needed — output is committed):

    python3 scripts/gen_sample_bars.py

Stdlib only; runs from any CWD (paths resolved relative to this file).
"""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

GENERATOR_VERSION = "finally-sample-v1"

# Fixed anchor — the last bar of every series. Never derived from today().
END_DATE = date(2026, 6, 30)

# ~3 years of business days (252 trading days/year convention).
BARS_PER_TICKER = 756

# Ticker -> final close (the live simulator's seed prices, duplicated here so
# the script stays stdlib-only and runnable without the backend venv).
US_FINAL_CLOSES: dict[str, float] = {
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
}

CN_FINAL_CLOSES: dict[str, float] = {
    "600519": 1700.00,
    "000858": 140.00,
    "300750": 180.00,
    "002594": 250.00,
    "601012": 18.00,
    "688981": 45.00,
    "300059": 15.00,
    "601318": 45.00,
    "600036": 35.00,
    "601988": 4.50,
    "600900": 28.00,
    "601899": 17.00,
    "000333": 75.00,
    "600276": 45.00,
}

MARKET_FINAL_CLOSES: dict[str, dict[str, float]] = {
    "us": US_FINAL_CLOSES,
    "cn": CN_FINAL_CLOSES,
}

# Regime name -> (daily drift, daily sigma). Range days are choppier relative
# to their drift; drawdowns bleed harder than trends climb (felt realism).
REGIMES: dict[str, tuple[float, float]] = {
    "trend": (0.0011, 0.016),
    "drawdown": (-0.0016, 0.022),
    "range": (0.0000, 0.013),
}

CSV_HEADER = ["date", "open", "high", "low", "close", "volume"]

OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "backend" / "app" / "market" / "sample_bars"


def business_days(end: date, count: int) -> list[date]:
    """The ``count`` business days (Mon-Fri) ending at ``end``, ascending.

    ``end`` falling on a weekend rolls back to the previous Friday first.
    No holiday calendar — synthetic data needs none.
    """
    days: list[date] = []
    current = end
    while current.weekday() >= 5:  # 5=Sat, 6=Sun
        current -= timedelta(days=1)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    days.reverse()
    return days


def _regime_plan(rng: random.Random, total: int) -> list[tuple[str, int]]:
    """Seed-drawn (regime, length) segments covering ``total`` bars.

    Guarantees at least one segment of each of the three regimes; segment
    order is shuffled and lengths drawn from the seeded RNG.
    """
    names = list(REGIMES)
    rng.shuffle(names)
    extra = [rng.choice(list(REGIMES)) for _ in range(3)]
    sequence = names + extra
    # Draw raw weights, normalize to the exact total (last segment absorbs
    # the rounding remainder). Minimum segment length keeps regimes visible.
    weights = [rng.uniform(0.6, 1.4) for _ in sequence]
    scale = total / sum(weights)
    lengths = [max(40, int(w * scale)) for w in weights]
    lengths[-1] = max(40, total - sum(lengths[:-1]))
    # Trim overshoot from the front segments if the minimums overflowed.
    overshoot = sum(lengths) - total
    i = 0
    while overshoot > 0:
        take = min(overshoot, lengths[i] - 40)
        lengths[i] -= take
        overshoot -= take
        i += 1
    return list(zip(sequence, lengths))


def generate_ticker_bars(market: str, ticker: str, final_close: float) -> list[dict]:
    """One ticker's full deterministic bar list, ascending by date.

    Returns ``[{"date", "open", "high", "low", "close", "volume"}, ...]``
    with 2-decimal prices and integer volumes. The final close lands exactly
    on ``final_close`` (whole path rescaled after generation).
    """
    rng = random.Random(f"{GENERATOR_VERSION}:{market}:{ticker}")
    dates = business_days(END_DATE, BARS_PER_TICKER)

    # 1) Raw close path from 1.0 through the regime plan.
    raw_closes: list[float] = []
    level = 1.0
    for regime, length in _regime_plan(rng, BARS_PER_TICKER):
        mu, sigma = REGIMES[regime]
        for _ in range(length):
            level *= math.exp(rng.gauss(mu, sigma))
            raw_closes.append(level)

    # 2) Rescale so the last close equals the market seed price exactly.
    scale = final_close / raw_closes[-1]
    closes = [c * scale for c in raw_closes]

    # 3) OHLC + volume around the close path.
    base_volume = rng.uniform(1e6, 4e7)
    bars: list[dict] = []
    prev_close = closes[0] / math.exp(rng.gauss(0.0, 0.012))
    for d, close in zip(dates, closes):
        sigma = abs(close - prev_close) / prev_close + 0.004
        open_px = prev_close * (1.0 + rng.gauss(0.0, sigma * 0.35))
        body_hi = max(open_px, close)
        body_lo = min(open_px, close)
        high = body_hi * (1.0 + abs(rng.gauss(0.0, sigma * 0.45)))
        low = body_lo * (1.0 - abs(rng.gauss(0.0, sigma * 0.45)))
        volume = int(base_volume * math.exp(rng.gauss(0.0, 0.45)))

        open_r = round(open_px, 2)
        close_r = round(close, 2)
        high_r = max(round(high, 2), open_r, close_r)
        low_r = max(min(round(low, 2), open_r, close_r), 0.01)
        bars.append(
            {
                "date": d.isoformat(),
                "open": open_r,
                "high": high_r,
                "low": low_r,
                "close": close_r,
                "volume": max(volume, 1),
            }
        )
        prev_close = close
    return bars


def generate_market_bars(market: str) -> dict[str, list[dict]]:
    """All tickers for one market: ``{ticker: [bar, ...]}`` (deterministic)."""
    finals = MARKET_FINAL_CLOSES[market]
    return {
        ticker: generate_ticker_bars(market, ticker, final_close)
        for ticker, final_close in finals.items()
    }


def write_market_csvs(market: str, root: Path = OUTPUT_ROOT) -> list[Path]:
    """Write one CSV per ticker under ``root/<market>/``; returns the paths."""
    out_dir = root / market
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for ticker, bars in generate_market_bars(market).items():
        path = out_dir / f"{ticker}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_HEADER)
            for bar in bars:
                writer.writerow(
                    [
                        bar["date"],
                        f"{bar['open']:.2f}",
                        f"{bar['high']:.2f}",
                        f"{bar['low']:.2f}",
                        f"{bar['close']:.2f}",
                        bar["volume"],
                    ]
                )
        written.append(path)
    return written


def main() -> None:
    total = 0
    for market in MARKET_FINAL_CLOSES:
        paths = write_market_csvs(market)
        total += len(paths)
        print(f"{market}: wrote {len(paths)} tickers x {BARS_PER_TICKER} bars")
    print(f"done — {total} CSV files under {OUTPUT_ROOT}")
    print("NOTE: synthetic sample data (fixed seed) — NOT real market prices.")


if __name__ == "__main__":
    main()
