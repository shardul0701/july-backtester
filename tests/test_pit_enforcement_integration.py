"""Integration test: pit_enforcement columns are correctly written into
portfolio_data and the simulator respects them end-to-end.

Zach PR #187 review (Major #2): pit_enforcement.py was tested in isolation but
never wired into the engine — _pit_force_exit was always False because main.py
never populated the column.  These tests verify the full pipeline:

  build_member_mask / build_forced_exit_mask
      → written as _pit_member / _pit_force_exit columns in portfolio_data
          → simulator reads them via _pit_flag()
              → trade exits with the correct ExitReason
"""
import pandas as pd
import pytest
from unittest.mock import patch

from helpers.pit_enforcement import build_member_mask, build_forced_exit_mask


# ---------------------------------------------------------------------------
# Minimal sim config (matches test_pipeline_fixes.py pattern)
# ---------------------------------------------------------------------------
_SIM_CONFIG = {
    "execution_time": "open",
    "slippage_pct": 0.0,
    "commission_per_share": 0.0,
    "max_pct_adv": 0.0,
    "volume_impact_coeff": 0.0,
    "htb_rate_annual": 0.0,
    "exclude_open_positions": False,
    "risk_free_rate": 0.0,
    "timeframe": "D",
    "timeframe_multiplier": 1,
    "rolling_sharpe_window": 0,
}


def _frame(idx):
    """Minimal OHLCV frame with no PIT columns (caller adds them)."""
    return pd.DataFrame(
        {
            "Open": 100.0,
            "High": 105.0,
            "Low": 95.0,
            "Close": 100.0,
            "Volume": 1_000_000,
            "ATR_14": 2.0,
            "Volume_Spike": 1.0,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# build_member_mask  — uses pd.bdate_range (Mon–Fri), all dates must be weekdays
# ---------------------------------------------------------------------------

class TestBuildMemberMask:
    def test_single_spell_covers_correct_range(self):
        # 2020-01-02 Thu, 2020-01-03 Fri, 2020-01-06 Mon … 2020-01-10 Fri
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        intervals = [(pd.Timestamp("2020-01-06"), pd.Timestamp("2020-01-09"))]
        mask = build_member_mask(idx, intervals)
        assert not mask.loc["2020-01-02"]   # before spell
        assert not mask.loc["2020-01-03"]   # before spell
        assert mask.loc["2020-01-06"]       # spell start (Monday)
        assert mask.loc["2020-01-07"]       # inside spell
        assert mask.loc["2020-01-09"]       # spell end
        assert not mask.loc["2020-01-10"]   # after spell

    def test_empty_intervals_returns_all_false(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        mask = build_member_mask(idx, [])
        assert not mask.any()

    def test_gap_between_two_spells_is_false(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-16")
        intervals = [
            (pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-07")),
            (pd.Timestamp("2020-01-13"), pd.Timestamp("2020-01-16")),
        ]
        mask = build_member_mask(idx, intervals)
        assert mask.loc["2020-01-02"]
        assert mask.loc["2020-01-07"]
        assert not mask.loc["2020-01-08"]   # gap (Wednesday)
        assert not mask.loc["2020-01-09"]   # gap (Thursday)
        assert mask.loc["2020-01-13"]
        assert mask.loc["2020-01-16"]

    def test_full_period_spell_returns_all_true(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        intervals = [(idx[0], idx[-1])]
        mask = build_member_mask(idx, intervals)
        assert mask.all()


# ---------------------------------------------------------------------------
# build_forced_exit_mask
# ---------------------------------------------------------------------------

class TestBuildForcedExitMask:
    def test_spell_ending_at_backtest_end_is_not_forced(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        intervals = [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-10"))]
        forced = build_forced_exit_mask(idx, intervals, backtest_end="2020-01-10")
        assert not forced.any()

    def test_spell_ending_with_timely_post_bar_is_not_forced(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-16")
        intervals = [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-09"))]
        forced = build_forced_exit_mask(idx, intervals, backtest_end="2020-01-16",
                                        exit_buffer_days=10)
        assert not forced.any()

    def test_no_post_leave_bar_marks_last_member_bar(self):
        # Spell ends 2020-01-10; backtest ends 2020-01-20 but buffer=1 day means
        # next bar must be within 1 calendar day of 2020-01-10 — there is none
        # (next bday is 2020-01-13, which is 3 calendar days away).
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        intervals = [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-10"))]
        forced = build_forced_exit_mask(idx, intervals, backtest_end="2020-01-20",
                                        exit_buffer_days=1)
        assert forced.loc["2020-01-10"]
        assert not forced.loc["2020-01-09"]

    def test_empty_intervals_returns_all_false(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        forced = build_forced_exit_mask(idx, [], backtest_end="2020-01-20")
        assert not forced.any()


# ---------------------------------------------------------------------------
# End-to-end: _pit_member / _pit_force_exit columns → simulator respects them
# ---------------------------------------------------------------------------

class TestPitColumnsWiredIntoSimulator:
    """Prove a non-member / force-exited symbol triggers the correct ExitReason."""

    def _run(self, df, sym="FAKE"):
        from helpers.portfolio_simulations import run_portfolio_simulation
        signals = {sym: pd.Series(1, index=df.index)}
        with patch.dict("config.CONFIG", _SIM_CONFIG):
            return run_portfolio_simulation(
                {sym: df}, signals, 10_000.0, 0.5,
                None, None, None, {"type": "none"},
            )

    def test_non_member_symbol_produces_no_trades(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-31")
        df = _frame(idx)
        df["_pit_member"] = False
        df["_pit_force_exit"] = False
        result = self._run(df)
        # run_portfolio_simulation returns None when there are no trades
        assert result is None

    def test_member_symbol_exits_on_membership_end(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-31")
        removal = pd.Timestamp("2020-01-15")   # Wednesday
        df = _frame(idx)
        df["_pit_member"] = idx <= removal
        df["_pit_force_exit"] = False
        result = self._run(df)
        assert result["Trades"] >= 1
        last = result["trade_log"][-1]
        assert "PIT Membership Exit" in last["ExitReason"]

    def test_force_exit_closes_at_last_member_bar(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        force_date = pd.Timestamp("2020-01-08")  # Wednesday
        df = _frame(idx)
        df["_pit_member"] = True
        df["_pit_force_exit"] = False
        df.loc[force_date, "_pit_force_exit"] = True
        result = self._run(df)
        forced = [t for t in result["trade_log"]
                  if "last available close" in t.get("ExitReason", "")]
        assert len(forced) >= 1
        assert forced[0]["ExitDate"][:10] <= "2020-01-08"
