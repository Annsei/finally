"""US_PROFILE ≡ None parity (CN-2 §0/§8).

Passing the neutral us profile (lot_size 1, t_plus 0, min_commission/stamp 0,
locale en-US) must be behavior-identical to passing None across every hooked
path — proving the checks are purely field-driven and the default (us) build is
byte-for-byte the pre-CN-2 code.
"""

from __future__ import annotations

import inspect

import pytest

from app.backtest import normalize_backtest_config, run_backtest
from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import US_PROFILE
from app.routes.orders import _place_order_on_conn, place_order_on_conn
from app.routes.portfolio import _execute_trade_on_conn, execute_trade_on_conn
from app.routes.rules import create_rule_on_conn

BPS = 10.0  # exercise the commission path so parity is non-trivial


class TestProfileHookSignatures:
    """CN-2 §0: the ``profile`` hook is added WITHOUT touching the two frozen
    public signatures (execute_trade_on_conn / place_order_on_conn), whose exact
    parameter lists the pre-CN-2 signature regression tests pin byte-for-byte.

    The additive hook lives on the profile-aware siblings (``_execute_trade_
    on_conn`` / ``_place_order_on_conn``); the public names are thin legacy
    wrappers that delegate with ``profile=None``. This test — in a new file,
    never by mutating the pre-CN-2 tests — locks both facts: the public forms
    stay legacy, and the impls carry the optional ``profile`` last, defaulting
    to ``None`` (us ≡ None).
    """

    def test_public_execute_trade_signature_is_frozen(self):
        # Exactly the pre-CN-2 form — no ``profile`` leaked into the public API.
        params = list(inspect.signature(execute_trade_on_conn).parameters.keys())
        assert params == [
            "conn", "price_cache", "ticker", "side", "quantity", "commission_bps",
            "session_clock", "user_id",
        ]

    def test_public_place_order_signature_is_frozen(self):
        params = list(inspect.signature(place_order_on_conn).parameters.keys())
        assert params == [
            "conn", "price_cache", "ticker", "side", "quantity", "kind",
            "limit_price", "stop_price", "time_in_force", "commission_bps",
            "user_id",
        ]

    def test_execute_trade_impl_appends_optional_profile(self):
        sig = inspect.signature(_execute_trade_on_conn)
        params = list(sig.parameters.keys())
        assert params == [
            "conn", "price_cache", "ticker", "side", "quantity", "commission_bps",
            "session_clock", "user_id", "profile",
        ]
        # No market profile by default — the wrapper delegates with None.
        assert sig.parameters["profile"].default is None

    def test_place_order_impl_appends_optional_keyword_only_profile(self):
        sig = inspect.signature(_place_order_on_conn)
        params = list(sig.parameters.keys())
        assert params == [
            "conn", "price_cache", "ticker", "side", "quantity", "kind",
            "limit_price", "stop_price", "time_in_force", "commission_bps",
            "user_id", "profile",
        ]
        assert sig.parameters["profile"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["profile"].default is None

    def test_create_rule_on_conn_appends_optional_keyword_only_profile(self):
        # create_rule_on_conn has no frozen signature test, so the hook lands
        # directly on it (no wrapper needed).
        sig = inspect.signature(create_rule_on_conn)
        assert list(sig.parameters.keys())[-1] == "profile"
        assert sig.parameters["profile"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["profile"].default is None

    def test_public_wrappers_delegate_to_impls_identically(self, tmp_path):
        # The wrapper path (public, profile=None) and the impl path with the
        # neutral us profile must produce identical trade state — the wrapper is
        # a pure pass-through, so us behavior is byte-identical either way.
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        wrap_db = _fresh_db(tmp_path, "wrap.db")
        impl_db = _fresh_db(tmp_path, "impl.db")
        for db, fn, kw in (
            (wrap_db, execute_trade_on_conn, {}),
            (impl_db, _execute_trade_on_conn, {"profile": US_PROFILE}),
        ):
            conn = get_conn(db)
            try:
                fn(conn, cache, "AAPL", "buy", 3, commission_bps=BPS, **kw)
                conn.commit()
            finally:
                conn.close()
        assert _trade_state(wrap_db) == _trade_state(impl_db)


@pytest.fixture
def cache():
    c = PriceCache()
    c.update("AAPL", 190.00)
    return c


def _fresh_db(tmp_path, name):
    db = str(tmp_path / name)
    init_db(db)
    return db


def _trade_state(db_file):
    conn = get_conn(db_file)
    try:
        cash = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id='default'"
        ).fetchone()["cash_balance"]
        positions = [
            (r["ticker"], r["quantity"], r["avg_cost"], r["t1_locked"])
            for r in conn.execute(
                "SELECT ticker, quantity, avg_cost, t1_locked FROM positions "
                "ORDER BY ticker"
            )
        ]
        trades = [
            (r["ticker"], r["side"], r["quantity"], r["price"], r["commission"],
             r["realized_pnl"])
            for r in conn.execute(
                "SELECT ticker, side, quantity, price, commission, realized_pnl "
                "FROM trades ORDER BY rowid"
            )
        ]
        return cash, positions, trades
    finally:
        conn.close()


class TestTradeParity:
    def _run(self, db_file, cache, profile):
        conn = get_conn(db_file)
        try:
            _execute_trade_on_conn(
                conn, cache, "AAPL", "buy", 10, commission_bps=BPS, profile=profile
            )
            _execute_trade_on_conn(
                conn, cache, "AAPL", "sell", 4, commission_bps=BPS, profile=profile
            )
            conn.commit()
        finally:
            conn.close()

    def test_us_equals_none(self, tmp_path, cache):
        none_db = _fresh_db(tmp_path, "none.db")
        us_db = _fresh_db(tmp_path, "us.db")
        self._run(none_db, cache, None)
        self._run(us_db, cache, US_PROFILE)
        assert _trade_state(none_db) == _trade_state(us_db)
        # And crucially, T+1 leaves no lock under us (== None).
        assert _trade_state(us_db)[1][0][3] == 0  # t1_locked == 0


class TestOrderParity:
    def _place_and_snapshot(self, db_file, cache, profile):
        conn = get_conn(db_file)
        try:
            # Marketable limit buy (fills at placement) exercises the fill path.
            _place_order_on_conn(
                conn, cache, ticker="AAPL", side="buy", quantity=10, kind="limit",
                limit_price=200.0, stop_price=None, time_in_force="gtc",
                commission_bps=BPS, profile=profile,
            )
            conn.commit()
        finally:
            conn.close()
        return _trade_state(db_file)

    def test_us_equals_none(self, tmp_path, cache):
        none_db = _fresh_db(tmp_path, "none_o.db")
        us_db = _fresh_db(tmp_path, "us_o.db")
        assert self._place_and_snapshot(none_db, cache, None) == self._place_and_snapshot(
            us_db, cache, US_PROFILE
        )


class TestRuleParity:
    def _create(self, db_file, cache, profile):
        conn = get_conn(db_file)
        try:
            out = create_rule_on_conn(
                conn, cache, ticker="AAPL", trigger_type="price_below", threshold=100.0,
                side="buy", quantity=10, profile=profile,
            )
            conn.commit()
        finally:
            conn.close()
        rule = dict(out["rule"])
        rule.pop("id")
        rule.pop("created_at")
        return out["status"], rule

    def test_us_equals_none(self, tmp_path, cache):
        none_db = _fresh_db(tmp_path, "none_r.db")
        us_db = _fresh_db(tmp_path, "us_r.db")
        assert self._create(none_db, cache, None) == self._create(us_db, cache, US_PROFILE)


class TestBacktestParity:
    def test_us_equals_none(self, cache):
        outcome = normalize_backtest_config(
            cache, ticker="AAPL", trigger_type="day_change_pct_below", threshold=-2,
            quantity=10, take_profit_pct=5, stop_loss_pct=3, days=20, runs=3, seed=7,
        )
        assert outcome["status"] == "ok"
        config = outcome["config"]
        none = run_backtest(config, commission_bps=BPS, end_time=5_000.0, profile=None)
        us = run_backtest(config, commission_bps=BPS, end_time=5_000.0, profile=US_PROFILE)
        assert none["stats"] == us["stats"]
        assert none["trades"] == us["trades"]
        assert none["equity_curve"] == us["equity_curve"]

    def test_us_normalize_equals_none(self, cache):
        # US profile's lot check is a no-op, so normalization is identical.
        base = dict(
            ticker="AAPL", trigger_type="price_below", threshold=100.0, quantity=7,
            days=15, runs=1, seed=3,
        )
        none = normalize_backtest_config(cache, **base, profile=None)
        us = normalize_backtest_config(cache, **base, profile=US_PROFILE)
        assert none == us
