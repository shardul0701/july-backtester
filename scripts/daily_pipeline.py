"""daily_pipeline.py
====================
Nightly automation: fetch prices → update positions → submit orders.

Run after market close (~5 PM ET, Mon–Fri):
    python scripts/daily_pipeline.py

Dry-run (no orders submitted):
    python scripts/daily_pipeline.py --dry-run

On first run it initialises live state from the frozen June 5 target books.
Each subsequent run appends one day incrementally — no full re-simulation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Constants (must match rebuild_blended_pit.py) ──────────────────────────────
POLYGON_KEY    = os.getenv("POLYGON_API_KEY", "")
MERGED_ROOT    = Path(os.getenv("MERGED_DATA_ROOT",
                                str(ROOT / "data" / "market_data" / "merged")))
SIG_PATH       = ROOT / "MR" / "outputs" / "signals" / "REG2E_signals.parquet"
STATE_DIR      = ROOT / "output" / "live_state"
OUT_BASE       = ROOT / "output" / "rebuild_blended_pit"
MR_SL_PATH     = OUT_BASE / "stock_level" / "MR_VG12_PIT" / "daily_target_book.csv"

# ── Cloud mode (--cloud): use Polygon history + paper_state/ instead of local parquet ──
CLOUD_MODE: bool = False          # set True early in main() when --cloud flag passed
PAPER_STATE   = ROOT / "paper_state"
_CLOUD_HISTORY: dict[str, pd.DataFrame] = {}   # sym → DataFrame[Open,High,Low,Close,Volume]

MR_TARGET_PCT  = 0.12
MR_STOP_PCT    = 0.06
MR_MA_PERIOD   = 20
MR_MAX_HOLD    = 15
MR_ALLOC_PCT   = 0.10      # 10% of equity per MR position, up to 5 positions
MR_MAX_POSITIONS = 5
SLIP_PCT       = 0.0005
COMM_PER_SHARE = 0.002

C7_ALLOC_PCT   = 0.40      # B1: 40% of equity in C7
C7_N_POSITIONS = 7
C7_REBAL_BARS  = 5
C7_LOOKBACK    = 126       # 126-day return for ranking
C7_BULL_MA     = 200       # QQQ 200d MA bull filter

VT12_TARGET    = 0.12      # 12% annualised vol target
VT12_WINDOW    = 20        # 20-day realised vol window
VT12_ANNUALISE = 252 ** 0.5

INITIAL_CAPITAL = 100_000.0

STRATEGY_CONFIG = {
    "B1_VG12_PIT": {"c7_alloc": 0.40, "def_tickers": ["IAU", "TLT"], "def_alloc": 0.15},
    "B2_VG12_PIT": {"c7_alloc": 0.40, "def_tickers": ["TLT"],         "def_alloc": 0.10},
    "MR_VG12_PIT": {"c7_alloc": 0.00, "def_tickers": [],               "def_alloc": 0.00},
}

ALPACA_BASE = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_KEYS = {
    "B1_VG12_PIT": (os.getenv("B1_APCA_API_KEY_ID"),  os.getenv("B1_APCA_API_SECRET_KEY")),
    "B2_VG12_PIT": (os.getenv("B2_APCA_API_KEY_ID"),  os.getenv("B2_APCA_API_SECRET_KEY")),
    "MR_VG12_PIT": (os.getenv("MR_APCA_API_KEY_ID"),  os.getenv("MR_APCA_API_SECRET_KEY")),
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def ep(raw): return raw * (1 + SLIP_PCT)
def xp(raw): return raw * (1 - SLIP_PCT)
def cm(sh):  return abs(sh) * COMM_PER_SHARE


def polygon_get(path: str, params: dict | None = None) -> dict | list:
    url = f"https://api.polygon.io{path}?apiKey={POLYGON_KEY}"
    if params:
        url += "&" + "&".join(f"{k}={v}" for k, v in params.items())
    req = Request(url, headers={"User-Agent": "july-backtester/1.0"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def alpaca_req(method: str, path: str, key: str, secret: str, body=None):
    data = json.dumps(body).encode() if body else None
    req = Request(
        ALPACA_BASE.rstrip("/") + path, data=data, method=method,
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                 "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        raise RuntimeError(f"Alpaca {method} {path}: {e.code} {e.read().decode()}") from e


# ── Polygon price fetcher ──────────────────────────────────────────────────────

def fetch_prices(tickers: list[str], date_str: str) -> dict[str, dict]:
    """Fetch OHLCV for tickers on a given date from Polygon grouped endpoint."""
    print(f"  Fetching {len(tickers)} tickers for {date_str} from Polygon...", end="", flush=True)
    result = {}
    try:
        resp = polygon_get(f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}")
        if resp.get("resultsCount", 0) == 0:
            print(f" no data (market closed or holiday).")
            return {}
        ticker_set = set(t.upper() for t in tickers)
        for bar in resp.get("results", []):
            sym = bar["T"].upper()
            if sym in ticker_set:
                result[sym] = {
                    "open": bar["o"], "high": bar["h"],
                    "low": bar["l"], "close": bar["c"],
                    "volume": bar.get("v", 0),
                }
        print(f" got {len(result)}/{len(tickers)} tickers.")
    except Exception as e:
        print(f" ERROR: {e}")
    return result


def last_trading_day(before: str | None = None) -> str:
    """Return the most recent trading day (YYYY-MM-DD) by checking Polygon."""
    d = datetime.now() if before is None else datetime.strptime(before, "%Y-%m-%d")
    for _ in range(10):
        d -= timedelta(days=1)
        ds = d.strftime("%Y-%m-%d")
        try:
            resp = polygon_get(f"/v2/aggs/ticker/SPY/range/1/day/{ds}/{ds}")
            if resp.get("resultsCount", 0) > 0:
                return ds
        except Exception:
            pass
    raise RuntimeError("Could not determine last trading day from Polygon.")


def today_trading_date() -> str | None:
    """Return today as YYYY-MM-DD if it is a trading day (weekday + Polygon has SPY data)."""
    now = datetime.now()
    if now.weekday() >= 5:   # 5=Saturday, 6=Sunday
        return None
    today = now.strftime("%Y-%m-%d")
    try:
        resp = polygon_get(f"/v2/aggs/ticker/SPY/range/1/day/{today}/{today}")
        if resp.get("resultsCount", 0) > 0:
            return today
    except Exception:
        pass
    return None


def load_merged(sym: str) -> pd.DataFrame | None:
    """Load OHLCV for a symbol — from cloud history in cloud mode, otherwise local parquet."""
    if CLOUD_MODE:
        return _CLOUD_HISTORY.get(sym.upper())
    path = MERGED_ROOT / f"{sym.upper()}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df.index.name = "Date"
        rename = {c: c.capitalize() for c in df.columns
                  if c.lower() in ("open", "high", "low", "close", "volume")}
        return df.rename(columns=rename)
    except Exception:
        return None


def append_to_merged(sym: str, bar: dict, date_str: str) -> None:
    """Append one OHLCV bar to the merged parquet file (no-op in cloud mode)."""
    if CLOUD_MODE:
        return
    path = MERGED_ROOT / f"{sym.upper()}.parquet"
    if not path.exists():
        return
    try:
        existing = pd.read_parquet(path)
        ts = pd.Timestamp(date_str).tz_localize(None).normalize()
        if ts in existing.index:
            return
        col_map = {c.lower(): c for c in existing.columns}
        new_row = pd.DataFrame([{
            col_map.get("open",  "open"):   bar["open"],
            col_map.get("high",  "high"):   bar["high"],
            col_map.get("low",   "low"):    bar["low"],
            col_map.get("close", "close"):  bar["close"],
            col_map.get("volume","volume"): bar["volume"],
        }], index=pd.DatetimeIndex([ts], name=existing.index.name or "Date"))
        updated = pd.concat([existing, new_row]).sort_index()
        updated.to_parquet(path)
    except Exception as e:
        print(f"    [merged] {sym}: {e}")


# ── Cloud history prefetch (grouped endpoint, all tickers) ──────────────────────

def _prefetch_cloud_history(date_str: str, lookback_days: int = 135) -> None:
    """Fetch N days of Polygon grouped data and populate _CLOUD_HISTORY.

    Uses the /v2/aggs/grouped endpoint (one call per day, all tickers),
    so 135 calls ≈ 40 seconds on the paid plan.  We need 135 days for the
    C7 rebalance (126d return), 25 days for VT12/MA20.
    """
    global _CLOUD_HISTORY
    from collections import defaultdict
    print(f"  [cloud] Fetching {lookback_days} days of market data from Polygon…", flush=True)

    # Build list of calendar dates working backwards (skip weekends)
    dates_to_fetch: list[str] = []
    d = pd.Timestamp(date_str)
    while len(dates_to_fetch) < lookback_days:
        if d.weekday() < 5:   # Mon–Fri
            dates_to_fetch.append(d.strftime("%Y-%m-%d"))
        d -= pd.Timedelta(days=1)
    dates_to_fetch.reverse()   # oldest first

    bars_by_sym: dict[str, list] = defaultdict(list)
    missing = 0
    for i, ds in enumerate(dates_to_fetch):
        try:
            resp = polygon_get(f"/v2/aggs/grouped/locale/us/market/stocks/{ds}")
            for bar in resp.get("results", []):
                sym = bar["T"].upper()
                bars_by_sym[sym].append({
                    "Date":   pd.Timestamp(ds),
                    "Open":   bar["o"], "High": bar["h"],
                    "Low":    bar["l"], "Close": bar["c"],
                    "Volume": bar.get("v", 0),
                })
        except Exception:
            missing += 1
        if (i + 1) % 25 == 0:
            print(f"    {i+1}/{len(dates_to_fetch)} days…", end="\r", flush=True)

    # Build per-ticker DataFrames and store in global cache
    for sym, rows in bars_by_sym.items():
        df = pd.DataFrame(rows)
        df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()
        _CLOUD_HISTORY[sym] = df.set_index("Date").sort_index()

    # Also fetch index data (VIX, TNX) via individual ticker endpoint
    for idx_sym, poly_sym in [("I:VIX", "I:VIX"), ("I:TNX", "I:TNX")]:
        try:
            start = dates_to_fetch[0]
            resp = polygon_get(f"/v2/aggs/ticker/{poly_sym}/range/1/day/{start}/{date_str}",
                               {"adjusted": "true", "sort": "asc", "limit": 500})
            if resp.get("results"):
                rows = [{"Date": pd.Timestamp(r["t"], unit="ms", tz="UTC").tz_localize(None).normalize(),
                         "Open": r["o"], "High": r["h"], "Low": r["l"], "Close": r["c"],
                         "Volume": r.get("v", 0)} for r in resp["results"]]
                df = pd.DataFrame(rows).set_index("Date").sort_index()
                _CLOUD_HISTORY[idx_sym] = df
        except Exception:
            pass

    print(f"\n  [cloud] Cache ready: {len(_CLOUD_HISTORY)} tickers, {missing} missing dates.")


def _generate_today_signals_cloud(date_str: str) -> pd.DataFrame:
    """Generate MR entry signals for date_str from _CLOUD_HISTORY.

    Replicates the authoritative REG2E entry rules from
    MR/generate_mr_cross_sectional_signals.py (full fidelity, no longer a proxy):
      - Wilder RSI-14 (rolling mean) < 25  AND  price >= 10
      - cross-sectional 10d-return rank: select the 5 MOST oversold (rank <= 5)
      - VIX <= 40 at signal day; 30% 1-day VIX spike in trailing 5d -> blackout
      - crash gate: skip only when QQQ < 200MA AND VIX rising (both today)
    Universe is the current NQ100 (correct for a forward as-of book).
    """
    nq100_path = ROOT / "tickers_to_scan" / "nasdaq_100.json"
    if not nq100_path.exists():
        return pd.DataFrame()
    with open(nq100_path) as f:
        nq100 = [t.upper() for t in json.load(f)]

    dt = pd.Timestamp(date_str)

    # ── VIX gates: level (>40) + 30% 1-day spike blackout (trailing 5d) ──────
    vix_df = _CLOUD_HISTORY.get("I:VIX")
    vix_close = (vix_df["Close"] if (vix_df is not None and "Close" in vix_df.columns)
                 else None)
    vix_val = (float(vix_close.loc[dt]) if (vix_close is not None and dt in vix_close.index)
               else np.nan)
    if np.isfinite(vix_val) and vix_val > 40:
        print(f"  [cloud] VIX={vix_val:.1f} > 40 — no MR entries today")
        return pd.DataFrame()
    vix_rising = False
    if vix_close is not None:
        vc = vix_close[vix_close.index <= dt]
        if len(vc) >= 2:
            vix_rising = bool(vc.iloc[-1] > vc.iloc[-2])
            recent = vc.tail(6).pct_change().tail(5)   # last 5 day-over-day changes
            if (recent > 0.30).any():
                print("  [cloud] VIX shock blackout (30% spike in last 5d) — no MR entries")
                return pd.DataFrame()

    # ── Crash gate: skip ONLY when QQQ < 200MA AND VIX rising (REG2E) ────────
    qqq_df = _CLOUD_HISTORY.get("QQQ")
    qqq_below = False
    if qqq_df is not None and "Close" in qqq_df.columns:
        ma200 = qqq_df["Close"].rolling(200, min_periods=100).mean()
        qc = float(qqq_df.loc[dt, "Close"]) if dt in qqq_df.index else np.nan
        qm = float(ma200.loc[dt]) if dt in ma200.index else np.nan
        qqq_below = bool(np.isfinite(qc) and np.isfinite(qm) and qc < qm)
    if qqq_below and vix_rising:
        print("  [cloud] crash gate (QQQ<200MA & VIX rising) — no MR entries today")
        return pd.DataFrame()

    # ── Per-ticker Wilder RSI-14 + 10d return; REG2E eligibility ─────────────
    rows = []
    for sym in nq100:
        df = _CLOUD_HISTORY.get(sym)
        if df is None or "Close" not in df.columns:
            continue
        c = df[df.index <= dt]["Close"]
        if len(c) < 16:
            continue
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14, min_periods=14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
        ln    = loss.iloc[-1]
        rs    = gain.iloc[-1] / ln if (np.isfinite(ln) and ln != 0) else np.nan
        rsi14 = float(100 - 100 / (1 + rs)) if np.isfinite(rs) else np.nan
        close_px = float(c.iloc[-1])
        ret_10d = float(c.iloc[-1] / c.iloc[-11] - 1)
        # REG2E eligibility: RSI < 25 AND price >= 10
        if not (np.isfinite(rsi14) and rsi14 < 25 and close_px >= 10):
            continue
        rows.append({"ticker": sym, "rsi14": rsi14, "ret_10d": ret_10d,
                     "close": close_px,
                     "vix": vix_val if np.isfinite(vix_val) else 20.0,
                     "signal_date": date_str, "entry_date": date_str})

    if not rows:
        return pd.DataFrame()

    df_sig = pd.DataFrame(rows)
    # Cross-sectional rank: 1 = most oversold (most negative 10d return); take top 5
    df_sig["rank_10d"] = df_sig["ret_10d"].rank(ascending=True, method="first").astype(int)
    df_sig = df_sig[df_sig["rank_10d"] <= 5].sort_values("rank_10d").copy()
    if not df_sig.empty:
        print(f"  [cloud] {len(df_sig)} REG2E MR signal(s) for {date_str}: "
              f"{df_sig['ticker'].tolist()}")
    return df_sig


# ── Live state ─────────────────────────────────────────────────────────────────

def state_path(strategy: str) -> Path:
    d = (PAPER_STATE / "state") if CLOUD_MODE else STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{strategy}_live_state.json"


def init_state_from_frozen(strategy: str) -> dict:
    """Bootstrap live state from the frozen June 5 target books + stock_level."""
    cfg = STRATEGY_CONFIG[strategy]
    state: dict[str, Any] = {
        "strategy": strategy,
        "last_processed_date": "2026-06-05",
        "cash": INITIAL_CAPITAL,
        "c7": {
            "bars_since_rebalance": 2,   # Jun 03 was rebal, Jun 04+05 = 2 bars since
            "last_rebalance_date": "2026-06-03",
            "tickers": [],
        },
        "mr_positions": {},
        "def_tickers": cfg["def_tickers"],
    }

    # C7 tickers from unified target book
    tb_path = (PAPER_STATE / "target_books" / f"{strategy}.csv") if CLOUD_MODE else \
              (OUT_BASE / strategy / "alpaca_ready" / "daily_target_book_unified.csv")
    if tb_path.exists() and cfg["c7_alloc"] > 0:
        df = pd.read_csv(tb_path, parse_dates=["date"])
        last = df[df["date"] == df["date"].max()]
        state["c7"]["tickers"] = sorted(
            last[last["sleeves"] == "C7_Momentum"]["ticker"].str.upper().tolist()
        )

    # MR positions from stock_level.
    # For standalone MR: use only the latest date's snapshot.
    # For B1/B2: use the unified target book's MR_NQ100 tickers, and for each
    #   look up the most recent stock_level row within the last 60 days.
    sl_path = (PAPER_STATE / "stock_level" / "MR.csv") if CLOUD_MODE else MR_SL_PATH
    if sl_path.exists():
        sl = pd.read_csv(sl_path, parse_dates=["Date", "EntryDate"])
        latest_date = sl["Date"].max()

        if strategy != "MR_VG12_PIT" and tb_path.exists():
            # B1/B2: get MR ticker list from unified target book
            df_tb = pd.read_csv(tb_path, parse_dates=["date"])
            last_tb = df_tb[df_tb["date"] == df_tb["date"].max()]
            mr_syms = set(last_tb[last_tb["sleeves"] == "MR_NQ100"]["ticker"].str.upper())
            # For each B1/B2 MR ticker, find its most recent stock_level row
            cutoff = latest_date - pd.Timedelta(days=60)
            recent_sl = sl[sl["Date"] >= cutoff]
            sym_latest = recent_sl.sort_values("Date").groupby("Symbol").last()
            for sym in mr_syms:
                if sym not in sym_latest.index:
                    continue
                row = sym_latest.loc[sym]
                state["mr_positions"][sym] = {
                    "entry_date":   pd.Timestamp(row["EntryDate"]).strftime("%Y-%m-%d"),
                    "entry_px":     float(row["EntryPrice"]),
                    "stop_price":   float(row["StopPrice"]),
                    "target_price": float(row["TargetPrice"]),
                    "hold_bars":    int(row["HoldBars"]),
                    "shares":       float(row["Shares"]),
                    "cost_basis":   float(row["Shares"]) * float(row["EntryPrice"]),
                }
        else:
            # Standalone MR: only the latest snapshot date
            latest = sl[sl["Date"] == latest_date]
            for _, row in latest.iterrows():
                sym = str(row["Symbol"]).upper()
                state["mr_positions"][sym] = {
                    "entry_date":   pd.Timestamp(row["EntryDate"]).strftime("%Y-%m-%d"),
                    "entry_px":     float(row["EntryPrice"]),
                    "stop_price":   float(row["StopPrice"]),
                    "target_price": float(row["TargetPrice"]),
                    "hold_bars":    int(row["HoldBars"]),
                    "shares":       float(row["Shares"]),
                    "cost_basis":   float(row["Shares"]) * float(row["EntryPrice"]),
                }

    # Estimate cash from equity - deployed notional
    deployed = sum(
        pos["shares"] * pos["entry_px"] for pos in state["mr_positions"].values()
    )
    state["cash"] = max(0.0, INITIAL_CAPITAL - deployed)

    return state


def load_state(strategy: str) -> dict:
    p = state_path(strategy)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    print(f"  [{strategy}] No live state found — initialising from frozen Jun 5 target books.")
    state = init_state_from_frozen(strategy)
    save_state(state)
    return state


def save_state(state: dict) -> None:
    p = state_path(state["strategy"])
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


# ── MR incremental update ──────────────────────────────────────────────────────

def load_signals() -> pd.DataFrame:
    sig_path = SIG_PATH
    if CLOUD_MODE:
        cloud_sig = PAPER_STATE / "signals" / "REG2E_recent.parquet"
        sig_path  = cloud_sig if cloud_sig.exists() else SIG_PATH
    if not sig_path.exists():
        return pd.DataFrame(columns=["ticker", "entry_date", "signal_date",
                                     "rsi14", "ret_10d", "rank_10d"])
    df = pd.read_parquet(sig_path)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.tz_localize(None).dt.normalize()
    return df


def load_ma20(sym: str) -> pd.Series | None:
    """Return MA20 of Close for a ticker from merged data."""
    df = load_merged(sym)
    if df is None or "Close" not in df.columns:
        return None
    return df["Close"].rolling(MR_MA_PERIOD, min_periods=MR_MA_PERIOD).mean()


def update_mr(state: dict, today_prices: dict, date_str: str,
              signals: pd.DataFrame, strategy: str) -> dict:
    """Apply one day of MR simulation logic to state.mr_positions."""
    dt = pd.Timestamp(date_str)
    mr = state["mr_positions"]
    cash = state["cash"]

    # --- Exit checks for existing positions ---
    ghost_syms: set[str] = set()
    for sym in list(mr.keys()):
        pos = mr[sym]
        if sym not in today_prices:
            # no data today (delisted?) — close at stop
            cash += pos["shares"] * xp(pos["stop_price"]) - cm(pos["shares"])
            del mr[sym]
            continue

        bar = today_prices[sym]
        bh, bl, bc = bar["high"], bar["low"], bar["close"]
        pos["hold_bars"] += 1
        hold = pos["hold_bars"]

        ma20_series = load_ma20(sym)
        ma20 = float(ma20_series.get(dt, np.nan)) if ma20_series is not None else np.nan

        xpr, xreason = None, None
        if bl <= pos["stop_price"]:
            xpr = xp(pos["stop_price"]); xreason = "Stop Loss"; ghost_syms.add(sym)
        elif bh >= pos["target_price"]:
            xpr = xp(pos["target_price"]); xreason = "Target"
        elif hold >= 2 and np.isfinite(ma20) and bc > ma20:
            xpr = xp(bc); xreason = "MA20 Exit"
        elif hold >= MR_MAX_HOLD:
            xpr = xp(bc); xreason = "Max Hold"

        if xpr:
            sh = pos["shares"]
            cash += sh * xpr - cm(sh)
            print(f"    MR EXIT: {sym} → {xreason}  PnL ${sh*(xpr - pos['entry_px']):.0f}")
            del mr[sym]

    # --- New entry signals for today ---
    today_signals = signals[signals["entry_date"] == dt]
    state["_ghost_syms"] = list(ghost_syms)  # persist for next day check

    for _, sig in today_signals.sort_values("rank_10d").iterrows():
        sym = str(sig["ticker"]).upper()
        if len(mr) >= MR_MAX_POSITIONS:
            break
        if sym in mr or sym in ghost_syms:
            continue
        if sym not in today_prices:
            continue
        raw = today_prices[sym]["open"]
        if raw <= 0 or np.isnan(raw):
            continue

        alloc = min(INITIAL_CAPITAL * MR_ALLOC_PCT, cash)
        e = ep(raw)
        sh = alloc / e
        cost = sh * e + cm(sh)
        if cash < cost:
            continue

        cash -= cost
        mr[sym] = {
            "entry_date":   date_str,
            "entry_px":     e,
            "stop_price":   raw * (1 - MR_STOP_PCT),
            "target_price": raw * (1 + MR_TARGET_PCT),
            "hold_bars":    0,
            "shares":       sh,
            "cost_basis":   cost,
        }
        print(f"    MR ENTRY: {sym}  RSI={sig['rsi14']:.1f}  10d={sig['ret_10d']*100:+.1f}%"
              f"  rank #{int(sig['rank_10d'])}/100  @ ${e:.2f}")

    state["mr_positions"] = mr
    state["cash"] = cash
    return state


# ── C7 incremental update ──────────────────────────────────────────────────────

def compute_sp500_top7(date_str: str, pit_snap: frozenset | None = None) -> list[str]:
    """Return top-7 SP500 tickers by 126d trailing return as of date_str."""
    dt = pd.Timestamp(date_str)
    start_dt = dt - pd.tseries.offsets.BDay(C7_LOOKBACK + 5)
    rows = {}

    sp500_path = ROOT / "tickers_to_scan" / "sp-500.json"
    if sp500_path.exists():
        with open(sp500_path) as f:
            sp500_tickers = json.load(f)
    else:
        return []

    if pit_snap:
        sp500_tickers = [t for t in sp500_tickers if t in pit_snap]

    for sym in sp500_tickers:
        df = load_merged(sym)
        if df is None or "Close" not in df.columns:
            continue
        try:
            sub = df[(df.index >= start_dt) & (df.index <= dt)]["Close"].dropna()
            if len(sub) < C7_LOOKBACK // 2:
                continue
            ret = sub.iloc[-1] / sub.iloc[0] - 1 if sub.iloc[0] > 0 else np.nan
            if np.isfinite(ret):
                rows[sym] = ret
        except Exception:
            pass

    if not rows:
        return []
    series = pd.Series(rows)
    return list(series.nlargest(C7_N_POSITIONS).index)


def update_c7(state: dict, today_prices: dict, date_str: str, strategy: str) -> dict:
    cfg = STRATEGY_CONFIG[strategy]
    if cfg["c7_alloc"] == 0:
        return state

    c7 = state["c7"]
    c7["bars_since_rebalance"] = c7.get("bars_since_rebalance", 0) + 1

    # Check QQQ bull filter
    qqq_bar = today_prices.get("QQQ")
    if qqq_bar is None:
        return state

    qq = load_merged("QQQ")
    qqq_bull = True  # default bull
    if qq is not None and "Close" in qq.columns:
        try:
            ma200 = qq["Close"].rolling(C7_BULL_MA, min_periods=C7_BULL_MA).mean()
            dt = pd.Timestamp(date_str)
            q_close = qq.loc[dt, "Close"] if dt in qq.index else np.nan
            q_ma    = float(ma200.loc[dt]) if dt in ma200.index else np.nan
            qqq_bull = np.isfinite(q_close) and np.isfinite(q_ma) and q_close > q_ma
        except Exception:
            pass

    do_rebalance = (c7["bars_since_rebalance"] >= C7_REBAL_BARS)

    if do_rebalance:
        c7["bars_since_rebalance"] = 0
        c7["last_rebalance_date"] = date_str

        if not qqq_bull:
            print(f"    C7: QQQ below 200d MA — flat (defensive mode)")
            c7["tickers"] = []
        else:
            new_top7 = compute_sp500_top7(date_str)
            old = set(c7.get("tickers", []))
            new = set(new_top7)
            if old != new:
                exits  = sorted(old - new)
                enters = sorted(new - old)
                print(f"    C7 REBAL: drop {exits}  add {enters}")
            else:
                print(f"    C7 REBAL: composition unchanged → {sorted(new_top7)}")
            c7["tickers"] = new_top7
    else:
        bars_left = C7_REBAL_BARS - c7["bars_since_rebalance"]
        print(f"    C7: holding current (next rebal in {bars_left} bar{'s' if bars_left != 1 else ''})")

    state["c7"] = c7
    return state


# ── Target book + VT12 ─────────────────────────────────────────────────────────

def compute_vt12_scale(sym: str, date_str: str) -> float:
    """Return VT12 scale factor for a symbol: min(1, 12%_annual / realized_20d_vol)."""
    df = load_merged(sym)
    if df is None or "Close" not in df.columns:
        return 1.0
    try:
        dt = pd.Timestamp(date_str)
        sub = df[df.index <= dt]["Close"].tail(VT12_WINDOW + 2)
        if len(sub) < VT12_WINDOW:
            return 1.0
        daily_ret = sub.pct_change().dropna()
        vol_daily = float(daily_ret.std())
        vol_annual = vol_daily * VT12_ANNUALISE
        if vol_annual <= 0:
            return 1.0
        return min(1.0, VT12_TARGET / vol_annual)
    except Exception:
        return 1.0


def _portfolio_vt12_scale(raw_weights: dict[str, float], date_str: str) -> float:
    """Portfolio-level VT-12% scalar — matches the backtest's apply_vt().

    The backtest scales the WHOLE book by min(1, 12% / annualised vol of the
    *diversified* portfolio's 20-day returns), so cross-holding correlation keeps
    the scalar near 1.0 for a normal book (B1 portfolio vol ≈ 12%). The old
    per-symbol compute_vt12_scale() ignored diversification and shrank a normal
    book to ~35% gross — a deviation from what was backtested.
    """
    dt = pd.Timestamp(date_str)
    series: dict[str, pd.Series] = {}
    for sym in raw_weights:
        df = load_merged(sym)
        if df is None or "Close" not in df.columns:
            continue
        sub = df[df.index <= dt]["Close"].tail(VT12_WINDOW + 2)
        if len(sub) >= VT12_WINDOW:
            series[sym] = sub.pct_change()
    if not series:
        return 1.0
    R = pd.DataFrame(series).dropna()
    if len(R) < VT12_WINDOW:
        return 1.0
    w = pd.Series({s: raw_weights[s] for s in R.columns})
    port_ret = (R * w).sum(axis=1)                      # weighted daily portfolio return
    vol_annual = float(port_ret.std()) * VT12_ANNUALISE
    if vol_annual <= 0:
        return 1.0
    return min(1.0, VT12_TARGET / vol_annual)


def generate_target_book(state: dict, equity: float, date_str: str,
                         strategy: str) -> pd.DataFrame:
    cfg = STRATEGY_CONFIG[strategy]

    # ── Collect raw (un-VT) sleeve weights ────────────────────────────────────
    raw: list[tuple[str, float, str]] = []
    for sym in state["c7"].get("tickers", []):
        raw.append((sym, cfg["c7_alloc"] / C7_N_POSITIONS, "C7_Momentum"))
    for sym in state["mr_positions"]:
        raw.append((sym, MR_ALLOC_PCT, "MR_NQ100"))
    if cfg["def_tickers"] and cfg["def_alloc"] > 0:
        def_w = cfg["def_alloc"] / len(cfg["def_tickers"])
        for sym in cfg["def_tickers"]:
            raw.append((sym, def_w, "Defensive_ETF"))

    if not raw:
        return pd.DataFrame()

    # ── Portfolio-level VT-12% (single scalar, matches backtest apply_vt) ──────
    raw_weights: dict[str, float] = {}
    for sym, w, _ in raw:
        raw_weights[sym] = raw_weights.get(sym, 0.0) + w
    port_scale = _portfolio_vt12_scale(raw_weights, date_str)

    rows = []
    for sym, w, sleeve in raw:
        tw = round(w * port_scale, 6)
        rows.append({
            "date": date_str, "ticker": sym,
            "target_weight": tw,
            "target_value":  round(equity * tw, 2),
            "sleeve": sleeve,
        })

    df = pd.DataFrame(rows)
    total_w = df["target_weight"].sum()
    if total_w > 1.0:
        df["target_weight"] = df["target_weight"] / total_w
        df["target_value"]  = df["target_weight"] * equity
    return df


# ── Order submission ───────────────────────────────────────────────────────────

def submit_orders_for_strategy(
    strategy: str, target_book: pd.DataFrame, dry_run: bool = True
) -> None:
    key, secret = ALPACA_KEYS[strategy]
    if not key or not secret:
        print(f"  [{strategy}] No API keys — skipping order submission.")
        return

    acct    = alpaca_req("GET", "/v2/account",   key, secret)
    equity  = float(acct["equity"])
    raw_pos = alpaca_req("GET", "/v2/positions", key, secret)
    current = {p["symbol"].upper(): float(p["market_value"]) for p in raw_pos}
    # Exact sellable share count per held symbol (raw string — avoids float round-trip).
    qty_avail = {p["symbol"].upper(): (p.get("qty_available") or p.get("qty") or "0")
                 for p in raw_pos}

    MIN_NOTIONAL = 25.0
    orders = []
    target_vals = {row["ticker"]: row["target_value"]
                   for _, row in target_book.iterrows() if row["target_weight"] > 0}

    all_syms = set(target_vals) | set(current)
    for sym in sorted(all_syms):
        tgt = target_vals.get(sym, 0.0)
        cur = current.get(sym, 0.0)
        delta = tgt - cur
        if abs(delta) < MIN_NOTIONAL:
            continue
        # Full exits (target dropped the symbol) sell by available QTY, not notional:
        # a notional sell of the whole position can request marginally more shares than
        # held once the price ticks, triggering Alpaca 403 "insufficient qty available".
        full_exit = delta < 0 and tgt == 0.0 and sym in current
        orders.append({
            "symbol":    sym,
            "side":      "buy" if delta > 0 else "sell",
            "notional":  round(abs(delta), 2),
            "full_exit": full_exit,
            "qty":       qty_avail.get(sym, "0"),
        })

    print(f"\n  [{strategy}] equity=${equity:,.0f}  orders={len(orders)}")
    submitted = 0
    for o in orders:
        print(f"    {o['side'].upper():4s}  {o['symbol']:6s}  ${o['notional']:,.0f}"
              f"{'  [FULL EXIT by qty]' if o['full_exit'] else ''}")
        if dry_run:
            continue
        if o["full_exit"]:
            if float(o["qty"]) <= 0:
                print(f"      [SKIP: no qty available for {o['symbol']}]")
                continue
            body = {"symbol": o["symbol"], "side": "sell", "type": "market",
                    "time_in_force": "day", "qty": o["qty"]}
        else:
            body = {"symbol": o["symbol"], "side": o["side"], "type": "market",
                    "time_in_force": "day", "notional": str(o["notional"])}
        # Isolate each order: a single rejection must NOT abort the whole strategy
        # (and thus skip every subsequent strategy in the submit loop).
        try:
            alpaca_req("POST", "/v2/orders", key, secret, body)
            submitted += 1
        except Exception as e:  # noqa: BLE001
            print(f"      [FAILED {o['symbol']}: {e}]")

    if dry_run:
        print("  [dry-run] No orders submitted.")
    else:
        print(f"  [{strategy}] Submitted {submitted}/{len(orders)} orders.")


# ── Save updated stock_level and unified target book ───────────────────────────

def save_stock_level_row(state: dict, today_prices: dict, date_str: str,
                          equity: float) -> None:
    """Append today's MR position snapshot to the stock_level CSV."""
    rows = []
    dt = pd.Timestamp(date_str)
    for sym, pos in state["mr_positions"].items():
        bar = today_prices.get(sym)
        close_px = bar["close"] if bar else pos["entry_px"]
        mkt_val  = pos["shares"] * close_px
        rows.append({
            "Date":          dt,
            "Symbol":        sym,
            "Shares":        round(pos["shares"], 6),
            "ClosePrice":    round(close_px, 4),
            "MarketValue":   round(mkt_val, 2),
            "Weight":        round(100.0 * mkt_val / equity, 4) if equity > 0 else 0.0,
            "UnrealizedPnL": round(mkt_val - pos["cost_basis"], 2),
            "EntryDate":     pos["entry_date"],
            "EntryPrice":    round(pos["entry_px"], 4),
            "StopPrice":     round(pos["stop_price"], 4),
            "TargetPrice":   round(pos["target_price"], 4),
            "HoldBars":      pos["hold_bars"],
            "Component":     "MR",
        })
    if not rows:
        return
    new_df = pd.DataFrame(rows)
    if CLOUD_MODE:
        # In cloud mode: overwrite with today's snapshot (positions report reads this)
        out = PAPER_STATE / "stock_level" / "MR.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        new_df.to_csv(out, index=False)
        return
    if MR_SL_PATH.exists():
        existing = pd.read_csv(MR_SL_PATH, parse_dates=["Date", "EntryDate"])
        existing = existing[pd.to_datetime(existing["Date"]).dt.date != dt.date()]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    MR_SL_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(MR_SL_PATH, index=False)


def save_unified_target_book(strategy: str, target_book: pd.DataFrame) -> None:
    if CLOUD_MODE:
        # In cloud mode: overwrite with today's snapshot
        out_path = PAPER_STATE / "target_books" / f"{strategy}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        target_book.rename(columns={"sleeve": "sleeves"}).to_csv(out_path, index=False)
        return
    out_path = OUT_BASE / strategy / "alpaca_ready" / "daily_target_book_unified.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        existing = pd.read_csv(out_path, parse_dates=["date"])
        date_str = target_book["date"].iloc[0]
        existing = existing[pd.to_datetime(existing["date"], format="mixed").dt.strftime("%Y-%m-%d") != date_str]
        combined = pd.concat([existing, target_book.rename(columns={"sleeve": "sleeves"})], ignore_index=True)
    else:
        combined = target_book.rename(columns={"sleeve": "sleeves"})
    combined.to_csv(out_path, index=False)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global CLOUD_MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Don't submit orders.")
    ap.add_argument("--cloud",   action="store_true",
                    help="Cloud mode: fetch price history from Polygon, save state to paper_state/.")
    ap.add_argument("--date", help="Override trading date (YYYY-MM-DD). Defaults to last trading day.")
    args = ap.parse_args()
    CLOUD_MODE = args.cloud

    print("\n" + "═" * 64)
    print("  DAILY PIPELINE — " + datetime.now().strftime("%A %b %d, %Y %H:%M"))
    if args.dry_run:
        print("  MODE: DRY-RUN (no orders will be submitted)")
    if CLOUD_MODE:
        print("  MODE: CLOUD (Polygon history, paper_state/ storage)")
    print("═" * 64)

    # Determine today's trading date
    if args.date:
        date_str = args.date
    else:
        date_str = today_trading_date()
        if date_str is None:
            print("\n  Market is not open today. Checking last trading day...")
            date_str = last_trading_day()
            print(f"  Using last trading day: {date_str}")
        else:
            print(f"\n  Trading date: {date_str}")

    # Cloud mode: prefetch all price history upfront, then generate signals
    if CLOUD_MODE:
        print("\n[1/5] Prefetching market data from Polygon (cloud mode)…")
        _prefetch_cloud_history(date_str, lookback_days=135)
        new_signals  = _generate_today_signals_cloud(date_str)
        # Merge new signals into paper_state signals file
        cloud_sig = PAPER_STATE / "signals" / "REG2E_recent.parquet"
        if not new_signals.empty:
            new_signals["entry_date"] = pd.to_datetime(new_signals["entry_date"])
            if cloud_sig.exists():
                old = pd.read_parquet(cloud_sig)
                combined = pd.concat([old, new_signals], ignore_index=True)
                combined.drop_duplicates(subset=["ticker", "entry_date"], keep="last",
                                         inplace=True)
            else:
                combined = new_signals
            # Normalise date columns to datetime before writing — concatenating
            # freshly-generated signals (str dates) with the existing parquet
            # (datetime dates) leaves these columns object-typed, which pyarrow
            # refuses to write ("cannot be converted to int").
            for _dcol in ("signal_date", "entry_date"):
                if _dcol in combined.columns:
                    combined[_dcol] = pd.to_datetime(combined[_dcol], errors="coerce")
            combined.to_parquet(cloud_sig, index=False)
        signals = load_signals()
        today_signals_count = len(signals[signals["entry_date"] == pd.Timestamp(date_str)])
        print(f"  Signals: {len(signals):,} total | {today_signals_count} for {date_str}")
    else:
        # Load signals file
        print("\n[1/5] Loading MR signals...")
        signals = load_signals()
        today_signals_count = len(signals[signals["entry_date"] == pd.Timestamp(date_str)])
        print(f"  Signals file: {len(signals):,} total rows | {today_signals_count} signals for {date_str}")

    # Determine all tickers to fetch
    all_tickers = set(["QQQ", "SPY", "TLT", "IAU"])
    for strategy in STRATEGY_CONFIG:
        state = load_state(strategy)
        all_tickers.update(state["mr_positions"].keys())
        all_tickers.update(state["c7"].get("tickers", []))
        all_tickers.update(STRATEGY_CONFIG[strategy]["def_tickers"])
    # Add SP500 for C7 ranking (sampled — full list fetched via grouped endpoint)
    sp500_path = ROOT / "tickers_to_scan" / "sp-500.json"
    if sp500_path.exists():
        with open(sp500_path) as f:
            all_tickers.update(json.load(f))

    # Fetch prices
    print(f"\n[2/5] Fetching prices from Polygon...")
    today_prices = fetch_prices(list(all_tickers), date_str)
    if not today_prices:
        print("  No price data returned — aborting.")
        sys.exit(1)

    # Update merged parquet files
    print(f"\n[3/5] Appending {len(today_prices)} bars to merged data...")
    updated = 0
    for sym, bar in today_prices.items():
        path = MERGED_ROOT / f"{sym.upper()}.parquet"
        if path.exists():
            append_to_merged(sym, bar, date_str)
            updated += 1
    print(f"  Updated {updated} parquet files.")

    # Per-strategy simulation update + order submission
    print(f"\n[4/5] Updating strategy positions...")
    if args.dry_run:
        print("  [dry-run] Simulation is computed in-memory only — no state/report files"
              " will be written, so today can be safely re-run once live.")
    strategy_states: dict[str, dict] = {}
    for strategy in STRATEGY_CONFIG:
        print(f"\n  ── {strategy} ──")
        state = load_state(strategy)

        already_done = state["last_processed_date"] >= date_str
        if already_done:
            print(f"  Already processed {date_str} — skipping simulation update.")
        else:
            state = update_mr(state, today_prices, date_str, signals, strategy)
            state = update_c7(state, today_prices, date_str, strategy)
            state["last_processed_date"] = date_str
            if not args.dry_run:
                save_state(state)

        strategy_states[strategy] = state

        # Estimate equity
        mr_mkt = sum(
            pos["shares"] * today_prices.get(sym, {}).get("close", pos["entry_px"])
            for sym, pos in state["mr_positions"].items()
        )
        equity = state["cash"] + mr_mkt
        print(f"    Estimated equity: ${equity:,.0f}  cash: ${state['cash']:,.0f}")

        target_book = generate_target_book(state, equity, date_str, strategy)
        if not args.dry_run:
            if not target_book.empty:
                save_unified_target_book(strategy, target_book)
            # Only write stock_level on first run for this date (avoid overwriting correct data)
            if not already_done:
                save_stock_level_row(state, today_prices, date_str, equity)

    # Submit orders — use live Alpaca equity for correct sizing
    print(f"\n[5/5] Submitting orders...")
    for strategy in STRATEGY_CONFIG:
        # Reuse the in-memory state from [4/5] rather than reloading from disk: in
        # dry-run mode nothing was persisted, so a fresh load_state() here would
        # silently discard today's simulated MR/C7 update and submit stale orders.
        state = strategy_states[strategy]
        key, secret = ALPACA_KEYS[strategy]
        if not key or not secret:
            print(f"  [{strategy}] No API keys — skipping.")
            continue
        try:
            acct   = alpaca_req("GET", "/v2/account", key, secret)
            equity = float(acct["equity"])
        except Exception as e:
            # fallback to local estimate if Alpaca unavailable
            mr_mkt = sum(
                pos["shares"] * today_prices.get(sym, {}).get("close", pos["entry_px"])
                for sym, pos in state["mr_positions"].items()
            )
            equity = state["cash"] + mr_mkt
            print(f"  [{strategy}] Alpaca unreachable ({e}), using local estimate ${equity:,.0f}")
        target_book = generate_target_book(state, equity, date_str, strategy)
        try:
            submit_orders_for_strategy(strategy, target_book, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001 — never let one strategy abort the rest
            print(f"  [{strategy}] submit failed: {e}")

    print(f"\n{'═'*64}")
    print(f"  Pipeline complete. Run positions report:")
    print(f"  python scripts/daily_positions_report.py")
    print(f"{'═'*64}\n")


if __name__ == "__main__":
    main()
