"""
Regression tests for issue #314 — ``weekday_overnight_logic`` inverted its own
documented behavior under the engine's default ``execution_time="open"``.

The strategy is meant to be long Monday–Thursday nights and flat Friday night
(avoid weekend risk). The old code emitted ``1`` on Mon–Thu and ``-1`` on Fri.
Under open execution a signal on day N fills at day N+1's OPEN, so Thursday's
``1`` filled Friday open (held over the weekend) and Friday's ``-1`` filled
Monday open — the exact inverse: held every weekend, flat Monday night.

Fix: make the day mapping execution-aware. Under ``open`` the buy days shift one
trading day earlier (Fri/Mon/Tue/Wed → fills Mon–Thu opens; Thursday's ``-1``
fills Friday open, flat over the weekend). Under ``close`` the fill is same-day,
so the original Mon–Thu mapping is correct and preserved.
"""

import numpy as np
import pandas as pd
import pytest

import helpers.indicators as ind
from helpers.portfolio_simulations import run_portfolio_simulation


def _week_df(weeks=3):
    # Clean weekday-only calendar (no holidays) so dayofweek maps cleanly.
    idx = pd.bdate_range("2024-01-01", periods=weeks * 5)  # 2024-01-01 is a Monday
    close = np.linspace(100.0, 100.0 + weeks * 5 - 1, weeks * 5)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": 1e6, "ATR_14": 1.0},
        index=idx,
    )


class TestWeekdayOvernightSignals:
    def test_open_execution_shifts_days_one_earlier(self, monkeypatch):
        import config
        monkeypatch.setitem(config.CONFIG, "execution_time", "open")
        out = ind.weekday_overnight_logic(_week_df(1))
        wd = out.index.dayofweek
        # Buy (1) on Fri(4)/Mon(0)/Tue(1)/Wed(2); sell (-1) on Thu(3).
        for i, day in enumerate(wd):
            expected = 1 if day in (4, 0, 1, 2) else -1
            assert out["Signal"].iloc[i] == expected, f"day {day} wrong"

    def test_close_execution_keeps_mon_thu(self, monkeypatch):
        import config
        monkeypatch.setitem(config.CONFIG, "execution_time", "close")
        out = ind.weekday_overnight_logic(_week_df(1))
        wd = out.index.dayofweek
        for i, day in enumerate(wd):
            expected = 1 if day in (0, 1, 2, 3) else -1
            assert out["Signal"].iloc[i] == expected, f"day {day} wrong"


class TestWeekdayOvernightHoldingEngine:
    def test_open_execution_is_flat_over_the_weekend(self, monkeypatch):
        # The real proof: under open execution every trade must enter on a Monday
        # and exit on a Friday, so the position is never held across a weekend.
        import config
        monkeypatch.setitem(config.CONFIG, "execution_time", "open")
        monkeypatch.setitem(config.CONFIG, "slippage_pct", 0.0)
        monkeypatch.setitem(config.CONFIG, "exclude_open_positions", True)
        df = _week_df(3)
        out = ind.weekday_overnight_logic(df.copy())
        res = run_portfolio_simulation(
            portfolio_data={"SPY": df}, signals={"SPY": out["Signal"]},
            initial_capital=100_000.0, allocation_pct=1.0,
            spy_df=None, vix_df=None, tnx_df=None, stop_config={"type": "none"},
        )
        trades = res["trade_log"] if res else []
        assert trades, "expected at least one completed trade"
        for t in trades:
            entry = pd.Timestamp(t["EntryDate"]); exit_ = pd.Timestamp(t["ExitDate"])
            # Exit ALWAYS on Friday's open => flat across every weekend (the old
            # bug exited on Monday, i.e. held the whole weekend).
            assert exit_.dayofweek == 4, f"exit not on Friday (weekend held): {t['ExitDate']}"
            # Entry is Monday (or Tuesday only for the very first week, which has
            # no prior Friday signal in the data).
            assert entry.dayofweek in (0, 1), f"unexpected entry weekday: {t['EntryDate']}"
            # No trade spans a weekend: same Mon–Fri week (<= 4 calendar days).
            assert (exit_ - entry).days <= 4, f"trade spans a weekend: {t}"
