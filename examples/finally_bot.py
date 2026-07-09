"""FinAlly example bot -- a tiny SMA-crossover momentum trader.
Polls /api/market/quotes ~1x/s, trades the classic 5/20 moving-average
cross (buy BOT_QTY on golden, liquidate on death) with a FinAlly API key
(Bearer header) minted on /developers. Walkthrough: examples/README.md.
"""
import os
import time
from collections import deque

import requests  # the only non-stdlib dependency: pip install requests

BASE = os.environ.get("FINALLY_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("FINALLY_API_KEY", "")
TICKER = os.environ.get("BOT_TICKER", "NVDA").upper()
QTY = float(os.environ.get("BOT_QTY", "2"))
FAST, SLOW = 5, 20  # short / long moving-average windows, in polls

def sma(prices, n):  # simple moving average of the last n prices
    return sum(prices[-n:]) / n

def crossed(prices, fast, slow):
    """Pure, testable signal: compare the fast/slow SMAs just before and
    after the newest price -> "golden" (fast rose above slow: buy), "death"
    (fast fell to/below slow: sell), or None. No I/O here."""
    prices = list(prices)
    if len(prices) < slow + 1:  # need a full slow window plus the new tick
        return None
    was_above = sma(prices[:-1], fast) > sma(prices[:-1], slow)
    is_above = sma(prices, fast) > sma(prices, slow)
    return None if is_above == was_above else ("golden" if is_above else "death")

def api(method, path, json=None):
    """Bearer-authed call; any error -> print reason, return None (403 = key
    guardrail/frozen, 429 = rate limit: normal pushback, so also back off)."""
    try:
        resp = requests.request(method, BASE + path, json=json, timeout=10,
                                headers={"Authorization": f"Bearer {API_KEY}"})
        if not resp.ok:
            print(f"[bot] {resp.status_code} {method} {path}: {resp.text[:100]}")
            if resp.status_code in (403, 429):
                time.sleep(10)  # back off instead of hammering a closed door
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        # Server not up yet / restarting (transport error), or a 200 that
        # isn't JSON (FINALLY_URL pointing at the wrong server): don't crash,
        # print one line and let the polling loop try again shortly.
        print(f"[bot] {method} {path} failed: {type(exc).__name__}: {exc}")
        time.sleep(3)
        return None

def trade(signal, side, qty):  # instant-fill market order, same endpoint the UI uses
    print(f"[bot] {signal} -> {side} {qty} {TICKER}")
    api("POST", "/api/portfolio/trade", json={"ticker": TICKER, "side": side, "quantity": qty})

def main():
    prices = deque(maxlen=SLOW + 1)  # exactly the history crossed() needs
    print(f"[bot] trading {TICKER} on {BASE} -- Ctrl-C to stop")
    misses = 0  # consecutive polls where TICKER was absent from the quotes
    while True:
        quotes = (api("GET", "/api/market/quotes") or {"quotes": []})["quotes"]
        quote = next((q for q in quotes if q["ticker"] == TICKER), None)
        if quote:
            prices.append(quote["price"])
            misses = 0
        else:
            # Don't spin silently forever: warn on the first miss, then once
            # every ~30 polls while the ticker stays missing.
            misses += 1
            if misses % 30 == 1:
                print(f"[bot] {TICKER} not in /api/market/quotes -- check BOT_TICKER "
                      f"and the market this server trades (e.g. the CN container has no NVDA)")
        signal = crossed(prices, FAST, SLOW) if quote else None
        if signal == "golden":  # momentum turned up: open a position
            trade("golden cross", "buy", QTY)
        elif signal == "death":  # momentum turned down: dump what we hold
            held = sum(p["quantity"] for p in (api("GET", "/api/portfolio/")
                       or {"positions": []})["positions"] if p["ticker"] == TICKER)
            if held > 0:
                trade("death cross", "sell", held)
        time.sleep(1)  # 1 poll/s stays well inside the key's 5 req/s refill

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # Ctrl-C: exit cleanly; positions live on
        print("\n[bot] stopped -- see you in the arena!")
