"""daily_positions_report.py
===========================
Shows current positions (or pending orders) across all 3 paper trading
accounts with entry rationale, stop/target levels, and P&L.

Run any time — before or after market open:
    python scripts/daily_positions_report.py

Before market open: shows pending orders queued for today's open.
After market open:  shows live positions with unrealized P&L.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

# Force UTF-8 output so box-drawing characters work on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

ACCOUNTS = [
    ("B1_VG12_PIT",  os.getenv("B1_APCA_API_KEY_ID"),  os.getenv("B1_APCA_API_SECRET_KEY")),
    ("B2_VG12_PIT",  os.getenv("B2_APCA_API_KEY_ID"),  os.getenv("B2_APCA_API_SECRET_KEY")),
    ("MR_VG12_PIT",  os.getenv("MR_APCA_API_KEY_ID"),  os.getenv("MR_APCA_API_SECRET_KEY")),
]

PAPER_STATE = ROOT / "paper_state"
OUT_BASE    = ROOT / "output" / "rebuild_blended_pit"
MR_SL_PATH  = OUT_BASE / "stock_level" / "MR_VG12_PIT" / "daily_target_book.csv"
SIG_PATH    = ROOT / "MR" / "outputs" / "signals" / "REG2E_signals.parquet"

CLOUD_MODE = False   # set by --cloud flag in main()


# ── Alpaca helpers ─────────────────────────────────────────────────────────────

def alpaca(path: str, key: str, secret: str) -> dict | list:
    req = Request(
        BASE_URL.rstrip("/") + path,
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
    )
    try:
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except HTTPError as e:
        raise RuntimeError(f"Alpaca {path}: {e.code} {e.read().decode()}") from e


# ── Reference data loaders ────────────────────────────────────────────────────

def load_sleeve_map(strategy: str) -> dict[str, str]:
    """Return {ticker: sleeve} from the latest date of the unified target book."""
    if CLOUD_MODE:
        p = PAPER_STATE / "target_books" / f"{strategy}.csv"
    else:
        p = OUT_BASE / strategy / "alpaca_ready" / "daily_target_book_unified.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    sleeve_col = "sleeves" if "sleeves" in df.columns else "sleeve"
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"] == df["date"].max()]
    return dict(zip(df["ticker"].str.upper(), df[sleeve_col]))


def load_mr_state() -> pd.DataFrame:
    """Load the MR stock_level file — gives entry date, stop, target, hold bars."""
    path = (PAPER_STATE / "stock_level" / "MR.csv") if CLOUD_MODE else MR_SL_PATH
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["Date", "EntryDate"])
    return df.sort_values("Date").groupby("Symbol").last().reset_index()


def load_signals() -> pd.DataFrame:
    """Load REG2E signals — gives RSI, 10d-return, rank at each entry."""
    if CLOUD_MODE:
        cloud_sig = PAPER_STATE / "signals" / "REG2E_recent.parquet"
        path = cloud_sig if cloud_sig.exists() else SIG_PATH
    else:
        path = SIG_PATH
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.tz_localize(None).dt.normalize()
    if "signal_date" in df.columns:
        df["signal_date"] = pd.to_datetime(df["signal_date"]).dt.tz_localize(None).dt.normalize()
    return df


def get_signal_for(ticker: str, entry_date: pd.Timestamp,
                   signals: pd.DataFrame) -> dict | None:
    """Find the REG2E signal closest to entry_date for this ticker."""
    if signals.empty:
        return None
    sub = signals[signals["ticker"] == ticker].copy()
    if sub.empty:
        return None
    sub["date_diff"] = (sub["entry_date"] - entry_date).abs()
    row = sub.sort_values("date_diff").iloc[0]
    if row["date_diff"].days > 3:
        return None
    return row.to_dict()


# ── Formatting helpers ─────────────────────────────────────────────────────────

def pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.2f}%"

def usd(v: float) -> str:
    return f"${v:,.2f}"

def bar_label(bars: int) -> str:
    return f"{bars} bar{'s' if bars != 1 else ''}"

def sleeve_short(s: str) -> str:
    return {"C7_Momentum": "C7", "MR_NQ100": "MR", "Defensive_ETF": "DEF"}.get(s, s[:6])

def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"

def green(t):  return color(t, "92")
def red(t):    return color(t, "91")
def yellow(t): return color(t, "93")
def bold(t):   return color(t, "1")
def dim(t):    return color(t, "2")


# ── Single-account report ──────────────────────────────────────────────────────

def report_account(strategy: str, key: str, secret: str,
                   sleeve_map: dict, mr_state: pd.DataFrame,
                   signals: pd.DataFrame) -> None:

    acct    = alpaca("/v2/account",             key, secret)
    raw_pos = alpaca("/v2/positions",           key, secret)
    raw_ord = alpaca("/v2/orders?status=open&limit=100", key, secret)

    equity     = float(acct["equity"])
    cash       = float(acct["cash"])
    day_pl     = float(acct.get("equity", equity)) - float(acct.get("last_equity", equity))
    day_pl_pct = day_pl / float(acct.get("last_equity", equity)) if float(acct.get("last_equity", equity)) else 0

    positions = {p["symbol"].upper(): p for p in raw_pos}
    orders    = {o["symbol"].upper(): o for o in raw_ord}

    deployed_symbols = set(positions) | set(orders)
    deployed_val = sum(float(p["market_value"]) for p in raw_pos)
    deployed_pct = deployed_val / equity if equity else 0

    header_pl = (green(f"+{usd(day_pl)} ({pct(day_pl_pct)})") if day_pl >= 0
                 else red(f"{usd(day_pl)} ({pct(day_pl_pct)})"))

    print(f"\n{'─'*72}")
    print(bold(f"  {strategy}"))
    print(f"  Equity: {bold(usd(equity))}  |  Day P&L: {header_pl}  |  "
          f"Deployed: {usd(deployed_val)} ({deployed_pct:.1%})")
    print(f"{'─'*72}")

    if not deployed_symbols:
        print(dim("  No positions or orders."))
        return

    # Determine order of sleeves to display
    sleeve_order = {"C7_Momentum": 0, "MR_NQ100": 1, "Defensive_ETF": 2}
    rows = []

    for sym in sorted(deployed_symbols):
        sleeve = sleeve_map.get(sym, "Unknown")
        in_position = sym in positions
        status = "OPEN" if in_position else "PENDING"

        if in_position:
            pos       = positions[sym]
            mkt_val   = float(pos["market_value"])
            entry_px  = float(pos["avg_entry_price"])
            cur_px    = float(pos["current_price"])
            unreal_pl = float(pos["unrealized_pl"])
            unreal_pct= float(pos["unrealized_plpc"])
            notional  = mkt_val
        else:
            ord_       = orders[sym]
            notional   = float(ord_["notional"] or 0)
            entry_px   = None
            cur_px     = None
            unreal_pl  = 0.0
            unreal_pct = 0.0
            mkt_val    = notional

        rows.append({
            "sym":         sym,
            "sleeve":      sleeve,
            "sleeve_ord":  sleeve_order.get(sleeve, 9),
            "status":      status,
            "notional":    notional,
            "entry_px":    entry_px,
            "cur_px":      cur_px,
            "unreal_pl":   unreal_pl,
            "unreal_pct":  unreal_pct,
        })

    rows.sort(key=lambda r: (r["sleeve_ord"], r["sym"]))

    # MR state lookup
    mr_by_sym = {} if mr_state.empty else {
        row["Symbol"].upper(): row for _, row in mr_state.iterrows()
    }

    c7_rank = 1
    exit_watches: list[str] = []

    print(f"\n  {'SLV':>4}  {'SYMBOL':>6}  {'NOTIONAL':>10}  {'STATUS':>8}  {'P&L':>12}  RATIONALE / SIGNAL")
    print(f"  {'':>4}  {'':>6}  {'':>10}  {'':>8}  {'':>12}  {'STOP':>10}  {'TARGET':>10}  HOLD")
    print(f"  {'─'*4}  {'─'*6}  {'─'*10}  {'─'*8}  {'─'*12}  {'─'*10}  {'─'*10}  {'─'*4}")

    prev_sleeve = None
    for r in rows:
        sym    = r["sym"]
        sleeve = r["sleeve"]
        status = r["status"]
        # For standalone MR account, all unknown-sleeve positions are MR
        if strategy == "MR_VG12_PIT" and sleeve == "Unknown":
            sleeve = "MR_NQ100"
            r["sleeve"] = sleeve
        sl     = sleeve_short(sleeve)

        if sleeve != prev_sleeve and prev_sleeve is not None:
            print()
        prev_sleeve = sleeve

        # P&L display
        if status == "OPEN" and r["cur_px"] is not None:
            pl_str = (green(f"+{usd(r['unreal_pl'])} ({pct(r['unreal_pct'])})")
                      if r["unreal_pl"] >= 0
                      else red(f"{usd(r['unreal_pl'])} ({pct(r['unreal_pct'])})"))
        else:
            pl_str = dim("pending fill")

        # ── C7 Momentum ──────────────────────────────────────────────────────
        if sleeve == "C7_Momentum":
            rationale = "SP500 Top-7 Momentum — top-7 by trailing 126d return, rebal every 5 days"
            stop_str   = dim("n/a — weekly rebal")
            target_str = dim("n/a — weekly rebal")
            hold_str   = ""

        # ── MR_NQ100 ─────────────────────────────────────────────────────────
        elif sleeve == "MR_NQ100" or strategy == "MR_VG12_PIT":
            mr = mr_by_sym.get(sym)
            if mr is not None:
                entry_dt   = pd.Timestamp(mr["EntryDate"])
                stop_px    = float(mr["StopPrice"])
                target_px  = float(mr["TargetPrice"])
                entry_p    = float(mr["EntryPrice"])
                hold_bars  = int(mr["HoldBars"])
                sig        = get_signal_for(sym, entry_dt, signals)

                rsi_str  = f"RSI {sig['rsi14']:.1f}" if sig else ""
                ret_str  = f"10d {sig['ret_10d']*100:+.1f}%" if sig else ""
                rnk_str  = f"rank #{int(sig['rank_10d'])}/100" if sig else ""
                rat_parts = [x for x in [rsi_str, ret_str, rnk_str] if x]
                rationale  = (f"NQ100 MR | {' | '.join(rat_parts)}"
                              f" | entry {entry_dt.strftime('%b %d')}")

                stop_str   = usd(stop_px)
                target_str = usd(target_px)
                hold_str   = bar_label(hold_bars)

                # Exit watches
                if r["cur_px"] is not None:
                    pct_to_stop   = (r["cur_px"] - stop_px) / stop_px
                    pct_to_target = (target_px - r["cur_px"]) / r["cur_px"]
                    if pct_to_stop < 0.08:
                        exit_watches.append(
                            yellow(f"  ⚠  {sym}: price {usd(r['cur_px'])} is "
                                   f"{pct(pct_to_stop)} from STOP {usd(stop_px)}"))
                    if pct_to_target < 0.06:
                        exit_watches.append(
                            green(f"  ★  {sym}: price {usd(r['cur_px'])} is "
                                  f"{pct(pct_to_target)} from TARGET {usd(target_px)}"))
                    if hold_bars >= 12:
                        exit_watches.append(
                            yellow(f"  ⏰  {sym}: {hold_bars} bars held — "
                                   f"approaching 15-bar max hold"))
            else:
                rationale  = "NQ100 Mean Reversion"
                stop_str   = dim("—")
                target_str = dim("—")
                hold_str   = ""

        # ── Defensive ETF ─────────────────────────────────────────────────────
        else:
            desc = {"IAU": "Gold hedge", "TLT": "Long-term treasury", "UUP": "USD hedge"}
            rationale  = f"Defensive sleeve — {desc.get(sym, 'ETF hedge')}"
            stop_str   = dim("n/a")
            target_str = dim("n/a")
            hold_str   = ""

        print(f"  {sl:>4}  {sym:>6}  {usd(r['notional']):>10}  {status:>8}  "
              f"{pl_str:<22}  {rationale}")
        print(f"  {'':>4}  {'':>6}  {'':>10}  {'':>8}  {'':>22}  "
              f"{stop_str:>10}  {target_str:>10}  {hold_str}")

    # Exit watches
    if exit_watches:
        print(f"\n  {'─'*68}")
        print("  EXIT WATCHES:")
        for w in exit_watches:
            print(w)

    print()


# ── Today's Activity ──────────────────────────────────────────────────────────

def report_todays_activity(strategy: str, key: str, secret: str) -> None:
    """Show orders that filled today (buys = new positions, sells = exits)."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    after = today_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        closed = alpaca(f"/v2/orders?status=closed&after={after}&limit=50&direction=desc",
                        key, secret)
    except Exception:
        return
    if not closed:
        return

    buys  = [o for o in closed if o.get("side") == "buy"  and o.get("status") == "filled"]
    sells = [o for o in closed if o.get("side") == "sell" and o.get("status") == "filled"]
    if not buys and not sells:
        return

    print(f"\n  ── Today's Activity ({strategy}) ──")
    if buys:
        print(f"  {'BOUGHT':>8}  {'SYMBOL':>6}  {'FILLED $':>10}  {'SHARES':>8}  {'@ PRICE':>9}")
        for o in buys:
            notional = float(o.get("filled_notional") or o.get("notional") or 0)
            shares   = float(o.get("filled_qty") or 0)
            price    = notional / shares if shares else 0
            print(f"  {green('BOUGHT'):>8}  {o['symbol']:>6}  "
                  f"{usd(notional):>10}  {shares:>8.1f}  {usd(price):>9}")
    if sells:
        print(f"  {'SOLD':>8}  {'SYMBOL':>6}  {'FILLED $':>10}  {'SHARES':>8}  {'@ PRICE':>9}")
        for o in sells:
            notional = float(o.get("filled_notional") or o.get("notional") or 0)
            shares   = float(o.get("filled_qty") or 0)
            price    = notional / shares if shares else 0
            print(f"  {red('SOLD'):>8}  {o['symbol']:>6}  "
                  f"{usd(notional):>10}  {shares:>8.1f}  {usd(price):>9}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global CLOUD_MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--cloud", action="store_true",
                    help="Cloud mode: read sleeve/MR state from paper_state/ instead of output/.")
    args = ap.parse_args()
    CLOUD_MODE = args.cloud

    today = datetime.now().strftime("%A %b %d, %Y")
    print(f"\n{'═'*72}")
    print(bold(f"  DAILY POSITIONS REPORT — {today}"))
    if CLOUD_MODE:
        print(dim("  (cloud mode — reading from paper_state/)"))
    print(f"{'═'*72}")

    # Load reference data once
    print("  Loading reference data...", end="", flush=True)
    sleeve_maps = {
        "B1_VG12_PIT": load_sleeve_map("B1_VG12_PIT"),
        "B2_VG12_PIT": load_sleeve_map("B2_VG12_PIT"),
        "MR_VG12_PIT": {},   # MR standalone — all positions are MR_NQ100
    }
    mr_state = load_mr_state()
    signals  = load_signals()
    print(" done.")

    for strategy, key, secret in ACCOUNTS:
        if not key or not secret:
            print(f"\n  [{strategy}] No API keys found — skipping.")
            continue
        try:
            # Today's activity (bought/sold at open) — shown before current positions
            report_todays_activity(strategy, key, secret)
            report_account(strategy, key, secret,
                           sleeve_maps[strategy], mr_state, signals)
        except Exception as e:
            print(f"\n  [{strategy}] ERROR: {e}")

    print(f"{'═'*72}\n")


if __name__ == "__main__":
    main()
