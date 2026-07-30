"""Build the LOCKED AQR-B @ 70% + MR_VG12_PIT @ 30% live target-weight book
for the shared AQR paper account.

This is the forward-test design Zach approved and merged 2026-07-07 (PR #18,
july-backtester-private-strategies): AQR-B (9M formation, quarterly rebal,
top-25, timegate stop 10%/70%) blended 70/30 with the live MR_VG12_PIT sleeve,
live-parity Calmar ~0.89. AQR-A was evaluated and explicitly held back as
research only -- A/B correlation ~0.75 means running both isn't real
diversification, so AQR-A gets NO live capital here. Do not add it back
without a new decision from Zach.

Reads MR_VG12_PIT's own live target book (paper_state/target_books/
MR_VG12_PIT.csv) and rescales its target_weight fractions (of MR's own
capital) into this blend's 30% capital slice -- MR_VG12_PIT itself is not
touched or re-run.

Does NOT place orders. Does NOT touch the broker. Pure target-weight
computation -- same contract as build_s8_alpaca_targets.py /
build_ssd_alpaca_targets.py / the legacy build_aqr_alpaca_targets.py it
replaces.

Usage:
    python scripts/build_aqr_blend_targets.py --account-equity 100000
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "live_aqr_alpaca"
BUILDER = ROOT / "intraday_research" / "build_aqr_targets.py"
HISTORY_DIR = ROOT / "paper_state" / "target_books"
MR_BOOK = ROOT / "paper_state" / "target_books" / "MR_VG12_PIT.csv"

B_SPLIT = 0.70   # AQR-B share of AQR account capital (locked, PR #18)
MR_SPLIT = 0.30  # MR_VG12_PIT share of AQR account capital (locked, PR #18)

_COMPONENT_COLS = ["date", "ticker", "target_weight", "target_value", "sleeves"]


def _append_history(variant: str, df: pd.DataFrame) -> None:
    """Persist this rebalance's AQR-B weights permanently, keyed by date --
    lets a later NAV replay reconstruct the AQR-B leg's own standalone return
    independent of the pooled account and of whatever account-equity was
    passed on any given run (target_weight is a fraction of AQR-B's own
    capital slice, replay-safe across runs)."""
    if df.empty:
        return
    as_of = str(df["date"].iloc[0])
    hist_path = HISTORY_DIR / f"AQR_{variant}_history.csv"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    if hist_path.exists():
        existing = pd.read_csv(hist_path)
        if as_of in existing["date"].astype(str).values:
            existing = existing[existing["date"].astype(str) != as_of]  # replace same-day rerun
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    combined.to_csv(hist_path, index=False)


def _run_variant_b(capital: float, out_path: Path) -> str:
    cmd = [sys.executable, str(BUILDER), "B", "--capital", str(capital), "--out", str(out_path)]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"AQR-B builder failed:\n{p.stdout}\n{p.stderr}")
    return p.stdout


def _read_component(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
        if df.empty:
            return pd.DataFrame(columns=_COMPONENT_COLS)
        return df
    except Exception:
        return pd.DataFrame(columns=_COMPONENT_COLS)


def _read_mr_component(capital: float) -> pd.DataFrame:
    """Rescale MR_VG12_PIT's own target_weight fractions into this blend's
    MR capital slice. MR's own book is not re-run -- it's already produced
    daily by the B1/B2/MR live pipeline (manage_live_portfolio.py Phase 1)."""
    if not MR_BOOK.exists():
        print(f"  WARNING: {MR_BOOK} not found -- MR leg will be empty.")
        return pd.DataFrame(columns=_COMPONENT_COLS)
    mr = pd.read_csv(MR_BOOK)
    if mr.empty:
        return pd.DataFrame(columns=_COMPONENT_COLS)
    rows = []
    for _, row in mr.iterrows():
        w = float(row["target_weight"])
        rows.append({
            "date": row["date"],
            "ticker": row["ticker"],
            "target_weight": round(w, 6),
            "target_value": round(w * capital, 2),
            "sleeves": "MR_VG12_PIT",
        })
    return pd.DataFrame(rows, columns=_COMPONENT_COLS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-equity", type=float, default=100_000.0,
                    help="Baseline capital for scaling target_weight fractions "
                         "(reconcile_and_submit rescales by the real account equity at submit time).")
    ap.add_argument("--output-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap_b = args.account_equity * B_SPLIT
    cap_mr = args.account_equity * MR_SPLIT

    path_b = out_dir / "_AQR_B_component.csv"

    print(f"[1] Building AQR-B component (${cap_b:,.0f}, {B_SPLIT:.0%})...")
    out_b = _run_variant_b(cap_b, path_b)
    print(out_b.strip().splitlines()[-1] if out_b.strip() else "  (no output)")

    print(f"[2] Reading MR_VG12_PIT component (${cap_mr:,.0f}, {MR_SPLIT:.0%})...")
    df_mr = _read_mr_component(cap_mr)
    print(f"  {len(df_mr)} MR names, weight sum {df_mr['target_weight'].sum():.4f}"
          if not df_mr.empty else "  (empty MR book)")

    df_b = _read_component(path_b)
    _append_history("B", df_b)

    as_of = None
    for df in (df_b, df_mr):
        if not df.empty:
            as_of = str(df["date"].iloc[0])
            break

    combined_value: dict[str, float] = {}
    for df in (df_b, df_mr):
        for _, row in df.iterrows():
            sym = str(row["ticker"]).upper()
            combined_value[sym] = combined_value.get(sym, 0.0) + float(row["target_value"])

    total_capital = cap_b + cap_mr
    rows = [
        {"date": as_of, "ticker": sym, "target_weight": round(val / total_capital, 6),
         "target_value": round(val, 2)}
        for sym, val in sorted(combined_value.items(), key=lambda kv: -kv[1])
        if total_capital > 0 and abs(val) > 1e-6
    ]
    book = pd.DataFrame(rows, columns=["date", "ticker", "target_weight", "target_value"])
    book_path = out_dir / "AQR_latest_target_weights.csv"
    book.to_csv(book_path, index=False)

    meta = {
        "as_of": as_of,
        "split_b": B_SPLIT,
        "split_mr": MR_SPLIT,
        "capital_b": cap_b,
        "capital_mr": cap_mr,
        "gross_target_weight": round(float(book["target_weight"].sum()), 4) if not book.empty else 0.0,
        "position_count": int(len(book)),
        "model": "AQR blend = 0.70*AQR-B(9M/Qu/T25, timegate stop 10%/70%) + 0.30*MR_VG12_PIT, "
                 "one shared paper account. AQR-A excluded (held as research, PR #18 2026-07-07).",
    }
    (out_dir / "AQR_run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"\nWrote {book_path}")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
