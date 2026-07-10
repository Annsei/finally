"""Market sentiment index (P4 §1) — pure math over the shared PriceCache.

Three axes, each 0..100, computed from the cache's full quote snapshot and
its 1-second OHLCV ring buffers:

- ``breadth``    — advancing share: fraction of tickers whose
  ``day_change_percent`` is strictly positive × 100. Exactly-flat tickers
  count in the denominator only (they are neither advancers nor decliners).
- ``volatility`` — mean day amplitude ``(day_high - day_low) / prev_close``
  across the board, linearly mapped so a 2% amplitude reads 100 (clamped).
  Tickers with ``prev_close <= 0`` are skipped; nothing measurable -> 50.
- ``volume``     — flow ratio: total 1-minute-bar volume of the last 10
  minutes over the 10 minutes before that (bars via
  :func:`app.indicators.aggregate_minute_bars`, anchored on the newest
  COMPLETED minute so the value never jitters mid-minute). Ratio 1.0 -> 50,
  >= 2.0 -> 100, <= 0.5 -> 0, linear between; an empty prior segment (or no
  bars at all) is neutral 50.

``score = round(0.5·breadth + 0.25·volatility + 0.25·volume)`` with a
five-tier label at thresholds 0/20/40/60/80:
frozen | cool | neutral | active | hot (label KEYS — the frontend renders
the i18n text). Fewer than 2 tickers in the cache -> every axis 50, score
50, label 'neutral' (not enough of a market to read a mood from).

``sentiment_context_line`` renders the one-line AI context (P4 §1: appended
to the chat portfolio context and the briefs event prompt ONLY when
``sample_size >= 2``; zh locale gets the Chinese line). The SYSTEM_PROMPT
constants are never touched — callers append this to per-request context.
"""

from __future__ import annotations

from app.indicators import aggregate_minute_bars
from app.market.cache import PriceCache
from app.market.models import PriceUpdate

# Day amplitude mapped linearly so 2% reads 100 (clamped above).
VOLATILITY_FULL_SCALE_AMPLITUDE = 0.02
# Volume flow ratio compares the last 10 minutes to the 10 minutes before.
VOLUME_WINDOW_SECONDS = 600
# Neutral axis reading when there is nothing to measure.
NEUTRAL_AXIS = 50.0
# Minimum tickers needed to read a market mood; below this everything is 50.
MIN_SAMPLE_SIZE = 2

# Five-tier label keys at thresholds 0/20/40/60/80 (i18n rendered frontend).
SENTIMENT_LABELS = ("frozen", "cool", "neutral", "active", "hot")
# Chinese label text for the zh AI-context line (P4 §5: 冰点/低迷/中性/活跃/沸腾).
SENTIMENT_LABELS_ZH = {
    "frozen": "冰点",
    "cool": "低迷",
    "neutral": "中性",
    "active": "活跃",
    "hot": "沸腾",
}


def label_for_score(score: int | float) -> str:
    """Five-tier sentiment label for a 0..100 score (thresholds 20/40/60/80)."""
    if score >= 80:
        return "hot"
    if score >= 60:
        return "active"
    if score >= 40:
        return "neutral"
    if score >= 20:
        return "cool"
    return "frozen"


def _breadth_axis(quotes: list[PriceUpdate]) -> float:
    """Advancing-share axis: strictly-positive day change over all tickers.

    Exactly-flat tickers are NOT advancers (numerator) but stay in the
    denominator. Caller guarantees ``quotes`` is non-empty.
    """
    advancers = sum(1 for q in quotes if q.day_change_percent > 0)
    return advancers / len(quotes) * 100.0


def _volatility_axis(quotes: list[PriceUpdate]) -> float:
    """Mean day amplitude axis, 2% amplitude -> 100 (linear, clamped).

    Tickers with a non-positive prev_close are skipped; when nothing is
    measurable the axis reads neutral 50.
    """
    amplitudes = [
        (q.day_high - q.day_low) / q.prev_close
        for q in quotes
        if q.prev_close is not None and q.prev_close > 0
    ]
    if not amplitudes:
        return NEUTRAL_AXIS
    mean_amplitude = sum(amplitudes) / len(amplitudes)
    scaled = mean_amplitude / VOLATILITY_FULL_SCALE_AMPLITUDE * 100.0
    return min(max(scaled, 0.0), 100.0)


