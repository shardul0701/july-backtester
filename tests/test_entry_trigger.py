"""Tests for ``entry_trigger`` -- level (default) vs edge entry semantics.

The engine has always tested the signal *level* (``== 1`` / ``== -2``) to decide
whether a bar is an entry. A strategy that emits a forward-filled *state* series
-- 1 on every bar of a hold, which ``custom_strategies/bull_flag.py`` produces
via ``.replace(0, np.nan).ffill()`` -- therefore makes every hold bar
entry-eligible, and which bar actually gets taken becomes a function of how much
cash happens to be free.

``entry_trigger: "edge"`` restricts entry to the transition INTO the entry state,
matching how the live scanner triggers. Default stays "level" so no existing
result moves.

Covers the two pure helpers and all four engine wiring sites (long/short entry
gate, long/short priority sort key) in helpers/portfolio_simulations.py.
"""

import pandas as pd
import pytest
from unittest.mock import patch

from helpers.config_validator import validate_config
from helpers.portfolio_simulations import (
    CONFIG,
    _build_entry_edge_masks,
    _is_entry_bar,
    run_portfolio_simulation,
)

DATES = pd.to_datetime([
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
    "2024-01-08", "2024-01-09", "2024-01-10",
])


def _sig(values, index=DATES):
    return pd.Series(values, index=index, dtype=float)


# --------------------------------------------------------------------------
# Pure: _build_entry_edge_masks
# --------------------------------------------------------------------------
class TestBuildEntryEdgeMasks:
    def test_only_the_transition_bar_is_an_edge(self):
        m = _build_entry_edge_masks({"X": _sig([0, 1, 1, 1, -1, 0, 0])})
        assert list(m["X"][1]) == [False, True, False, False, False, False, False]

    def test_first_bar_of_the_series_counts_as_an_edge(self):
        # a transition from nothing is still a transition
        m = _build_entry_edge_masks({"X": _sig([1, 1, 1, -1, 0, 0, 0])})
        assert bool(m["X"][1].iloc[0]) is True
        assert not m["X"][1].iloc[1:].any()

    def test_re_entry_after_an_exit_is_a_new_edge(self):
        m = _build_entry_edge_masks({"X": _sig([1, 1, -1, 0, 1, 1, 0])})
        assert list(m["X"][1]) == [True, False, False, False, True, False, False]

    def test_a_series_with_no_entries_has_no_edges(self):
        m = _build_entry_edge_masks({"X": _sig([0, 0, -1, 0, 0, 0, 0])})
        assert not m["X"][1].any()
        assert not m["X"][-2].any()

    def test_long_and_short_masks_are_independent(self):
        m = _build_entry_edge_masks({"X": _sig([1, 1, -1, -2, -2, -1, 0])})
        assert list(m["X"][1]) == [True, False, False, False, False, False, False]
        assert list(m["X"][-2]) == [False, False, False, True, False, False, False]

    def test_every_symbol_gets_a_mask(self):
        m = _build_entry_edge_masks({"A": _sig([1] * 7), "B": _sig([0] * 7)})
        assert set(m) == {"A", "B"}
        assert set(m["A"]) == {1, -2}


