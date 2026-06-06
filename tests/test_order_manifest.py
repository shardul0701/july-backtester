"""tests/test_order_manifest.py

Tests for #161 — per-bar order manifest output.
Verifies that run_portfolio_simulation correctly collects manifest rows and
that the manifest row count >= trade log row count.
"""
import os
import sys
from unittest.mock import patch

import pandas as pd
import numpy as np
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from helpers.portfolio_simulations import run_portfolio_simulation

_BASE_CONFIG = {
    "execution_time": "close",
    "slippage_pct": 0.0,
    "commission_per_share": 0.0,
    "max_pct_adv": 0,
    "volume_impact_coeff": 0.0,
    "htb_rate_annual": 0.0,
    "include_delisted": False,
    "noise_injection_pct": 0.0,
    "rolling_sharpe_window": 0,
    "pit_enforce_daily": False,
    "exclude_open_positions": False,
    "entry_priority": "alphabetical",
    "entry_random_seed": 42,
    "export_order_manifest": False,
}

_MANIFEST_COLS = {
    "Date", "Symbol", "Direction", "Shares", "Target_Price", "Capital_Allocated", "Reason",
}


def _price_df(price: float, n: int = 10) -> pd.DataFrame:
    dates = pd.date_range("2020-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": [price] * n,
            "High": [price + 1.0] * n,
            "Low": [price - 1.0] * n,
            "Close": [price] * n,
            "Volume": [1_000_000] * n,
        },
        index=pd.DatetimeIndex(dates),
    )


def _signal(price_df, buy_on: int = 0, sell_on: int = 5) -> pd.Series:
    """Signal: buy on bar buy_on, sell on bar sell_on."""
    s = pd.Series(-1, index=price_df.index)
    s.iloc[buy_on] = 1
    s.iloc[sell_on] = -1
    return s


def _run(portfolio_data, signals, export=False, allocation_pct=0.1, initial_capital=10_000.0):
    cfg = {**_BASE_CONFIG, "export_order_manifest": export}
    with patch("helpers.portfolio_simulations.CONFIG", cfg):
        return run_portfolio_simulation(
            portfolio_data=portfolio_data,
            signals=signals,
            initial_capital=initial_capital,
            allocation_pct=allocation_pct,
            spy_df=None, vix_df=None, tnx_df=None,
            stop_config={"type": "none"},
        )


# ---------------------------------------------------------------------------
# TestManifestDisabled — zero overhead when flag is off
# ---------------------------------------------------------------------------

class TestManifestDisabled:

    def test_no_manifest_key_when_disabled(self):
        """When export_order_manifest=False (default), key is absent from result."""
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"])}
        result = _run(pf, sig, export=False)
        assert "order_manifest" not in result

    def test_trade_log_still_populated_when_disabled(self):
        """Manifest flag has no effect on the trade log."""
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"])}
        result = _run(pf, sig, export=False)
        assert len(result["trade_log"]) >= 1


# ---------------------------------------------------------------------------
# TestManifestEnabled — core behaviour
# ---------------------------------------------------------------------------

class TestManifestEnabled:

    def test_manifest_key_present_when_enabled(self):
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"])}
        result = _run(pf, sig, export=True)
        assert "order_manifest" in result
        assert isinstance(result["order_manifest"], list)

    def test_manifest_row_count_gte_trade_log(self):
        """Manifest rows >= trade log rows (manifest includes skips)."""
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"])}
        result = _run(pf, sig, export=True)
        assert len(result["order_manifest"]) >= len(result["trade_log"])

    def test_manifest_contains_required_columns(self):
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"])}
        result = _run(pf, sig, export=True)
        row_keys = set(result["order_manifest"][0].keys())
        assert _MANIFEST_COLS.issubset(row_keys)

    def test_buy_and_sell_rows_present(self):
        """Both BUY (entry) and SELL (exit) rows appear in the manifest."""
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"], buy_on=0, sell_on=5)}
        result = _run(pf, sig, export=True)
        directions = {r["Direction"] for r in result["order_manifest"]}
        assert "BUY" in directions
        assert "SELL" in directions

    def test_buy_row_has_positive_shares_and_capital(self):
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"])}
        result = _run(pf, sig, export=True)
        buy_rows = [r for r in result["order_manifest"] if r["Direction"] == "BUY" and r["Shares"] > 0]
        assert len(buy_rows) >= 1
        for row in buy_rows:
            assert row["Capital_Allocated"] > 0
            assert row["Target_Price"] > 0

    def test_sell_row_has_positive_shares(self):
        pf = {"AAPL": _price_df(100.0)}
        sig = {"AAPL": _signal(pf["AAPL"], buy_on=0, sell_on=5)}
        result = _run(pf, sig, export=True)
        sell_rows = [r for r in result["order_manifest"] if r["Direction"] == "SELL"]
        assert len(sell_rows) >= 1
        assert all(r["Shares"] > 0 for r in sell_rows)

    def test_skipped_row_has_zero_shares(self):
        """When capital is exhausted, skipped signals appear with Shares=0."""
        pf = {
            "AAPL": _price_df(100.0),
            "MSFT": _price_df(100.0),
        }
        # Both signal buy on bar 0; only enough cash for one (allocation_pct=1.0, IC=100)
        sig = {
            "AAPL": pd.Series(1, index=pf["AAPL"].index),
            "MSFT": pd.Series(1, index=pf["MSFT"].index),
        }
        result = _run(pf, sig, export=True, allocation_pct=1.0, initial_capital=100.0)
        skipped = [r for r in result["order_manifest"]
                   if r["Direction"] == "BUY" and r["Shares"] == 0.0]
        assert len(skipped) >= 1
        assert all("insufficient" in r["Reason"].lower() for r in skipped)

    def test_symbol_field_matches_trade_log(self):
        """Every filled BUY row's Symbol appears in the trade log."""
        pf = {"AAPL": _price_df(100.0), "MSFT": _price_df(200.0)}
        sig = {s: _signal(df) for s, df in pf.items()}
        result = _run(pf, sig, export=True, allocation_pct=0.4, initial_capital=10_000.0)
        trade_symbols = {t["Symbol"] for t in result["trade_log"]}
        filled_buy_symbols = {r["Symbol"] for r in result["order_manifest"]
                              if r["Direction"] == "BUY" and r["Shares"] > 0}
        # every filled buy should have a corresponding trade log entry
        assert filled_buy_symbols.issubset(trade_symbols)

    def test_multi_symbol_manifest_row_count(self):
        """Multi-symbol run: manifest has at least 2 rows (one BUY + one SELL per symbol)."""
        pf = {"AAPL": _price_df(100.0), "MSFT": _price_df(100.0)}
        sig = {s: _signal(df) for s, df in pf.items()}
        result = _run(pf, sig, export=True, allocation_pct=0.4, initial_capital=10_000.0)
        assert len(result["order_manifest"]) >= 4  # 2 BUY + 2 SELL
