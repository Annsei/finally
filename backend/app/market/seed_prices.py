"""Seed prices and per-ticker parameters for the market simulator."""

# Realistic starting prices (as of project creation). Includes the 10 default
# watchlist equities plus the crypto set (M3.3) — crypto is NOT in the default
# watchlist but is seeded here so adding BTC/ETH via the watchlist starts from
# a realistic price.
SEED_PRICES: dict[str, float] = {
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
    "BTC": 65000.00,
    "ETH": 3500.00,
}

# The default watchlist seeded into fresh databases (PLAN.md §7): the 10
# equities only. Crypto joins on demand via a watchlist add.
DEFAULT_WATCHLIST: list[str] = [
    "AAPL",
    "GOOGL",
    "MSFT",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
    "JPM",
    "V",
    "NFLX",
]

# Crypto tickers (M3.3): tick 24/7 regardless of the session clock and are
# exempt from session settlement (equity-only DAY-order expiry, prev_close
# roll). Everything else — including unknown user-added tickers — is equity.
CRYPTO_TICKERS: set[str] = {"BTC", "ETH"}


def asset_class_for(ticker: str) -> str:
    """Return the asset class for a ticker: 'crypto' or 'equity'.

    Unknown/user-added tickers default to 'equity'. Input is normalized
    (strip + uppercase) so callers may pass raw user input.
    """
    return "crypto" if ticker.strip().upper() in CRYPTO_TICKERS else "equity"


# Static ticker -> sector map (M3.2b / M3.4). Drives the simulator's
# sector-correlated event bursts and the analytics sector-allocation grouping.
# Distinct from CORRELATION_GROUPS below (the GBM tick-correlation structure,
# where TSLA is deliberately independent): here TSLA is plain tech.
SECTORS: dict[str, str] = {
    "AAPL": "tech",
    "GOOGL": "tech",
    "MSFT": "tech",
    "AMZN": "tech",
    "META": "tech",
    "NVDA": "tech",
    "NFLX": "tech",
    "TSLA": "tech",
    "JPM": "financials",
    "V": "financials",
    "BTC": "crypto",
    "ETH": "crypto",
}


def sector_for(ticker: str) -> str:
    """Return the sector for a ticker; unknown/user-added tickers -> 'other'.

    Input is normalized (strip + uppercase) so callers may pass raw user input.
    """
    return SECTORS.get(ticker.strip().upper(), "other")

# Per-ticker GBM parameters
# sigma: annualized volatility (higher = more price movement)
# mu: annualized drift / expected return
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  # High volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},  # High volatility, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},  # Low volatility (bank)
    "V": {"sigma": 0.17, "mu": 0.04},  # Low volatility (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
    "BTC": {"sigma": 0.75, "mu": 0.10},  # Crypto: ~3x typical equity volatility
    "ETH": {"sigma": 0.85, "mu": 0.08},  # Crypto: ~3x typical equity volatility
}

# Default parameters for tickers not in the list above (dynamically added)
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Correlation groups for the simulator's Cholesky decomposition
# Tickers in the same group have higher intra-group correlation
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
    "crypto": set(CRYPTO_TICKERS),
}

# Correlation coefficients
INTRA_TECH_CORR = 0.6  # Tech stocks move together
INTRA_FINANCE_CORR = 0.5  # Finance stocks move together
INTRA_CRYPTO_CORR = 0.7  # BTC/ETH move together strongly
CROSS_GROUP_CORR = 0.3  # Between sectors / unknown tickers
TSLA_CORR = 0.3  # TSLA does its own thing
