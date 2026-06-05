"""Tests for scripts/alpaca_paper_runner.py (issue #163).

All Alpaca API calls are mocked — no live network calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.alpaca_paper_runner import (
    build_fill_row,
    build_moo_orders,
    load_manifest,
    poll_fill,
    run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manifest_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def _write_manifest(path: Path, rows: list[dict]) -> None:
    _manifest_df(rows).to_csv(path, index=False)


SAMPLE_ROWS = [
    {
        "Date": "2026-06-05",
        "Symbol": "AAPL",
        "Direction": "BUY",
        "Shares": 10,
        "Target_Price": 195.50,
        "Strategy": "RSI",
        "Portfolio": "NQ100",
        "Capital_Allocated": 1955.0,
        "Reason": "Strategy Entry",
    },
    {
        "Date": "2026-06-05",
        "Symbol": "MSFT",
        "Direction": "SELL",
        "Shares": 5,
        "Target_Price": 420.0,
        "Strategy": "RSI",
        "Portfolio": "NQ100",
        "Capital_Allocated": 2100.0,
        "Reason": "Strategy Exit",
    },
    {
        "Date": "2026-06-05",
        "Symbol": "NVDA",
        "Direction": "BUY",
        "Shares": 0,
        "Target_Price": 900.0,
        "Strategy": "RSI",
        "Portfolio": "NQ100",
        "Capital_Allocated": 0.0,
        "Reason": "Skipped — insufficient cash",
    },
]


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------

class TestLoadManifest:
    def test_loads_correct_date(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        _write_manifest(run_dir / "order_manifest.csv", SAMPLE_ROWS)
        df = load_manifest(run_dir, "2026-06-05")
        assert len(df) == 3

    def test_raises_when_manifest_missing(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="order_manifest.csv"):
            load_manifest(run_dir, None)

    def test_raises_when_date_not_in_manifest(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        _write_manifest(run_dir / "order_manifest.csv", SAMPLE_ROWS)
        with pytest.raises(RuntimeError, match="No orders in manifest"):
            load_manifest(run_dir, "2025-01-01")

    def test_defaults_to_latest_date(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        rows = SAMPLE_ROWS + [{**SAMPLE_ROWS[0], "Date": "2026-06-06"}]
        _write_manifest(run_dir / "order_manifest.csv", rows)
        df = load_manifest(run_dir, None)
        assert (df["Date"] == pd.Timestamp("2026-06-06")).all()


# ---------------------------------------------------------------------------
# build_moo_orders
# ---------------------------------------------------------------------------

class TestBuildMooOrders:
    def test_skips_zero_share_rows(self):
        df = _manifest_df(SAMPLE_ROWS)
        orders = build_moo_orders(df)
        symbols = [o["symbol"] for o in orders]
        assert "NVDA" not in symbols

    def test_skipped_reason_also_excluded(self):
        df = _manifest_df(SAMPLE_ROWS)
        orders = build_moo_orders(df)
        assert len(orders) == 2

    def test_buy_direction_maps_to_buy_side(self):
        df = _manifest_df([SAMPLE_ROWS[0]])
        orders = build_moo_orders(df)
        assert orders[0]["side"] == "buy"

    def test_sell_direction_maps_to_sell_side(self):
        df = _manifest_df([SAMPLE_ROWS[1]])
        orders = build_moo_orders(df)
        assert orders[0]["side"] == "sell"

    def test_time_in_force_is_opg(self):
        df = _manifest_df(SAMPLE_ROWS)
        orders = build_moo_orders(df)
        assert all(o["time_in_force"] == "opg" for o in orders)

    def test_type_is_market(self):
        df = _manifest_df(SAMPLE_ROWS)
        orders = build_moo_orders(df)
        assert all(o["type"] == "market" for o in orders)


# ---------------------------------------------------------------------------
# build_fill_row
# ---------------------------------------------------------------------------

class TestBuildFillRow:
    def test_slippage_bps_positive_when_fill_above_expected(self):
        row = {"Target_Price": 100.0, "Symbol": "AAPL", "Date": "2026-06-05",
               "Direction": "BUY", "Shares": 10, "Strategy": "RSI", "Portfolio": "NQ100"}
        alpaca = {"id": "abc", "status": "filled", "filled_qty": "10", "filled_avg_price": "101.0"}
        result = build_fill_row(row, alpaca, submitted=True)
        assert result["Slippage_bps"] == pytest.approx(100.0)

    def test_slippage_bps_negative_when_fill_below_expected(self):
        row = {"Target_Price": 100.0, "Symbol": "AAPL", "Date": "2026-06-05",
               "Direction": "BUY", "Shares": 10, "Strategy": "RSI", "Portfolio": "NQ100"}
        alpaca = {"id": "abc", "status": "filled", "filled_qty": "10", "filled_avg_price": "99.0"}
        result = build_fill_row(row, alpaca, submitted=True)
        assert result["Slippage_bps"] == pytest.approx(-100.0)

    def test_dry_run_status(self):
        row = {"Target_Price": 100.0, "Symbol": "AAPL", "Date": "2026-06-05",
               "Direction": "BUY", "Shares": 10, "Strategy": "RSI", "Portfolio": "NQ100"}
        result = build_fill_row(row, {"id": "DRY_RUN", "status": "dry_run"}, submitted=False)
        assert result["Status"] == "dry_run"
        assert result["Shares_Filled"] == 0

    def test_zero_expected_price_gives_none_slippage(self):
        row = {"Target_Price": 0, "Symbol": "AAPL", "Date": "2026-06-05",
               "Direction": "BUY", "Shares": 10, "Strategy": "RSI", "Portfolio": "NQ100"}
        alpaca = {"id": "abc", "status": "filled", "filled_qty": "10", "filled_avg_price": "101.0"}
        result = build_fill_row(row, alpaca, submitted=True)
        assert result["Slippage_bps"] is None


# ---------------------------------------------------------------------------
# dry-run end-to-end (no network)
# ---------------------------------------------------------------------------

class TestDryRunEndToEnd:
    def test_dry_run_writes_fills_csv(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        _write_manifest(run_dir / "order_manifest.csv", SAMPLE_ROWS)

        run(
            run_dir=run_dir,
            date="2026-06-05",
            submit=False,
            base_url="https://paper-api.alpaca.markets",
            timeout=300,
            output_path=None,
        )

        fills_path = run_dir / "alpaca_fills.csv"
        assert fills_path.exists()
        df = pd.read_csv(fills_path)
        assert len(df) == 2  # NVDA (zero shares) skipped
        assert (df["Status"] == "dry_run").all()

    def test_dry_run_does_not_call_alpaca_api(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        _write_manifest(run_dir / "order_manifest.csv", SAMPLE_ROWS)

        with patch("scripts.alpaca_paper_runner.alpaca_request") as mock_req:
            run(
                run_dir=run_dir,
                date="2026-06-05",
                submit=False,
                base_url="https://paper-api.alpaca.markets",
                timeout=300,
                output_path=None,
            )
        mock_req.assert_not_called()

    def test_submit_mode_calls_alpaca(self, tmp_path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        _write_manifest(run_dir / "order_manifest.csv", SAMPLE_ROWS[:1])

        alpaca_resp = {
            "id": "order-123",
            "status": "filled",
            "filled_qty": "10",
            "filled_avg_price": "195.75",
        }
        with patch("scripts.alpaca_paper_runner.alpaca_request", return_value=alpaca_resp):
            with patch("scripts.alpaca_paper_runner.poll_fill", return_value=alpaca_resp):
                run(
                    run_dir=run_dir,
                    date="2026-06-05",
                    submit=True,
                    base_url="https://paper-api.alpaca.markets",
                    timeout=300,
                    output_path=None,
                )

        df = pd.read_csv(run_dir / "alpaca_fills.csv")
        assert df.iloc[0]["Order_ID"] == "order-123"
        assert df.iloc[0]["Status"] == "filled"
