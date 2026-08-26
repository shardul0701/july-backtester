"""
Regression test for issue #313 — the long entry loop did not check
``short_positions``.

The short entry guard is ``if symbol in positions or symbol in short_positions:
continue``, but the long entry guard was only ``if symbol in positions:
continue``. A signal of ``1`` emitted while a short (from an earlier ``-2``) is
still open therefore entered a long *on top of* the open short: a hedged double
position with doubled commissions/borrow, self-cancelling MTM, and two
overlapping trades the strategy never intended.

Per the documented convention (``-1`` = "exit long or cover short"), a ``1``
while short should be ignored (covers happen on ``<= -1``); it must not stack a
long. The fix mirrors the short-side guard onto the long entry.
"""

import pandas as pd
import pytest

import helpers.portfolio_simulations as ps
from helpers.portfolio_simulations import run_portfolio_simulation


def _frame(n=6, close=100.0):
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": 1_000_000.0, "ATR_14": 1.0},
        index=idx,
    )


def _run(sig_map, monkeypatch):
    monkeypatch.setitem(ps.CONFIG, "execution_time", "close")
    monkeypatch.setitem(ps.CONFIG, "slippage_pct", 0.0)
    df = _frame()
    sig = pd.Series(0, index=df.index)
    for i, v in sig_map.items():
        sig.iloc[i] = v
    return run_portfolio_simulation(
        portfolio_data={"TEST": df}, signals={"TEST": sig},
        initial_capital=100_000.0, allocation_pct=0.5,
        spy_df=None, vix_df=None, tnx_df=None, stop_config={"type": "none"},
    )


class TestLongEntryShortGuard:
    def test_long_signal_while_short_open_does_not_stack_a_long(self, monkeypatch):
        # -2 short at bar1 (never covered); 1 long attempt at bar3 while short
        # is still open. The long must be skipped -> only the short exists.
        res = _run({1: -2, 3: 1}, monkeypatch)
        trades = res["trade_log"] if res else []
        longs = [t for t in trades if str(t["Trade"]).startswith("Long")]
        shorts = [t for t in trades if str(t["Trade"]).startswith("Short")]
        assert longs == [], f"a long was stacked on the open short: {longs}"
        assert len(shorts) == 1

    def test_ignored_long_leaves_the_short_intact(self, monkeypatch):
        # The ignored 1-while-short must NOT cover the short (only <= -1 covers):
        # the short stays open and is marked to market at end of backtest.
        res = _run({1: -2, 3: 1}, monkeypatch)
        trades = res["trade_log"] if res else []
        shorts = [t for t in trades if str(t["Trade"]).startswith("Short")]
        assert len(shorts) == 1
        assert shorts[0]["ExitReason"] == "End of Backtest"

    def test_minus_two_while_long_open_flips_not_stacks(self, monkeypatch):
        # Documented flip: -2 is <= -1, so it exits the open long first (exits run
        # before entries), then opens a short on the now-flat symbol. This is a
        # clean flip, not a simultaneous long+short — no guard change needed here.
        res = _run({1: 1, 3: -2}, monkeypatch)
        trades = res["trade_log"] if res else []
        assert any(str(t["Trade"]).startswith("Long") for t in trades)
        assert any(str(t["Trade"]).startswith("Short") for t in trades)
        # The long must have CLOSED at/after the flip bar, never overlapping the
        # short's whole life: its exit is not "End of Backtest".
        _long = next(t for t in trades if str(t["Trade"]).startswith("Long"))
        assert _long["ExitReason"] != "End of Backtest"

    def test_flip_after_cover_still_allowed(self, monkeypatch):
        # A cover (-1) frees the symbol; a later long (1) may then enter. Exits
        # run before entries, so cover at bar3 then long at bar4 is a clean flip.
        res = _run({1: -2, 3: -1, 4: 1}, monkeypatch)
        trades = res["trade_log"] if res else []
        assert any(str(t["Trade"]).startswith("Short") for t in trades)
        assert any(str(t["Trade"]).startswith("Long") for t in trades)