def _volume_axis(price_cache: PriceCache, tickers: list[str]) -> float:
    """Volume flow axis from COMPLETED one-minute bars across all tickers.

    The window anchor is the newest completed bar time across the board, so
    the reading is deterministic under injected timestamps and never moves
    mid-minute. Ratio mapping: <=0.5 -> 0, 1.0 -> 50, >=2.0 -> 100, linear
    between; an empty prior segment (nothing to compare against) -> 50.
    """
    bars: list[dict] = []
    for ticker in tickers:
        bars.extend(aggregate_minute_bars(price_cache.get_history(ticker)))
    if not bars:
        return NEUTRAL_AXIS
    anchor = max(bar["time"] for bar in bars)
    recent = sum(
        bar["volume"] for bar in bars if anchor - VOLUME_WINDOW_SECONDS < bar["time"] <= anchor
    )
    prior = sum(
        bar["volume"]
        for bar in bars
        if anchor - 2 * VOLUME_WINDOW_SECONDS < bar["time"] <= anchor - VOLUME_WINDOW_SECONDS
    )
    if prior <= 0:
        return NEUTRAL_AXIS
    ratio = recent / prior
    if ratio >= 2.0:
        return 100.0
    if ratio <= 0.5:
        return 0.0
    if ratio >= 1.0:
        return 50.0 + (ratio - 1.0) * 50.0
    return (ratio - 0.5) / 0.5 * 50.0


def compute_market_sentiment(price_cache: PriceCache) -> dict:
    """Compute the market sentiment index from the live cache (P4 §1).

    Returns::

        {"score": int, "label": str,
         "axes": {"breadth": float, "volatility": float, "volume": float},
         "sample_size": int}

    Axes are rounded to 1dp; the score is the weighted round of the ROUNDED
    axes (0.5/0.25/0.25) so the displayed numbers always reconcile. Fewer
    than :data:`MIN_SAMPLE_SIZE` tickers -> all axes 50, score 50, 'neutral'.
    """
    snapshot = price_cache.get_all()
    sample_size = len(snapshot)
    if sample_size < MIN_SAMPLE_SIZE:
        return {
            "score": 50,
            "label": "neutral",
            "axes": {
                "breadth": NEUTRAL_AXIS,
                "volatility": NEUTRAL_AXIS,
                "volume": NEUTRAL_AXIS,
            },
            "sample_size": sample_size,
        }

    quotes = list(snapshot.values())
    breadth = round(_breadth_axis(quotes), 1)
    volatility = round(_volatility_axis(quotes), 1)
    volume = round(_volume_axis(price_cache, sorted(snapshot)), 1)
    score = int(round(0.5 * breadth + 0.25 * volatility + 0.25 * volume))
    return {
        "score": score,
        "label": label_for_score(score),
        "axes": {"breadth": breadth, "volatility": volatility, "volume": volume},
        "sample_size": sample_size,
    }


def sentiment_context_line(sentiment: dict, zh: bool = False) -> str | None:
    """One-line AI context for a computed sentiment, or None below the sample gate.

    P4 §1: the line is appended to the chat portfolio context and the briefs
    event prompt ONLY when ``sample_size >= 2`` — thin markets add nothing.
    ``zh=True`` renders the Chinese line (label translated); the English
    line is ``Market sentiment: {score}/100 ({label}) — breadth {b},
    volatility {v}, volume {vol}``.
    """
    if sentiment["sample_size"] < MIN_SAMPLE_SIZE:
        return None
    axes = sentiment["axes"]
    score = sentiment["score"]
    label = sentiment["label"]
    breadth = axes["breadth"]
    volatility = axes["volatility"]
    volume = axes["volume"]
    if zh:
        return (
            f"市场情绪：{score}/100（{SENTIMENT_LABELS_ZH[label]}）—— "
            f"涨跌家数 {breadth:g}，波动 {volatility:g}，量能 {volume:g}"
        )
    return (
        f"Market sentiment: {score}/100 ({label}) — "
        f"breadth {breadth:g}, volatility {volatility:g}, volume {volume:g}"
    )
