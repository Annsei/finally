"""AI strategy researcher — batch candidate history backtests (D4 §2.2).

One research instruction turns the AI into a strategy researcher: it names
2-4 candidate declarative strategies for a ticker, and this module backtests
every candidate on stored daily-bar history, persists each successful
candidate as a DRAFT strategy plus a Run Library entry, and ranks the
results by a documented robustness score. Everything composes existing
machinery — the P2 strategy DSL/templates, the D1 history backtest, the Run
Library — with no new tables, columns, or env vars.

Provides:
- ``run_research_on_conn(conn, price_cache, *, ...)`` — the chat 'research'
  action handler (D4 §2.1). Does NOT commit: the chat turn owns the single
  commit, exactly like ``create_strategy_on_conn`` and
  ``insert_backtest_run_on_conn`` (both reused here). All failures return
  outcome dicts and never raise on bad input.

Ranking (deterministic, D4 §2.2): ``score = round(total_return_pct -
0.5 * max_drawdown_pct, 2)``. The engine's ``max_drawdown_pct`` is a
non-negative magnitude (``np.max((peaks - eq) / peaks) * 100`` in
``app.backtest``), so a larger drawdown always LOWERS the score. Completed
candidates sort by (traded desc, score desc, win_rate desc, original index
asc) where ``traded = round_trips >= 1``; ``rank`` is 1..n over completed
candidates only. The rank-1 candidate is recommended IFF it traded — an
untraded winner is not a recommendation.

Deploy is NOT part of research: the comparison card deploys via the existing
``PATCH /api/strategies/{id}`` route, on an explicit user click only.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping

from app.backtest import (
    HISTORY_MAX_DAYS,
    HISTORY_MIN_DAYS,
    attach_history_bars,
    normalize_strategy_backtest_config,
    run_backtest,
)
from app.indicators import has_any_exit
from app.market.cache import PriceCache
from app.market.profiles import MarketProfile
from app.market.universe import MarketUniverse
from app.routes.backtest_runs import insert_backtest_run_on_conn
from app.routes.strategies import TEMPLATES_BY_KEY, create_strategy_on_conn

logger = logging.getLogger(__name__)

# D4 §2.1: default research window in TRADING days; the effective value is
# clamped with the history mode's existing bounds (20..750) so the batch
# echo always matches what the engine evaluated.
DEFAULT_RESEARCH_DAYS = 120

# D4 §2.2: batch guard — fewer candidates is not research, more is noise.
MIN_CANDIDATES, MAX_CANDIDATES = 2, 4

# Run Library label prefix for research-produced runs (D4 §2.2 step 6).
RUN_LABEL_PREFIX = "Research: "


def _merge_candidate_config(candidate: Mapping) -> tuple[dict | None, str | None]:
    """Template merge for one candidate — the chat 'create' action's rule.

    ``template`` supplies ``entry``/``exits``/``sizing``; explicit candidate
    fields override (D4 §2.1, mirroring the strategies-create step in
    ``app.routes.chat``). Returns ``({entry, exits, sizing, template}, None)``
    or ``(None, error)`` for an unknown template key.
    """
    raw_template = candidate.get("template")
    template_key = raw_template.strip().lower() if raw_template else None
    template_cfg = TEMPLATES_BY_KEY.get(template_key) if template_key else None
    if template_key and template_cfg is None:
        return None, f"Unknown template '{raw_template}'"
    entry = candidate.get("entry")
    if entry is None:
        entry = template_cfg["entry"] if template_cfg else None
    exits = candidate.get("exits")
    if exits is None:
        exits = template_cfg["exits"] if template_cfg else None
    sizing = candidate.get("sizing")
    if sizing is None:
        sizing = template_cfg["sizing"] if template_cfg else None
    return (
        {"entry": entry, "exits": exits, "sizing": sizing, "template": template_key},
        None,
    )


def _robustness_score(stats: Mapping) -> float:
    """The D4 §2.2 robustness score: return minus half the max drawdown.

    ``max_drawdown_pct`` is the engine's non-negative magnitude, so the
    subtraction guarantees a larger drawdown always lowers the score.
    """
    return round(
        float(stats["total_return_pct"]) - 0.5 * float(stats["max_drawdown_pct"]), 2
    )


def run_research_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    *,
    ticker: str,
    days: int | None,
    candidates: list[Mapping],
    user_id: str,
    universe: MarketUniverse | None = None,
    profile: MarketProfile | None = None,
    market: str = "us",
    commission_bps: float = 0.0,
    starting_cash: float = 10_000.0,
) -> dict:
    """Backtest, persist, and rank one research batch (D4 §2.2). No commit.

    Per candidate, in order — any failure marks THAT candidate failed and
    continues with the next one:

    1. template merge (explicit fields override) + ``has_any_exit`` gate —
       research products must be deployable;
    2. ``normalize_strategy_backtest_config(..., source="history")`` — the
       P2 DSL/name/ticker/lot validation plus the history days clamp;
    3. ``attach_history_bars`` — insufficient stored history fails the
       candidate, never the batch;
    4. ``run_backtest`` — the deterministic D1 daily-bar replay;
    5. ``create_strategy_on_conn`` — a DRAFT owned by ``user_id``;
    6. ``insert_backtest_run_on_conn`` with label ``"Research: " + name``.

    Args:
        conn: Open connection — all writes join the caller's transaction
            (the chat turn owns the single commit; this function never
            commits or rolls back).
        price_cache: Live cache for ticker/anchor validation (history mode
            overwrites the anchor with the last stored close).
        ticker: The researched symbol (normalized to upper case here).
        days: TRADING days of history; None means ``DEFAULT_RESEARCH_DAYS``
            (120), then the history mode's 20..750 clamp applies.
        candidates: 2-4 candidate mappings ``{name, hypothesis?, template?,
            entry?, exits?, sizing?}`` (D4 §2.1 shape).
        user_id: Owner of the created drafts and runs.
        universe: Optional market universe (CN) — threaded to the normalizer
            and creation helper exactly like the chat strategies step.
        profile: Optional market profile (CN) — lot sizing, fees, T+1 ride
            the existing helpers/engine.
        market: ``daily_bars`` partition for ``attach_history_bars``.
        commission_bps: Per-leg commission (main.py's startup value).
        starting_cash: Engine account opening cash (the profile's seed cash
            on a named market — sourced by the caller).

    Returns:
        Batch outcome (D4 §2.2): ``{"status": "completed"|"failed", ticker,
        days, candidates: [...], recommended_strategy_id}`` — "failed" only
        when zero candidates completed. Candidate-count violations return
        the compact guard shape ``{"status": "failed", ticker, error}``.
    """
    ticker = (ticker or "").strip().upper()

    if not MIN_CANDIDATES <= len(candidates) <= MAX_CANDIDATES:
        return {
            "status": "failed",
            "ticker": ticker,
            "error": (
                f"research needs {MIN_CANDIDATES}-{MAX_CANDIDATES} candidates "
                f"(got {len(candidates)})"
            ),
        }

    # Effective window: default 120, then the history mode's existing clamp
    # (the same bounds normalize_strategy_backtest_config applies) so the
    # echoed value always matches the evaluated window.
    days = DEFAULT_RESEARCH_DAYS if days is None else int(days)
    days = max(HISTORY_MIN_DAYS, min(HISTORY_MAX_DAYS, days))

    outcomes: list[dict] = []
    for candidate in candidates:
        name = (candidate.get("name") or "").strip()
        hypothesis = candidate.get("hypothesis")

        def failed(error: str, name: str = name) -> dict:
            return {"name": name, "status": "failed", "error": error}

        # 1) Template merge + the deployability gate (D4 §2.2 step 1).
        merged, error = _merge_candidate_config(candidate)
        if error is not None:
            outcomes.append(failed(error))
            continue
        if not has_any_exit(merged["exits"]):
            outcomes.append(
                failed(
                    "research candidates require at least one exit "
                    "(take_profit_pct, stop_loss_pct, trailing_stop_pct, "
                    "or max_holding_days)"
                )
            )
            continue

        # 2) DSL/name/ticker/lot validation + history-mode config (step 2).
        normalized = normalize_strategy_backtest_config(
            price_cache,
            ticker=ticker,
            entry=merged["entry"],
            exits=merged["exits"],
            sizing=merged["sizing"],
            days=days,
            source="history",
            universe=universe,
            profile=profile,
        )
        if normalized["status"] == "failed":
            outcomes.append(failed(normalized["error"]))
            continue

        # 3) Stored daily-bar window (step 3) — read-only on the shared conn.
        error = attach_history_bars(normalized["config"], conn, market=market)
        if error is not None:
            outcomes.append(failed(error))
            continue

        # 4) Deterministic history replay (step 4).
        result = run_backtest(
            normalized["config"],
            commission_bps=commission_bps,
            starting_cash=starting_cash,
            profile=profile,
        )

        # 5) Persist the candidate as a draft owned by user_id (step 5).
        created = create_strategy_on_conn(
            conn,
            price_cache,
            name=name,
            ticker=ticker,
            entry=merged["entry"],
            exits=merged["exits"],
            sizing=merged["sizing"],
            template=merged["template"],
            user_id=user_id,
            universe=universe,
            profile=profile,
        )
        if created["status"] == "failed":
            outcomes.append(failed(created["error"]))
            continue
        strategy_id = created["strategy"]["id"]

        # 6) Run Library row linked to the new draft (step 6).
        run = insert_backtest_run_on_conn(
            conn,
            user_id=user_id,
            strategy_id=strategy_id,
            label=RUN_LABEL_PREFIX + created["strategy"]["name"],
            result=result,
        )

        stats = result["stats"]
        outcomes.append(
            {
                "name": created["strategy"]["name"],
                "hypothesis": hypothesis,
                "status": "completed",
                "strategy_id": strategy_id,
                "run_id": run["id"],
                "score": _robustness_score(stats),
                "rank": None,  # assigned over completed candidates below
                "traded": stats["round_trips"] >= 1,
                "stats": stats,
            }
        )

    # Ranking (D4 §2.2): completed candidates only, deterministic tie-breaks
    # — traded desc, score desc, win_rate desc (None only occurs untraded;
    # a -1.0 sentinel keeps the comparison total), original index asc.
    completed = [i for i, o in enumerate(outcomes) if o["status"] == "completed"]

    def sort_key(i: int) -> tuple:
        outcome = outcomes[i]
        win_rate = outcome["stats"]["win_rate"]
        return (
            not outcome["traded"],
            -outcome["score"],
            -(win_rate if win_rate is not None else -1.0),
            i,
        )

    ranked = sorted(completed, key=sort_key)
    for rank, i in enumerate(ranked, start=1):
        outcomes[i]["rank"] = rank

    # Recommendation: the rank-1 candidate IFF it traded (an untraded winner
    # is not a recommendation); null when nothing completed.
    recommended_strategy_id = None
    if ranked and outcomes[ranked[0]]["traded"]:
        recommended_strategy_id = outcomes[ranked[0]]["strategy_id"]

    return {
        "status": "completed" if completed else "failed",
        "ticker": ticker,
        "days": days,
        "candidates": outcomes,
        "recommended_strategy_id": recommended_strategy_id,
    }
