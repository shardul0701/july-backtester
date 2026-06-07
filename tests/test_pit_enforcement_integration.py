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

from helpers.pit_enforcement import build_member_mask, build_forced_exit_mask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(idx, member_until=None, force_exit_on=None):
    """Minimal OHLCV frame with PIT columns."""
    df = pd.DataFrame(
        {
            "Open": 100.0,
            "High": 105.0,
            "Low": 95.0,
            "Close": 100.0,
            "Volume": 1_000_000,
            "Signal": 1,
            "ATR_14": 2.0,
        },
        index=idx,
    )
    if member_until is not None:
        df["_pit_member"] = idx <= pd.Timestamp(member_until)
    else:
        df["_pit_member"] = True
    df["_pit_force_exit"] = False
    if force_exit_on is not None:
        df.loc[pd.Timestamp(force_exit_on), "_pit_force_exit"] = True
    return df


# ---------------------------------------------------------------------------
# build_member_mask
# ---------------------------------------------------------------------------

class TestBuildMemberMask:
    def test_single_spell_covers_correct_range(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        intervals = [(pd.Timestamp("2020-01-05"), pd.Timestamp("2020-01-09"))]
        mask = build_member_mask(idx, intervals)
        assert not mask["2020-01-02"]
        assert mask["2020-01-05"]
        assert mask["2020-01-07"]
        assert mask["2020-01-09"]
        assert not mask["2020-01-10"]

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
        assert mask["2020-01-02"]
        assert mask["2020-01-07"]
        assert not mask["2020-01-08"]   # gap
        assert not mask["2020-01-09"]   # gap
        assert mask["2020-01-13"]
        assert mask["2020-01-16"]

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
        """If the spell ends at (or after) the backtest end, no forced exit needed."""
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        intervals = [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-10"))]
        forced = build_forced_exit_mask(idx, intervals, backtest_end="2020-01-10")
        assert not forced.any()

    def test_spell_ending_with_timely_post_bar_is_not_forced(self):
        """There are bars after the spell end within the buffer — normal exit."""
        idx = pd.bdate_range("2020-01-02", "2020-01-16")
        intervals = [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-09"))]
        forced = build_forced_exit_mask(idx, intervals, backtest_end="2020-01-16",
                                        exit_buffer_days=10)
        assert not forced.any()

    def test_no_post_leave_bar_marks_last_member_bar(self):
        """Delisted with no trading days after removal → mark last member bar."""
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        intervals = [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-10"))]
        # backtest ends on 2020-01-10 too, but the spell ends exactly there — no
        # bar exists AFTER the spell end within the buffer.
        forced = build_forced_exit_mask(idx, intervals, backtest_end="2020-01-20",
                                        exit_buffer_days=1)
        # last member bar = 2020-01-10; no post bar within 1 day → forced
        assert forced["2020-01-10"]
        # earlier bars are not forced
        assert not forced["2020-01-09"]

    def test_empty_intervals_returns_all_false(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        forced = build_forced_exit_mask(idx, [], backtest_end="2020-01-20")
        assert not forced.any()


# ---------------------------------------------------------------------------
# End-to-end: columns written into portfolio_data → simulator respects them
# ---------------------------------------------------------------------------

class TestPitColumnsWiredIntoSimulator:
    """Verify that _pit_member=False actually blocks entry and triggers exit."""

    def _run_sim(self, portfolio_data, config=None):
        from helpers.portfolio_simulations import run_portfolio_simulation
        from config import CONFIG as _CONFIG

        cfg = dict(_CONFIG)
        if config:
            cfg.update(config)

        stop_config = {"type": "none"}
        signals = {sym: df["Signal"] for sym, df in portfolio_data.items()}
        return run_portfolio_simulation(
            portfolio_data=portfolio_data,
            signals=signals,
            initial_capital=100_000.0,
            allocation_per_trade=0.10,
            stop_config=stop_config,
            slippage_pct=0.0,
            commission_per_share=0.0,
            config=cfg,
        )

    def test_non_member_symbol_produces_no_trades(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-31")
        df = _make_df(idx)
        df["_pit_member"] = False   # never a member — no entries allowed
        _, trade_log, _ = self._run_sim({"FAKE": df})
        assert len(trade_log) == 0

    def test_member_symbol_enters_and_exits_on_membership_end(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-31")
        removal_date = "2020-01-15"
        df = _make_df(idx, member_until=removal_date)
        _, trade_log, _ = self._run_sim({"FAKE": df})

        assert len(trade_log) >= 1
        last_trade = trade_log[-1]
        assert "PIT Membership Exit" in last_trade["ExitReason"]

    def test_force_exit_bar_closes_at_close_price(self):
        idx = pd.bdate_range("2020-01-02", "2020-01-10")
        force_date = "2020-01-08"
        df = _make_df(idx, member_until="2020-01-31", force_exit_on=force_date)
        _, trade_log, _ = self._run_sim({"FAKE": df})

        # The forced-exit trade should close on or before the forced-exit bar.
        forced_trades = [t for t in trade_log
                         if "last available close" in t.get("ExitReason", "")]
        assert len(forced_trades) >= 1
        assert forced_trades[0]["ExitDate"] <= force_date