# --------------------------------------------------------------------------
# Pure: _is_entry_bar
# --------------------------------------------------------------------------
class TestIsEntryBar:
    def setup_method(self):
        self.signals = {"X": _sig([0, 1, 1, 1, -1, 0, 0])}
        self.masks = _build_entry_edge_masks(self.signals)

    def test_level_mode_accepts_any_bar_holding_the_value(self):
        # edge_masks=None -> the original equality test
        assert _is_entry_bar(self.signals, None, "X", DATES[1], 1) is True
        assert _is_entry_bar(self.signals, None, "X", DATES[3], 1) is True

    def test_edge_mode_accepts_only_the_transition(self):
        assert _is_entry_bar(self.signals, self.masks, "X", DATES[1], 1) is True
        assert _is_entry_bar(self.signals, self.masks, "X", DATES[3], 1) is False

    def test_wrong_value_is_rejected_in_both_modes(self):
        for masks in (None, self.masks):
            assert _is_entry_bar(self.signals, masks, "X", DATES[0], 1) is False
            assert _is_entry_bar(self.signals, masks, "X", DATES[1], -2) is False

    @pytest.mark.parametrize("bad_date", [pd.NaT, pd.Timestamp("2020-06-01")])
    def test_missing_or_nat_date_is_rejected(self, bad_date):
        assert _is_entry_bar(self.signals, self.masks, "X", bad_date, 1) is False
        assert _is_entry_bar(self.signals, None, "X", bad_date, 1) is False

    def test_symbol_absent_from_signals_is_rejected(self):
        assert _is_entry_bar(self.signals, self.masks, "NOPE", DATES[1], 1) is False
        assert _is_entry_bar(self.signals, None, "NOPE", DATES[1], 1) is False


# --------------------------------------------------------------------------
# Engine wiring
# --------------------------------------------------------------------------
def _frame(px=100.0, n=7):
    return pd.DataFrame(
        {"Open": [px] * n, "High": [px * 1.01] * n, "Low": [px * 0.99] * n,
         "Close": [px] * n, "Volume": [50_000_000] * n,
         "ATR_14": [1.0] * n, "RSI_14": [50.0] * n, "SMA_200": [px] * n},
        index=DATES[:n],
    )


def _run(portfolio_data, signals, mode, allocation_pct=0.99):
    with patch.dict(CONFIG, {"entry_trigger": mode}):
        res = run_portfolio_simulation(
            portfolio_data, signals, 100_000.0, allocation_pct,
            None, None, None, {"type": "none"},
        )
    return pd.DataFrame(res["trade_log"])


# AAA holds all the cash for exactly one bar, then releases it. BBB's signal is a
# forward-filled state series whose only real breakout is the first bar.
_STARVED_DATA = {"AAA": _frame(), "BBB": _frame()}
_STARVED_SIGS = {
    "AAA": _sig([1, -1, 0, 0, 0, 0, 0]),
    "BBB": _sig([1, 1, 1, 1, 1, -1, 0]),
}


class TestLevelModeIsUnchanged:
    def test_default_config_is_level(self):
        assert str(CONFIG.get("entry_trigger", "level")).lower() == "level"

    def test_uncontested_entry_is_identical_in_both_modes(self):
        """With cash free on the breakout bar the two modes must agree exactly.

        This is the no-regression invariant: edge mode may only ever remove
        entries the strategy never signalled a fresh breakout for.
        """
        data = {"BBB": _frame()}
        sigs = {"BBB": _sig([1, 1, 1, 1, 1, -1, 0])}
        lvl, edg = _run(data, sigs, "level"), _run(data, sigs, "edge")
        assert len(lvl) == len(edg) == 1
        assert lvl.iloc[0]["EntryDate"] == edg.iloc[0]["EntryDate"]
        assert lvl.iloc[0]["ExitDate"] == edg.iloc[0]["ExitDate"]

    def test_level_mode_enters_a_hold_bar_when_capital_frees_up(self):
        """The defect, pinned. BBB's breakout was 01-02 (fill 01-03), but AAA
        held the cash. Level mode enters on 01-04 off a *hold* bar instead."""
        log = _run(_STARVED_DATA, _STARVED_SIGS, "level")
        assert sorted(log["Symbol"]) == ["AAA", "BBB"]
        bbb = log[log["Symbol"] == "BBB"].iloc[0]
        assert pd.Timestamp(bbb["EntryDate"]) == pd.Timestamp("2024-01-04")


