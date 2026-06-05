"""Fill reconciliation report: backtest order manifest vs Alpaca actual fills.

Usage:
    python scripts/reconcile_fills.py --run-id 2026-06-05_09-00-00

Reads:
    output/runs/<run_id>/order_manifest.csv   (from ticket #161)
    output/runs/<run_id>/alpaca_fills.csv     (from ticket #163)

Writes:
    output/runs/<run_id>/fill_reconciliation.csv

Prints a four-section report to the terminal:
    1. Per-trade diff table
    2. Aggregate slippage summary (with recalibration warning)
    3. Unfilled order audit
    4. Symbol-level fill rate
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RECALIBRATION_THRESHOLD = 0.20  # warn if observed median differs >20% from configured


def _load_config_slippage() -> float:
    try:
        from config import CONFIG
        return float(CONFIG.get("slippage_pct", 0.0005))
    except ImportError:
        return 0.0005


def find_run_dir(run_id: str | None) -> Path:
    runs_dir = ROOT / "output" / "runs"
    if run_id:
        d = runs_dir / run_id
        if not d.is_dir():
            raise FileNotFoundError(f"Run folder not found: {d}")
        return d
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No run folders found in output/runs/")
    return candidates[0]


def load_manifest(run_dir: Path) -> pd.DataFrame:
    path = run_dir / "order_manifest.csv"
    if not path.exists():
        raise FileNotFoundError(f"order_manifest.csv not found in {run_dir}")
    df = pd.read_csv(path, parse_dates=["Date"])
    df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()
    df["Direction"] = df["Direction"].str.upper()
    return df


def load_fills(run_dir: Path) -> pd.DataFrame:
    path = run_dir / "alpaca_fills.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"alpaca_fills.csv not found in {run_dir}.\n"
            "Run scripts/alpaca_paper_runner.py first."
        )
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()
    df["Direction"] = df["Direction"].str.upper()
    return df


def reconcile(manifest: pd.DataFrame, fills: pd.DataFrame) -> pd.DataFrame:
    joined = manifest.merge(
        fills[["Date", "Symbol", "Direction", "Shares_Filled", "Fill_Price",
               "Slippage_bps", "Order_ID", "Status"]],
        on=["Date", "Symbol", "Direction"],
        how="left",
    )
    shares_ordered = pd.to_numeric(joined.get("Shares", joined.get("Shares_Ordered")), errors="coerce")
    shares_filled = pd.to_numeric(joined["Shares_Filled"], errors="coerce").fillna(0)
    joined["Fill_Rate"] = (shares_filled / shares_ordered.replace(0, float("nan"))).round(4)
    return joined


def _col_width(series: pd.Series, header: str) -> int:
    return max(len(header), series.astype(str).str.len().max() if len(series) else 0)


def print_section(title: str, df: pd.DataFrame, cols: list[str]) -> None:
    print(f"\n{'═' * 72}")
    print(f"  {title}")
    print(f"{'═' * 72}")
    if df.empty:
        print("  (no data)")
        return
    sub = df[cols].copy()
    widths = {c: _col_width(sub[c], c) + 2 for c in cols}
    header = "".join(str(c).ljust(widths[c]) for c in cols)
    sep = "".join("─" * widths[c] for c in cols)
    print(header)
    print(sep)
    for _, row in sub.iterrows():
        print("".join(str(row[c]).ljust(widths[c]) for c in cols))


def aggregate_slippage(rec: pd.DataFrame, configured_slippage_pct: float) -> None:
    configured_bps = configured_slippage_pct * 10_000
    slippage = pd.to_numeric(rec["Slippage_bps"], errors="coerce").dropna()
    if slippage.empty:
        print("\nNo filled trades to compute slippage statistics.")
        return
    median_bps = slippage.median()
    p95_bps = slippage.quantile(0.95)
    suggested_bps = slippage.quantile(0.75)

    print(f"\n{'═' * 72}")
    print("  Aggregate Slippage Summary")
    print(f"{'═' * 72}")
    print(f"  Median slippage (bps):     {median_bps:>8.1f}")
    print(f"  P95 slippage (bps):        {p95_bps:>8.1f}")
    print(f"  Configured slippage_pct:   {configured_bps:>8.1f} bps  ({configured_slippage_pct*100:.3f}%)")
    print(f"  Suggested slippage_pct:    {suggested_bps:>8.1f} bps  (p75 of observed)")

    divergence = abs(median_bps - configured_bps) / max(configured_bps, 1e-9)
    if divergence > RECALIBRATION_THRESHOLD:
        suggested_pct = suggested_bps / 10_000
        print(
            f"\n[WARNING] Observed median slippage ({median_bps:.1f} bps) diverges "
            f"{divergence*100:.0f}% from configured ({configured_bps:.1f} bps). "
            f"Consider updating slippage_pct to {suggested_pct:.4f} in config.py."
        )


def symbol_fill_rates(rec: pd.DataFrame) -> None:
    grouped = (
        rec.groupby("Symbol")
        .agg(
            Ordered=("Shares", "sum"),
            Filled=("Shares_Filled", lambda x: pd.to_numeric(x, errors="coerce").fillna(0).sum()),
        )
        .reset_index()
    )
    grouped["Ordered"] = pd.to_numeric(grouped["Ordered"], errors="coerce")
    grouped["Fill_Rate"] = (grouped["Filled"] / grouped["Ordered"].replace(0, float("nan"))).round(4)
    chronic = grouped[grouped["Fill_Rate"] < 0.90].copy()
    print(f"\n{'═' * 72}")
    print("  Symbol Fill Rate (< 90% flagged)")
    print(f"{'═' * 72}")
    if chronic.empty:
        print("  All symbols filled at ≥ 90% — no chronic partial fills.")
        return
    print_section("", chronic, ["Symbol", "Ordered", "Filled", "Fill_Rate"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile backtest manifest against Alpaca fills.")
    parser.add_argument("--run-id", help="Run folder name under output/runs/. Defaults to latest.")
    parser.add_argument("--output", type=Path, help="Override output CSV path.")
    args = parser.parse_args()

    try:
        run_dir = find_run_dir(args.run_id)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print(f"Run: {run_dir.name}")

    try:
        manifest = load_manifest(run_dir)
        fills = load_fills(run_dir)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    rec = reconcile(manifest, fills)
    configured_slippage = _load_config_slippage()

    print_section(
        "Per-Trade Diff",
        rec,
        ["Symbol", "Date", "Direction", "Expected_Price", "Fill_Price",
         "Slippage_bps", "Shares", "Shares_Filled", "Fill_Rate", "Status"],
    )

    aggregate_slippage(rec, configured_slippage)

    unfilled = rec[rec["Shares_Filled"].isna() | (pd.to_numeric(rec["Shares_Filled"], errors="coerce") == 0)].copy()
    print_section(
        "Unfilled Order Audit",
        unfilled,
        ["Symbol", "Date", "Direction", "Shares", "Status", "Strategy"],
    )

    symbol_fill_rates(rec)

    out_path = args.output or (run_dir / "fill_reconciliation.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rec.to_csv(out_path, index=False)
    print(f"\nWrote reconciliation → {out_path}")


if __name__ == "__main__":
    main()
