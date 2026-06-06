"""Alpaca paper-trade adapter — reads an order manifest and submits MOO orders.

Depends on ticket #161 (order manifest output) being complete. Until then,
use alpaca_rebalance_from_vt12_targets.py for target-weight rebalancing.

Usage (dry-run, default):
    python scripts/alpaca_paper_runner.py --run-id 2026-06-05_09-00-00

Usage (submit actual orders):
    python scripts/alpaca_paper_runner.py --run-id 2026-06-05_09-00-00 \\
        --submit --i-understand-submit

Environment variables (set in .env or shell):
    APCA_API_KEY_ID      — Alpaca paper API key ID
    APCA_API_SECRET_KEY  — Alpaca paper API secret key

Alpaca config can be overridden via config.py "alpaca" section.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_BASE_URL = "https://paper-api.alpaca.markets"
DEFAULT_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 10


def _load_config() -> dict:
    try:
        from config import CONFIG
        return CONFIG.get("alpaca", {})
    except ImportError:
        return {}


def _api_key() -> tuple[str, str]:
    cfg = _load_config()
    key_env = cfg.get("api_key_env", "APCA_API_KEY_ID")
    secret_env = cfg.get("secret_key_env", "APCA_API_SECRET_KEY")

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    key = os.environ.get(key_env, "")
    secret = os.environ.get(secret_env, "")
    return key, secret


def alpaca_request(
    method: str,
    path: str,
    body: dict | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    key, secret = _api_key()
    if not key or not secret:
        raise RuntimeError(
            "Missing Alpaca API credentials. Set APCA_API_KEY_ID and "
            "APCA_API_SECRET_KEY in your .env file."
        )
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca {method} {path} → {exc.code}: {detail}") from exc


def find_latest_run() -> Path:
    runs_dir = ROOT / "output" / "runs"
    if not runs_dir.is_dir():
        raise RuntimeError(f"No runs directory found at {runs_dir}")
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("No run folders found in output/runs/")
    return candidates[0]


def load_manifest(run_dir: Path, date: str | None) -> pd.DataFrame:
    manifest_path = run_dir / "order_manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"order_manifest.csv not found in {run_dir}.\n"
            "This file is produced by the backtest engine when "
            "'export_order_manifest': True is set in config.py (ticket #161).\n"
            "For target-weight rebalancing without a manifest, use "
            "scripts/alpaca_rebalance_from_vt12_targets.py instead."
        )
    df = pd.read_csv(manifest_path, parse_dates=["Date"])
    df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()

    if date:
        target_date = pd.Timestamp(date).normalize()
    else:
        target_date = df["Date"].max()

    day = df[df["Date"] == target_date].copy()
    if day.empty:
        raise RuntimeError(
            f"No orders in manifest for {target_date.date()}. "
            f"Available dates: {sorted(df['Date'].dt.date.unique())}"
        )
    print(f"Manifest date: {target_date.date()}  ({len(day)} orders)")
    return day


def build_moo_orders(manifest: pd.DataFrame) -> list[dict]:
    """Convert manifest rows to Alpaca order dicts. Skips cash-skipped rows."""
    orders = []
    skipped = 0
    for _, row in manifest.iterrows():
        reason = str(row.get("Reason", ""))
        if "Skipped" in reason or float(row.get("Shares", 0) or 0) <= 0:
            skipped += 1
            continue
        direction = str(row.get("Direction", "")).upper()
        side = "buy" if direction in ("BUY", "BUY_TO_COVER") else "sell"
        orders.append(
            {
                "symbol": str(row["Symbol"]),
                "side": side,
                "type": "market",
                "time_in_force": "opg",
                "qty": str(int(float(row["Shares"]))),
                "_manifest_row": row.to_dict(),
            }
        )
    if skipped:
        print(f"Skipped {skipped} manifest rows (zero shares / insufficient cash).")
    return orders


def submit_order(order: dict, base_url: str) -> dict:
    body = {k: v for k, v in order.items() if not k.startswith("_")}
    return alpaca_request("POST", "/v2/orders", body=body, base_url=base_url)


def poll_fill(order_id: str, timeout: int, base_url: str) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = alpaca_request("GET", f"/v2/orders/{order_id}", base_url=base_url)
        if status.get("status") in ("filled", "partially_filled", "cancelled", "expired"):
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    # Timeout reached — cancel and return last known state
    try:
        alpaca_request("DELETE", f"/v2/orders/{order_id}", base_url=base_url)
    except RuntimeError:
        pass
    return {"id": order_id, "status": "cancelled", "filled_qty": "0", "filled_avg_price": None}


def build_fill_row(manifest_row: dict, alpaca_resp: dict, submitted: bool) -> dict:
    expected = float(manifest_row.get("Target_Price") or 0)
    filled_qty = float(alpaca_resp.get("filled_qty") or 0)
    fill_price = float(alpaca_resp.get("filled_avg_price") or 0)
    slippage_bps = (
        round((fill_price - expected) / expected * 10_000, 1)
        if expected and fill_price
        else None
    )
    return {
        "Date": manifest_row.get("Date", ""),
        "Symbol": manifest_row.get("Symbol", ""),
        "Direction": manifest_row.get("Direction", ""),
        "Shares_Ordered": manifest_row.get("Shares", 0),
        "Shares_Filled": filled_qty if submitted else 0,
        "Fill_Price": fill_price if submitted else None,
        "Expected_Price": expected,
        "Slippage_bps": slippage_bps if submitted else None,
        "Order_ID": alpaca_resp.get("id", "DRY_RUN"),
        "Status": alpaca_resp.get("status", "dry_run") if submitted else "dry_run",
        "Strategy": manifest_row.get("Strategy", ""),
        "Portfolio": manifest_row.get("Portfolio", ""),
    }


def run(
    run_dir: Path,
    date: str | None,
    submit: bool,
    base_url: str,
    timeout: int,
    output_path: Path | None,
) -> None:
    manifest = load_manifest(run_dir, date)
    orders = build_moo_orders(manifest)

    print(f"Orders to {'submit' if submit else 'simulate (dry-run)'}: {len(orders)}")
    if not orders:
        print("Nothing to do.")
        return

    fill_rows = []
    for order in orders:
        row = order["_manifest_row"]
        sym = order["symbol"]
        side = order["side"]
        qty = order["qty"]
        expected = float(row.get("Target_Price") or 0)

        if not submit:
            print(f"  DRY-RUN  {side.upper():4s}  {sym:10s}  {qty} shares  @ expected ${expected:.2f}")
            fill_rows.append(build_fill_row(row, {"id": "DRY_RUN", "status": "dry_run"}, submitted=False))
            continue

        try:
            resp = submit_order(order, base_url)
            order_id = resp.get("id", "unknown")
            print(f"  SUBMITTED {side.upper():4s}  {sym:10s}  {qty} shares  order_id={order_id}")
            filled = poll_fill(order_id, timeout, base_url)
            fill_rows.append(build_fill_row(row, filled, submitted=True))
        except RuntimeError as exc:
            # Non-tradeable symbol (index, delisted, etc.) — log and continue
            print(f"  SKIPPED  {sym}: {exc}")
            fill_rows.append(
                build_fill_row(
                    row,
                    {"id": "SKIPPED", "status": "skipped", "filled_qty": 0, "filled_avg_price": None},
                    submitted=False,
                )
            )

    fills_df = pd.DataFrame(fill_rows)
    out_path = output_path or (run_dir / "alpaca_fills.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fills_df.to_csv(out_path, index=False)
    print(f"\nWrote fills → {out_path}")

    if submit:
        filled_n = (fills_df["Status"] == "filled").sum()
        partial_n = (fills_df["Status"] == "partially_filled").sum()
        skipped_n = (fills_df["Status"].isin(["skipped", "cancelled"])).sum()
        print(f"Summary: {filled_n} filled | {partial_n} partial | {skipped_n} skipped/cancelled")


def main() -> None:
    cfg = _load_config()
    parser = argparse.ArgumentParser(
        description="Submit today's backtest order manifest to Alpaca paper trading."
    )
    parser.add_argument("--run-id", help="Run folder name under output/runs/. Defaults to latest run.")
    parser.add_argument("--date", help="Filter manifest to this date (YYYY-MM-DD). Defaults to latest.")
    parser.add_argument("--base-url", default=cfg.get("base_url", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=int, default=cfg.get("order_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    parser.add_argument("--output", type=Path, help="Override output path for alpaca_fills.csv.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Print orders without submitting (default).")
    parser.add_argument("--submit", action="store_true", help="Submit orders to Alpaca. Overrides --dry-run.")
    parser.add_argument("--i-understand-submit", action="store_true", help="Required safety confirmation for --submit.")
    args = parser.parse_args()

    submit = args.submit and args.i_understand_submit
    if args.submit and not args.i_understand_submit:
        print("[ERROR] --submit requires --i-understand-submit to prevent accidental order placement.")
        sys.exit(1)

    if args.run_id:
        run_dir = ROOT / "output" / "runs" / args.run_id
        if not run_dir.is_dir():
            print(f"[ERROR] Run directory not found: {run_dir}")
            sys.exit(1)
    else:
        run_dir = find_latest_run()
        print(f"Using latest run: {run_dir.name}")

    run(
        run_dir=run_dir,
        date=args.date,
        submit=submit,
        base_url=args.base_url,
        timeout=args.timeout,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
