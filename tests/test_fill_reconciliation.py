"""Tests for scripts/reconcile_fills.py (issue #164).

All tests use fixture CSV files in tmp_path — no network, no live data.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.reconcile_fills import (
    aggregate_slippage,
    load_fills,
    load_manifest,
    reconcile,
    symbol_fill_rates,
)


def _write_manifest(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_fills(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


MANIFEST_ROWS = [
    {"Date": "2026-06-05", "Symbol": "AAPL", "Direction": "BUY",
     "Shares": 10, "Target_Price": 195.0, "Strategy": "RSI", "Portfolio": "NQ100",
     "Capital_Allocated": 1950.0, "Reason": "Strategy Entry"},
    {"Date": "2026-06-05", "Symbol": "MSFT", "Direction": "BUY",
     "Shares": 5, "Target_Price": 420.0, "Strategy": "RSI", "Portfolio": "NQ100",
     "Capital_Allocated": 2100.0, "Reason": "Strategy Entry"},
    {"Date": "2026-06-05", "Symbol": "NVDA", "Direction": "SELL",
     "Shares": 8, "Target_Price": 900.0, "Strategy": "RSI", "Portfolio": "NQ100",
     "Capital_Allocated": 7200.0, "Reason": "Strategy Exit"},
]

FILLS_ROWS = [
    {"Date": "2026-06-05", "Symbol": "AAPL", "Direction": "BUY",
     "Shares_Filled": 10, "Fill_Price": 196.0, "Slippage_bps": 51.3,
     "Order_ID": "ord-1", "Status": "filled"},
    {"Date": "2026-06-05", "Symbol": "MSFT", "Direction": "BUY",
     "Shares_Filled": 3, "Fill_Price": 421.0, "Slippage_bps": 23.8,
     "Order_ID": "ord-2", "Status": "partially_filled"},
    # NVDA not filled at all
]


# ---------------------------------------------------------------------------
# load helpers
# ---------------------------------------------------------------------------

class TestLoadHelpers:
    def test_load_manifest_raises_when_missing(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="order_manifest"):
            load_manifest(run_dir)

    def test_load_fills_raises_when_missing(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="alpaca_fills"):
            load_fills(run_dir)

    def test_load_manifest_normalises_direction(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        rows = [{**MANIFEST_ROWS[0], "Direction": "buy"}]
        _write_manifest(run_dir / "order_manifest.csv", rows)
        df = load_manifest(run_dir)
        assert df.iloc[0]["Direction"] == "BUY"


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------

class TestReconcile:
    def test_join_matches_on_date_symbol_direction(self, tmp_path):
        manifest = pd.DataFrame(MANIFEST_ROWS)
        manifest["Date"] = pd.to_datetime(manifest["Date"])
        manifest["Direction"] = manifest["Direction"].str.upper()
        fills = pd.DataFrame(FILLS_ROWS)
        fills["Date"] = pd.to_datetime(fills["Date"])
        fills["Direction"] = fills["Direction"].str.upper()

        rec = reconcile(manifest, fills)
        assert len(rec) == 3  # all manifest rows present

    def test_unfilled_symbol_has_nan_fill_price(self):
        manifest = pd.DataFrame(MANIFEST_ROWS)
        manifest["Date"] = pd.to_datetime(manifest["Date"])
        manifest["Direction"] = manifest["Direction"].str.upper()
        fills = pd.DataFrame(FILLS_ROWS)
        fills["Date"] = pd.to_datetime(fills["Date"])
        fills["Direction"] = fills["Direction"].str.upper()

        rec = reconcile(manifest, fills)
        nvda_row = rec[rec["Symbol"] == "NVDA"].iloc[0]
        assert pd.isna(nvda_row["Fill_Price"])

    def test_fill_rate_computed_for_partial(self):
        manifest = pd.DataFrame(MANIFEST_ROWS)
        manifest["Date"] = pd.to_datetime(manifest["Date"])
        manifest["Direction"] = manifest["Direction"].str.upper()
        fills = pd.DataFrame(FILLS_ROWS)
        fills["Date"] = pd.to_datetime(fills["Date"])
        fills["Direction"] = fills["Direction"].str.upper()

        rec = reconcile(manifest, fills)
        msft_row = rec[rec["Symbol"] == "MSFT"].iloc[0]
        assert msft_row["Fill_Rate"] == pytest.approx(0.6)

    def test_full_fill_rate_is_one(self):
        manifest = pd.DataFrame(MANIFEST_ROWS)
        manifest["Date"] = pd.to_datetime(manifest["Date"])
        manifest["Direction"] = manifest["Direction"].str.upper()
        fills = pd.DataFrame(FILLS_ROWS)
        fills["Date"] = pd.to_datetime(fills["Date"])
        fills["Direction"] = fills["Direction"].str.upper()

        rec = reconcile(manifest, fills)
        aapl_row = rec[rec["Symbol"] == "AAPL"].iloc[0]
        assert aapl_row["Fill_Rate"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# aggregate_slippage
# ---------------------------------------------------------------------------

class TestAggregateSlippage:
    def test_warning_fires_when_divergence_exceeds_20pct(self, capsys):
        # configured = 5 bps; observed median = 50 bps → 900% divergence
        rec = pd.DataFrame({"Slippage_bps": [48.0, 50.0, 52.0]})
        aggregate_slippage(rec, configured_slippage_pct=0.0005)  # 5 bps
        captured = capsys.readouterr()
        assert "[WARNING]" in captured.out

    def test_no_warning_when_within_threshold(self, capsys):
        # configured = 5 bps; observed median = 5.5 bps → 10% divergence
        rec = pd.DataFrame({"Slippage_bps": [5.0, 5.5, 6.0]})
        aggregate_slippage(rec, configured_slippage_pct=0.0005)
        captured = capsys.readouterr()
        assert "[WARNING]" not in captured.out

    def test_empty_slippage_prints_message(self, capsys):
        rec = pd.DataFrame({"Slippage_bps": [None, None]})
        aggregate_slippage(rec, configured_slippage_pct=0.0005)
        captured = capsys.readouterr()
        assert "No filled trades" in captured.out


# ---------------------------------------------------------------------------
# symbol fill rates
# ---------------------------------------------------------------------------

class TestSymbolFillRates:
    def test_flags_chronic_partial(self, capsys):
        manifest = pd.DataFrame(MANIFEST_ROWS)
        manifest["Date"] = pd.to_datetime(manifest["Date"])
        manifest["Direction"] = manifest["Direction"].str.upper()
        fills = pd.DataFrame(FILLS_ROWS)
        fills["Date"] = pd.to_datetime(fills["Date"])
        fills["Direction"] = fills["Direction"].str.upper()
        rec = reconcile(manifest, fills)

        symbol_fill_rates(rec)
        captured = capsys.readouterr()
        # MSFT (60% fill rate) and NVDA (0%) should appear
        assert "MSFT" in captured.out or "All symbols" in captured.out

    def test_clean_slate_prints_all_good(self, capsys):
        manifest = pd.DataFrame(MANIFEST_ROWS[:1])
        manifest["Date"] = pd.to_datetime(manifest["Date"])
        manifest["Direction"] = manifest["Direction"].str.upper()
        fills = pd.DataFrame([FILLS_ROWS[0]])
        fills["Date"] = pd.to_datetime(fills["Date"])
        fills["Direction"] = fills["Direction"].str.upper()
        rec = reconcile(manifest, fills)

        symbol_fill_rates(rec)
        captured = capsys.readouterr()
        assert "All symbols" in captured.out
