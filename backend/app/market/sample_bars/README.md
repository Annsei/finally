# Sample daily bars — SYNTHETIC data, not real market prices

The CSV files in `us/` and `cn/` are the FinAlly **sample** history source
(D1 contract §1): deterministic, fixed-seed synthetic OHLCV series generated
by `scripts/gen_sample_bars.py` at the repository root.

**None of this is real market data.** No prices were downloaded from Yahoo,
Eastmoney, or any other vendor, and nothing here may be mistaken for (or used
as) actual historical quotes. The series exist purely so that:

- the app works fully offline (no API keys, no network),
- pytest / jest / E2E / CI never touch an external market-data host,
- the repository redistributes no vendor-licensed data.

Layout: one file per ticker — `us/` covers the 10 default US watchlist
equities, `cn/` the 14 A-share universe codes — each with ~3 years (756
business days, Mon–Fri, no holiday calendar) of `date,open,high,low,close,
volume` rows ending 2026-06-30. Each ticker mixes trend / drawdown / range
regimes and its final close lands exactly on the live simulator's seed price.

Regenerate (rarely needed — the same fixed seed reproduces these files):

```bash
python3 scripts/gen_sample_bars.py
```
