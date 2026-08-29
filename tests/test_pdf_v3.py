"""tests/test_pdf_v3.py — smoke tests for the v3 (dense 2-page) tearsheet.

Uses a synthetic trades DataFrame (long enough to exercise the 126-day rolling
charts) and tmp_path only — no network, no run directory.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")  # headless; before any pyplot import
import numpy as np
import pandas as pd
import pytest


def _make_trades(n: int = 180, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = datetime(2019, 1, 2)
    rows = []
    eq = 100_000.0
    for i in range(n):
        entry = start + timedelta(days=i * 4)
        exit_ = entry + timedelta(days=int(rng.integers(2, 15)))
        profit = float(rng.normal(80, 700))
        eq += profit
        rows.append({
            "Symbol": rng.choice(["ESM6", "GCJ6"]),
            "Date": entry, "Ex. date": exit_,
            "Price": float(rng.uniform(50, 300)),
            "Ex. Price": float(rng.uniform(50, 300)),
            "Profit": profit, "% Profit": profit / 100_000 * 100,
            "Shares": 1.0, "Win": profit > 0,
            "# bars": float(rng.integers(2, 15)),
            "MAE": float(rng.uniform(-3, 0)), "MFE": float(rng.uniform(0, 4)),
            "Equity": eq, "Cumulative_Profit": eq - 100_000,
            "RMultiple": float(rng.normal(0.1, 1.0)), "InitialRisk": 700.0,
            "Entry YrMo": entry.strftime("%Y-%m"),
        })
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df["Ex. date"] = pd.to_datetime(df["Ex. date"])
    return df


def _report_data(df: pd.DataFrame) -> dict:
    from trade_analyzer import calculations, data_handler
    init = 100_000.0
    de, dr = data_handler.calculate_daily_returns(df, init)
    core = dict(calculations.calculate_core_metrics(df))
    core["sharpe"] = calculations.calculate_sharpe_ratio(dr, 0.05, 252)
    core["sortino"] = calculations.calculate_sortino_ratio(dr, 0.05, 252)
    core["duration_years"] = (df["Ex. date"].max() - df["Date"].min()).days / 365.25
    _, _, _, maxdd = calculations.calculate_equity_drawdown(de)
    _, _, _, _, ddper = calculations.calculate_drawdown_details(
        df["Cumulative_Profit"], df["Ex. date"])
    return {
        "name": "V3 Test Strategy", "run_date": "2026-01-01", "trades_df": df,
        "initial_equity": init, "daily_equity": de, "daily_returns": dr,
        "core_metrics": core, "max_equity_dd_pct": float(maxdd),
        "dd_periods_df": ddper, "risk_free_rate": 0.05, "trading_days": 252,
        "wfa_split_date": None,
    }


class TestV3Tearsheet:
    def test_generates_two_page_pdf(self, tmp_path):
        from trade_analyzer._pdf_v3 import generate_tearsheet_v3
        out = str(tmp_path / "v3.pdf")
        generate_tearsheet_v3(_report_data(_make_trades()), out)
        assert os.path.exists(out), "v3 PDF was not created"
        assert os.path.getsize(out) > 10_000, "v3 PDF suspiciously small"
        pypdf = pytest.importorskip("pypdf")
        assert len(pypdf.PdfReader(out).pages) == 2

    def test_oos_split_in_range_does_not_raise(self, tmp_path):
        from trade_analyzer._pdf_v3 import generate_tearsheet_v3
        rd = _report_data(_make_trades())
        rd["wfa_split_date"] = "2020-06-01"  # inside the synthetic date range
        out = str(tmp_path / "v3_oos.pdf")
        generate_tearsheet_v3(rd, out)
        assert os.path.getsize(out) > 10_000

    def test_empty_trades_does_not_crash(self, tmp_path):
        from trade_analyzer._pdf_v3 import generate_tearsheet_v3
        rd = {
            "name": "Empty", "trades_df": pd.DataFrame(),
            "initial_equity": 100_000.0,
            "daily_equity": pd.Series(dtype=float),
            "daily_returns": pd.Series(dtype=float),
            "core_metrics": {}, "max_equity_dd_pct": 0.0,
            "dd_periods_df": pd.DataFrame(), "risk_free_rate": 0.0,
            "trading_days": 252,
        }
        out = str(tmp_path / "v3_empty.pdf")
        generate_tearsheet_v3(rd, out)  # must not raise
        assert os.path.exists(out)
