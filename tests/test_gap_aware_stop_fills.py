"""Coverage for `gap_aware_stop_fills` (config SECTION 28).

A stop is an instruction to sell at market once the level trades, not a limit
at the level. When a bar OPENS through the stop, the stop level is a price that
was never available, so booking it overstates the exit. This project therefore
defaults the flag ON in config.py; the engine default is OFF so it still matches
the reference/upstream contract that tests/test_intrabar.py and
tests/test_futures_engine.py pin.

The one carve-out mirrors the sub-bar refinement's: once an ATR trail has ARMED,
the reference mechanic fills at the exact trail level. The trail is seeded from
an intrabar high the price already traversed, so the next open is not a gap
through it -- treating it as one double-counts a same-bar retrace.
"""

import os
import sys

import pandas as pd
import pytest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from helpers.portfolio_simulations import run_portfolio_simulation

_CFG = {
    "slippage_pct": 0.0, "commission_per_share": 0.0, "execution_time": "close",
    "risk_free_rate": 0.05, "htb_rate_annual": 0.0, "volume_impact_coeff": 0.0,
    "max_pct_adv": 0.0, "position_sizing_method": "fixed", "target_risk_per_trade": 0.02,
    "max_portfolio_heat": 1.0, "entry_priority": "alphabetical",
    "exclude_open_positions": False, "include_delisted": False,
    "maintenance_margin_pct": 0.0, "intrabar_resolution": False,
    "instruments": {"default_asset_class": "equity", "overrides": {}},
}
_TRAIL = {"type": "trailing_atr", "stop_mult": 1.0, "trail_mult": 1.0,
          "t1_mult": 1.0, "floor": "breakeven"}


def _df(rows):
    idx = pd.bdate_range("2023-01-02", periods=len(rows))
    idx.name = "Datetime"
    return pd.DataFrame(
        {"Open": [r[0] for r in rows], "High": [r[1] for r in rows],
         "Low": [r[2] for r in rows], "Close": [r[3] for r in rows],
         "ATR_14": [r[4] for r in rows], "Volume": [1e6] * len(rows)}, index=idx)


def _sig(df, pairs):
    s = pd.Series(0, index=df.index, dtype=int)
    for i, v in pairs.items():
        s.iloc[i] = v
    return s


def _run(rows, sig_map, stop_config, gap_aware):
    df = _df(rows)
    cfg = {**_CFG, "gap_aware_stop_fills": gap_aware}
    with patch.dict("config.CONFIG", cfg, clear=False):
        return run_portfolio_simulation(
            portfolio_data={"AAA": df}, signals={"AAA": _sig(df, sig_map)},
            initial_capital=100_000.0, allocation_pct=1.0,
            spy_df=None, vix_df=None, tnx_df=None, stop_config=stop_config)


# Held at 100 with a 5% stop (95); the next bar gaps straight to 90.
_GAP_ROWS = [(100, 100, 100, 100, 2.0), (100, 100, 100, 100, 2.0),
             (90, 91, 88, 89, 2.0)]
_PCT_STOP = {"type": "percentage", "value": 0.05}


class TestGapAwareStopFills:
    def test_on_fills_at_the_gap_open(self):
        t = _run(_GAP_ROWS, {1: 1}, _PCT_STOP, gap_aware=True)["trade_log"][0]
        assert t["ExitReason"].startswith("Stop Loss")
        assert t["ExitPrice"] == pytest.approx(90.0)

    def test_off_fills_at_the_stop_level(self):
        t = _run(_GAP_ROWS, {1: 1}, _PCT_STOP, gap_aware=False)["trade_log"][0]
        assert t["ExitReason"].startswith("Stop Loss")
        assert t["ExitPrice"] == pytest.approx(95.0)

    def test_on_is_never_better_than_off(self):
        on = _run(_GAP_ROWS, {1: 1}, _PCT_STOP, gap_aware=True)["trade_log"][0]
        off = _run(_GAP_ROWS, {1: 1}, _PCT_STOP, gap_aware=False)["trade_log"][0]
        assert on["ExitPrice"] < off["ExitPrice"]

    def test_intact_stop_still_fills_at_the_level(self):
        # Bar opens ABOVE the stop and only trades through it intraday -- not a
        # gap, so the flag must change nothing.
        rows = [(100, 100, 100, 100, 2.0), (100, 100, 100, 100, 2.0),
                (99, 99, 90, 92, 2.0)]
        on = _run(rows, {1: 1}, _PCT_STOP, gap_aware=True)["trade_log"][0]
        off = _run(rows, {1: 1}, _PCT_STOP, gap_aware=False)["trade_log"][0]
        assert on["ExitPrice"] == pytest.approx(95.0)
        assert on["ExitPrice"] == off["ExitPrice"]

    def test_armed_trail_ignores_the_open_even_when_on(self):
        # Arm bar High=110 -> trail seeds at 108; the price already traversed 108
        # on that same bar (Close 103), so the next bar's Open is not a gap
        # through the trail. Must still fill at the exact trail level.
        rows = [(100, 100, 100, 100, 2.0), (100, 100, 100, 100, 2.0),
                (100, 110, 100, 103, 2.0), (103, 104, 102, 102, 2.0),
                (102, 102, 100.5, 100.5, 2.0)]
        t = _run(rows, {1: 1}, _TRAIL, gap_aware=True)["trade_log"][0]
        assert t["ExitPrice"] == pytest.approx(108.0)
        assert t["ExitReason"] == "Stop Loss (trailing_atr)"


class TestConfigDefault:
    def test_engine_default_is_off(self):
        # Absent from CONFIG entirely -> reference parity, not our policy.
        cfg = {k: v for k, v in _CFG.items()}
        with patch.dict("config.CONFIG", cfg, clear=True):
            df = _df(_GAP_ROWS)
            res = run_portfolio_simulation(
                portfolio_data={"AAA": df}, signals={"AAA": _sig(df, {1: 1})},
                initial_capital=100_000.0, allocation_pct=1.0,
                spy_df=None, vix_df=None, tnx_df=None, stop_config=_PCT_STOP)
        assert res["trade_log"][0]["ExitPrice"] == pytest.approx(95.0)

    def test_project_config_turns_it_on(self):
        from config import CONFIG
        assert CONFIG.get("gap_aware_stop_fills") is True
