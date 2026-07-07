"""Market universe abstraction for FinAlly (CN-1).

A ``MarketUniverse`` bundles everything the simulator and backtest engine
need to know about one market's tradable set: seed prices, per-ticker GBM
parameters, the sector map, display names, the crypto subset (ticks 24/7,
exempt from the closed-session freeze), and the correlation structure used
to build the simulator's Cholesky matrix.

``US_UNIVERSE`` wraps the existing module constants in ``seed_prices.py``
(same objects, no copies) so injecting it is behavior-identical to the
pre-universe constant lookups. The Chinese A-share universe lives in
``seed_prices_cn.py``; profile selection lives in ``profiles.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    CRYPTO_TICKERS,
    DEFAULT_PARAMS,
    DEFAULT_WATCHLIST,
    INTRA_CRYPTO_CORR,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SECTORS,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)


@dataclass(frozen=True)
class MarketUniverse:
    """One market's tradable set and simulator statistics — pure data."""

    seed_prices: dict[str, float]
    ticker_params: dict[str, dict[str, float]]  # {"sigma", "mu"} per ticker
    default_params: dict[str, float]  # Unknown/user-added tickers
    default_watchlist: list[str]
    sectors: dict[str, str]
    names: dict[str, str]  # Display names (us has none)
    crypto_tickers: frozenset[str]  # Tick 24/7; everything else is equity
    correlation_groups: dict[str, frozenset[str]]  # Group name -> members
    group_correlations: dict[str, float]  # Group name -> intra-group rho
    cross_group_corr: float  # Between groups / unknown tickers
    # Tickers that correlate at ``independent_corr`` with EVERYTHING —
    # checked before group membership (US: TSLA "does its own thing").
    independent_tickers: frozenset[str] = frozenset()
    independent_corr: float | None = None  # None -> cross_group_corr

    def pairwise_correlation(self, t1: str, t2: str) -> float:
        """Correlation between two tickers from the group structure.

        Reproduces ``GBMSimulator._pairwise_correlation`` exactly when
        called on ``US_UNIVERSE``: the independent-ticker check (TSLA)
        comes first, then intra-group membership, then the cross-group
        fallback (which also covers unknown tickers).
        """
        if t1 in self.independent_tickers or t2 in self.independent_tickers:
            if self.independent_corr is not None:
                return self.independent_corr
            return self.cross_group_corr
        for group, members in self.correlation_groups.items():
            if t1 in members and t2 in members:
                return self.group_correlations[group]
        return self.cross_group_corr

    def sector_for(self, ticker: str) -> str:
        """Sector for a ticker; unknown/user-added tickers -> 'other'.

        Input is normalized (strip + uppercase) so callers may pass raw
        user input — same contract as ``seed_prices.sector_for``.
        """
        return self.sectors.get(ticker.strip().upper(), "other")

    def asset_class_for(self, ticker: str) -> str:
        """Asset class for a ticker: 'crypto' or 'equity' (the default).

        Same normalization contract as ``seed_prices.asset_class_for``.
        """
        return "crypto" if ticker.strip().upper() in self.crypto_tickers else "equity"


# The US universe: the existing seed_prices.py constants, verbatim. The
# correlation structure mirrors GBMSimulator._pairwise_correlation — tech
# 0.6, finance 0.5, crypto 0.7, cross 0.3, TSLA independent at 0.3.
US_UNIVERSE = MarketUniverse(
    seed_prices=SEED_PRICES,
    ticker_params=TICKER_PARAMS,
    default_params=DEFAULT_PARAMS,
    default_watchlist=DEFAULT_WATCHLIST,
    sectors=SECTORS,
    names={},
    crypto_tickers=frozenset(CRYPTO_TICKERS),
    correlation_groups={
        name: frozenset(members) for name, members in CORRELATION_GROUPS.items()
    },
    group_correlations={
        "tech": INTRA_TECH_CORR,
        "finance": INTRA_FINANCE_CORR,
        "crypto": INTRA_CRYPTO_CORR,
    },
    cross_group_corr=CROSS_GROUP_CORR,
    independent_tickers=frozenset({"TSLA"}),
    independent_corr=TSLA_CORR,
)