class TestEdgeModePreventsLateEntry:
    def test_edge_mode_skips_rather_than_enters_late(self):
        log = _run(_STARVED_DATA, _STARVED_SIGS, "edge")
        assert sorted(log["Symbol"]) == ["AAA"]

    def test_the_two_modes_differ_only_on_the_starved_symbol(self):
        lvl = _run(_STARVED_DATA, _STARVED_SIGS, "level")
        edg = _run(_STARVED_DATA, _STARVED_SIGS, "edge")
        aaa_l = lvl[lvl["Symbol"] == "AAA"].iloc[0]
        aaa_e = edg[edg["Symbol"] == "AAA"].iloc[0]
        assert aaa_l["EntryDate"] == aaa_e["EntryDate"]
        assert aaa_l["ExitDate"] == aaa_e["ExitDate"]
        assert aaa_l["Profit"] == pytest.approx(aaa_e["Profit"])

    def test_signal_date_priority_queue_also_respects_edge_mode(self):
        """The priority sort key is a second wiring site; if it kept testing the
        level it would rank hold bars ahead of genuine breakouts."""
        with patch.dict(CONFIG, {"entry_priority": "signal_date"}):
            lvl = _run(_STARVED_DATA, _STARVED_SIGS, "level")
            edg = _run(_STARVED_DATA, _STARVED_SIGS, "edge")
        assert sorted(lvl["Symbol"]) == ["AAA", "BBB"]
        assert sorted(edg["Symbol"]) == ["AAA"]


class TestShortSide:
    """A forward-filled SHORT state series churns under level mode.

    ``-2`` satisfies both the entry test (``== -2``) and the exit test
    (``< 0``), so each hold bar covers the position and immediately re-opens it,
    paying a full round trip of commission and slippage every bar. Edge mode
    blocks the re-entry.

    NOTE: the exit-side overlap is a SEPARATE pre-existing issue and is NOT
    fixed here -- edge mode leaves one trade that still covers on the next
    ``-2`` bar rather than holding the short. These tests pin the entry-side
    change only.
    """

    DATA = {"SSS": _frame()}
    SIGS = {"SSS": _sig([-2, -2, -2, -2, -1, 0, 0])}

    def test_level_mode_churns_one_short_into_a_round_trip_per_bar(self):
        log = _run(self.DATA, self.SIGS, "level", allocation_pct=0.5)
        assert len(log) == 4
        assert all(str(t).startswith("Short") for t in log["Trade"])

    def test_edge_mode_opens_the_short_once(self):
        log = _run(self.DATA, self.SIGS, "edge", allocation_pct=0.5)
        assert len(log) == 1
        assert pd.Timestamp(log.iloc[0]["EntryDate"]) == pd.Timestamp("2024-01-03")

    def test_a_clean_short_signal_is_unaffected_by_the_mode(self):
        """One -2 followed by flat bars: nothing to churn, modes must agree."""
        data, sigs = {"SSS": _frame()}, {"SSS": _sig([-2, 0, 0, -1, 0, 0, 0])}
        lvl = _run(data, sigs, "level", allocation_pct=0.5)
        edg = _run(data, sigs, "edge", allocation_pct=0.5)
        assert len(lvl) == len(edg) == 1
        assert lvl.iloc[0]["EntryDate"] == edg.iloc[0]["EntryDate"]
        assert lvl.iloc[0]["ExitDate"] == edg.iloc[0]["ExitDate"]


# --------------------------------------------------------------------------
# Config validation
# --------------------------------------------------------------------------
class TestConfigValidation:
    def test_known_values_produce_no_entry_trigger_warning(self):
        for value in ("level", "edge", "EDGE"):
            warns = validate_config({"entry_trigger": value})
            assert not [w for w in warns if "entry_trigger" in w]

    def test_a_typo_is_caught_at_startup(self):
        """Silently falling back to 'level' would produce exactly the numbers
        the option exists to switch off, while the config file says otherwise."""
        warns = validate_config({"entry_trigger": "edges"})
        assert [w for w in warns if "entry_trigger" in w]

    def test_absent_key_is_not_a_warning(self):
        assert not [w for w in validate_config({}) if "entry_trigger" in w]
