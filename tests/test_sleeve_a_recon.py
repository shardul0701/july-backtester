# tests/test_sleeve_a_recon.py
"""
Reconciliation tests for @shardul0701's #234 empirical review of v1.11.0.

Includes his two drop-in reproductions (both FAILED on 39f1865, must PASS now):
  - trailing_atr trails off the running-max HIGH, not the close (BLOCKING #1)
  - short positions get a real stop/target/trail (BLOCKING #2)
Plus short-side coverage for #2b (EoB mark-to-market) and #3 (short margin call),
and the symmetric short trail-off-Low + breakeven cap.
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
    "exclude_open_positions": False, "include_delisted": False, "maintenance_margin_pct": 0.0,
    "instruments": {"default_asset_class": "equity", "futures_initial_margin_pct": 0.10,
                    "futures_commission_per_contract": 0.0, "futures_slippage_ticks": 0.0,
                    "overrides": {}},
}
_TRAIL = {"type": "trailing_atr", "stop_mult": 1.0, "trail_mult": 1.0, "t1_mult": 1.0, "floor": "breakeven"}


def _df(rows):
    idx = pd.bdate_range("2023-01-02", periods=len(rows)); idx.name = "Datetime"
    return pd.DataFrame({"Open": [r[0] for r in rows], "High": [r[1] for r in rows],
                         "Low": [r[2] for r in rows], "Close": [r[3] for r in rows],
                         "Volume": [1e6] * len(rows), "ATR_14": [r[4] for r in rows]}, index=idx)


def _sig(df, p):
    s = pd.Series(0, index=df.index, dtype=int)
    for i, v in p.items():
        s.iloc[i] = v
    return s


def _run(pd_, sg, sc, cfg_over=None):
    with patch.dict("config.CONFIG", {**_CFG, **(cfg_over or {})}, clear=False):
        return run_portfolio_simulation(portfolio_data=pd_, signals=sg, initial_capital=100_000.0,
                                        allocation_pct=1.0, spy_df=None, vix_df=None, tnx_df=None,
                                        stop_config=sc)


# --- Shardul's two reproductions -------------------------------------------
def test_long_trails_off_high_not_close():
    # arm bar High=110 Close=103 -> trail seeds at 108 (High), not 101 (Close)
    df = _df([(100, 100, 100, 100, 2.0), (100, 100, 100, 100, 2.0), (100, 110, 100, 103, 2.0),
              (103, 104, 102, 102, 2.0), (102, 102, 100.5, 100.5, 2.0)])
    t = _run({"AAA": df}, {"AAA": _sig(df, {1: 1})}, _TRAIL)["trade_log"][0]
    assert t["ExitPrice"] == pytest.approx(108.0), f"trailed off Close not High: {t['ExitPrice']}"


def test_short_gets_a_stop():
    # short @100, rips to 140 -> Sleeve A stop at 102 caps loss; engine must not ride to cover
    df = _df([(100, 100, 100, 100, 2.0), (100, 100, 100, 100, 2.0), (100, 120, 100, 120, 2.0),
              (120, 140, 120, 140, 2.0), (140, 140, 140, 140, 2.0)])
    res = _run({"BBB": df}, {"BBB": _sig(df, {1: -2, 4: -1})}, _TRAIL)
    assert res is not None
    st = [x for x in res["trade_log"] if str(x["Trade"]).startswith("Short")][0]
    assert abs(st["ExitPrice"] - 102) < 1e-6, f"short never stopped: {st['ExitPrice']}"
    assert st["ExitReason"] == "Stop Loss (trailing_atr)"
    assert st["InitialRisk"] == pytest.approx(2.0)   # real risk, not 0.0
    assert st["RMultiple"] is not None               # real R-multiple, not None


# --- short-side coverage ----------------------------------------------------
def test_short_trails_off_low_and_caps_at_breakeven():
    # short @100; drops to arm (target 98), rides down, then a low High keeps stop capped at entry.
    df = _df([(100, 100, 100, 100, 2.0), (100, 100, 100, 100, 2.0),
              (98, 98, 90, 97, 2.0),      # arm: Low 90 <= target 98 -> trail = 90+2 = 92
              (95, 103, 95, 102, 2.0)])   # High 103 >= trailed stop 92 -> cover at 92
    res = _run({"BBB": df}, {"BBB": _sig(df, {1: -2})}, _TRAIL)
    st = [x for x in res["trade_log"] if str(x["Trade"]).startswith("Short")][0]
    assert st["ExitReason"] == "Stop Loss (trailing_atr)"
    assert st["ExitPrice"] == pytest.approx(92.0)    # trailed off running-min Low (90+2)


def test_open_short_marked_at_end_of_backtest():
    # #2b: a short still open at the last bar must be logged, not silently dropped.
    df = _df([(100, 100, 100, 100, 2.0), (100, 100, 100, 100, 2.0),
              (99, 100, 98, 98, 2.0), (97, 98, 96, 97, 2.0)])
    res = _run({"BBB": df}, {"BBB": _sig(df, {1: -2})}, {"type": "none"})
    assert res is not None, "open short vanished -> run returned None (#2b regression)"
    st = [x for x in res["trade_log"] if str(x["Trade"]).startswith("Short")]
    assert len(st) == 1 and st[0]["ExitReason"] == "End of Backtest"
    assert st[0]["Profit"] > 0    # short into a falling market -> profit


def test_short_margin_call():
    # #3: futures short squeezed up through maintenance margin -> forced cover.
    df = _df([(4000, 4000, 4000, 4000, 5.0), (4000, 4000, 4000, 4000, 5.0),
              (4000, 4400, 4000, 4400, 5.0)])   # +10% against the short
    res = _run({"MESM6": df}, {"MESM6": _sig(df, {1: -2})}, {"type": "none"},
               cfg_over={"maintenance_margin_pct": 0.05})
    assert res is not None
    st = [x for x in res["trade_log"] if str(x["Trade"]).startswith("Short")][0]
    assert st["ExitReason"] == "Margin Call"
