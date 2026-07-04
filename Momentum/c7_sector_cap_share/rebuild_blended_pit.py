"""
rebuild_blended_pit.py
======================
Re-runs all 5 frozen strategies using:
  - Norgate parquet prices (Total-Return adjusted, includes delisted)
  - S&P 500 Point-In-Time universe (survivorship-bias-free C7 and SP500-MR ranking)
  - NQ100 prices from Norgate (MR signals reused from pre-computed REG2E_signals.parquet)

Prints a side-by-side comparison vs the frozen original metrics for all 5 strategies.

Run from project root:
    python scripts/rebuild_blended_pit.py
"""

import sys, os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

NORGATE_ROOT = os.environ.get("NORGATE_DATA_ROOT", "")
SP500_REPO   = os.environ.get("SP500_DATA_ROOT",   "")
# Merged Norgate+Polygon parquet directory (preferred over NORGATE_ROOT)
MERGED_ROOT  = os.environ.get(
    "MERGED_DATA_ROOT",
    str(ROOT / "data" / "market_data" / "merged"),
)

OUT_BASE = ROOT / "output" / os.environ.get("REBUILD_OUT_DIR", "rebuild_blended_pit")
OUT_BASE.mkdir(parents=True, exist_ok=True)

# Opt-in C7 sector-diversification cap (default 0 = disabled, original
# unconstrained top-N behavior). Set e.g. C7_SECTOR_CAP_MAX=2 to test capping
# the number of C7 positions drawn from the same broad sector bucket.
C7_SECTOR_CAP_MAX = int(os.environ.get("C7_SECTOR_CAP_MAX", "0"))
_SECTOR_MAP = None
if C7_SECTOR_CAP_MAX > 0:
    from sector_map import load_ticker_sector_map
    _SECTOR_MAP = load_ticker_sector_map()

# ── Constants (identical to rebuild_blended_strategies.py) ───────────────────
INITIAL_CAPITAL = 100_000.0
# Blend start/fetch are env-overridable so an early-history run (e.g. from the
# UUP 2007-02-20 ETF-inception floor) can be produced without clobbering the
# shipped 2016 outputs: set BLEND_TEST_START / BLEND_FETCH_START / REBUILD_OUT_DIR.
TEST_START       = os.environ.get("BLEND_TEST_START", "2016-09-01")
MR_TEST_START    = "2004-01-02"   # MR_VG12 runs from 2004
FETCH_START      = os.environ.get("BLEND_FETCH_START", "2016-05-15")
MR_FETCH_START   = "2003-06-01"
END_DATE         = os.environ.get("BACKTEST_END_DATE", "2026-04-30")

MR_TARGET_PCT   = 0.12
MR_STOP_PCT     = 0.06
MR_MA_PERIOD    = 20
MR_MAX_HOLD     = 15
SLIP_PCT        = 0.0005
COMM_PER_SHARE  = 0.002

C7_LOOKBACK     = 126
C7_N_POSITIONS  = 7
C7_ALLOC_PCT    = 0.40
C7_ALLOC_35     = 0.35        # S8 bear-mode uses 35%
C7_REBAL_BARS   = 5

B5_QQQ_ALLOC    = 0.60
B5_MR_MAX_POS   = 4
B5_MR_TZ_POS    = 5
B5_TZ_PCT       = 0.02
B5_QQQ_MA       = 200   # MA100 tested but hurt S8/SSD in PIT universe — reverted to 200
C7_BULL_MA      = 200

SSD_VOL_GATE_DG20 = 20.0     # DG20 uses 20%, not 30%
SSD_VOL_LOOKBACK  = 20
SSD_VOL_TARGET    = 10.0

S8_QQQ_MA       = 20

VT_TARGET_PCT   = 12.0
VT_LOOKBACK     = 20

RF_ANNUAL       = 0.04

# Frozen original metrics
FROZEN = {
    "B1_VG12":     {"cagr":17.98, "max_dd":-11.33, "calmar":1.587, "sharpe":1.126, "sortino":1.704},
    "B2_VG12":     {"cagr":16.36, "max_dd":-12.04, "calmar":1.359, "sharpe":1.009, "sortino":1.492},
    "SSD_B2_DG20": {"cagr":16.16, "max_dd":-13.68, "calmar":1.181, "sharpe":0.938, "sortino":1.364},
    "S8_DMVC35":   {"cagr":16.27, "max_dd":-10.26, "calmar":1.586, "sharpe":0.983, "sortino":1.447},
    "MR_VG12":     {"cagr":10.79, "max_dd":-11.69, "calmar":0.923, "sharpe":0.714, "sortino":1.053},
}


# =============================================================================
# DATA LOADING — NORGATE
# =============================================================================

# Despike threshold: a bar is treated as a bad print only if it round-trips by
# >DESPIKE_THRESH against BOTH neighbours. Real gaps/earnings moves don't revert
# next day, so they pass through untouched. Env-overridable; set to 0 to disable.
DESPIKE_THRESH = float(os.environ.get("DESPIKE_THRESH", "0.5"))


def _despike_ohlc(df: pd.DataFrame, thresh: float = DESPIKE_THRESH) -> pd.DataFrame:
    """Repair isolated single-day price spikes (bad data prints).

    A bar is a spike when its price is >thresh away from BOTH the previous and
    next bar *in the same direction* — i.e. a 1-day round trip (e.g. LSI close
    7.13 -> 28.59 -> 7.20 on 2013-01-16 in the merged dataset). Such prints are a
    data-quality artifact, not a real move (genuine gaps do not revert the next
    day), and the simulator would otherwise mark positions at the bad price,
    producing phantom equity. Flagged bars are replaced by the geometric mean of
    their neighbours. Vectorised; no-op when thresh<=0 or no spikes are present.
    """
    if thresh <= 0 or len(df) < 3:
        return df
    for col in ("Open", "High", "Low", "Close"):
        if col not in df.columns:
            continue
        s = df[col].astype(float)
        prev, nxt = s.shift(1), s.shift(-1)
        r1 = s / prev - 1.0
        r2 = s / nxt - 1.0
        mask = (
            (r1.abs() > thresh) & (r2.abs() > thresh)
            & (np.sign(r1) == np.sign(r2))
            & prev.notna() & nxt.notna() & (prev > 0) & (nxt > 0)
        )
        if mask.any():
            df.loc[mask, col] = np.sqrt(prev[mask] * nxt[mask])
    return df


# =============================================================================
# (continued)
# =============================================================================

def load_norgate(symbol: str, fetch_start: str = FETCH_START) -> pd.DataFrame | None:
    """Load OHLCV from the merged parquet directory (Norgate+Polygon total-return).
    Handles delisted tickers stored as {SYMBOL}-YYYYMM.parquet date-suffixed files.
    Falls back to original NORGATE_ROOT if merged directory not found.
    """
    merged_dir = Path(MERGED_ROOT)
    safe = symbol.upper()

    frames_to_load: list[Path] = []
    if merged_dir.is_dir():
        # Exact match
        for candidate in [merged_dir / f"{safe}.parquet",
                          merged_dir / f"{symbol}.parquet"]:
            if candidate.exists():
                frames_to_load = [candidate]
                break
        if not frames_to_load:
            # Date-suffix fallback (e.g. ALTR-201512.parquet)
            prefix = safe + "-"
            frames_to_load = sorted(
                merged_dir / f for f in os.listdir(merged_dir)
                if f.upper().startswith(prefix) and f.upper().endswith(".PARQUET")
            )
    elif NORGATE_ROOT:
        # Legacy: original Norgate directory
        p = Path(NORGATE_ROOT) / f"{symbol}.parquet"
        if p.exists():
            frames_to_load = [p]

    if not frames_to_load:
        return None

    dfs = []
    for fp in frames_to_load:
        try:
            dfs.append(pd.read_parquet(fp))
        except Exception as e:
            print(f"    [merged] {symbol} {fp.name}: {e}")
    if not dfs:
        return None

    # Stable sort is required: date-suffixed files come from ticker REUSE (e.g. two
    # different companies both ticker "LSI" — LSI Corp delisted 2014-05 and a later
    # firm that reused it, overlapping on 4,751 dates). `sorted()` above orders the
    # files by suffix (delisting month) ascending, so a stable sort keeps the
    # earlier-delisted company's row for shared dates (correct: 2013 -> LSI Corp),
    # while later-only dates come from the reused-ticker file. A non-stable sort
    # picks arbitrarily between the two companies' prices, injecting phantom spikes.
    df = pd.concat(dfs).sort_index(kind="stable") if len(dfs) > 1 else dfs[0]
    df = df[~df.index.duplicated(keep="first")]
    # Normalize index — merged files use a 'date' column-index not a DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index = df.index.tz_localize(None).normalize()
    df.index.name = "Date"
    # Normalize column names (merged files use lowercase)
    df.columns = [c.strip().capitalize() if c.lower() in
                  ("open","high","low","close","volume") else c
                  for c in df.columns]
    # Proper capitalization for OHLCV
    rename = {c: c.capitalize() for c in df.columns if c.lower() in
              ("open","high","low","close","volume")}
    df = df.rename(columns=rename)
    keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]
    df   = df[keep].dropna(subset=["Close"])
    df   = df[(df.index >= pd.Timestamp(fetch_start)) &
              (df.index <= pd.Timestamp(END_DATE))]
    df   = _despike_ohlc(df)
    return df if len(df) >= 100 else None


def load_sp500_prices_norgate(pit_tickers: list) -> dict:
    print(f"  Loading SP500 Norgate prices for {len(pit_tickers)} PIT tickers...", flush=True)
    data = {}
    for i, sym in enumerate(pit_tickers):
        df = load_norgate(sym)
        if df is not None:
            data[sym] = df
        if (i + 1) % 150 == 0:
            print(f"    {i+1}/{len(pit_tickers)} ({len(data)} loaded)", flush=True)
    print(f"  SP500 Norgate: {len(data)}/{len(pit_tickers)} tickers")
    return data


def load_nq100_prices_norgate(pit_tickers: list, fetch_start: str = FETCH_START) -> dict:
    bench = {"VIX","QQQ","SPY","GLD","TLT","IAU","UUP"}
    data  = {}
    for sym in pit_tickers:
        if sym in bench:
            continue
        df = load_norgate(sym, fetch_start)
        if df is not None:
            data[sym] = df
    print(f"  NQ100 Norgate: {len(data)} tickers")
    return data


def load_etf_norgate(syms: list) -> dict:
    data = {}
    for sym in syms:
        df = load_norgate(sym, MR_FETCH_START)
        if df is not None:
            data[sym] = df
            continue
        # fallback: csv_data/
        p = ROOT / "csv_data" / f"{sym}.csv"
        if p.exists():
            df2 = pd.read_csv(p)
            dc = next((c for c in df2.columns if c.lower() in ("date","datetime")), None)
            if dc:
                df2[dc] = pd.to_datetime(df2[dc]).dt.tz_localize(None).dt.normalize()
                df2 = df2.set_index(dc).sort_index()
                for col, mapped in [("Adj Close","Close"),("adj_close","Close")]:
                    if col in df2.columns and "Close" not in df2.columns:
                        df2 = df2.rename(columns={col:"Close"})
                if "Close" in df2.columns:
                    data[sym] = df2[["Open","High","Low","Close"]].dropna(subset=["Close"])
    return data


# =============================================================================
# HELPERS
# =============================================================================

def ep(r): return r * (1 + SLIP_PCT)
def xp(r): return r * (1 - SLIP_PCT)
def cm(s): return s * COMM_PER_SHARE


def calc_metrics(eq: pd.Series) -> dict:
    n  = (eq.index[-1] - eq.index[0]).days / 365.25
    tr = eq.iloc[-1] / eq.iloc[0] - 1
    cagr   = (1 + tr)**(1/n) - 1 if n > 0 and (1+tr) > 0 else 0
    hwm    = eq.cummax(); dd = (eq - hwm) / hwm; mdd = dd.min()
    calmar = cagr / abs(mdd) if mdd else 0
    dr     = eq.pct_change().dropna(); rfd = (1+RF_ANNUAL)**(1/252) - 1
    exc    = dr - rfd
    sh     = exc.mean() / exc.std() * np.sqrt(252) if exc.std() > 0 else 0
    so     = exc.mean() / exc[exc<0].std() * np.sqrt(252) if (exc<0).any() else 0
    return {"cagr":round(cagr*100,2), "max_dd":round(mdd*100,2),
            "calmar":round(calmar,3), "sharpe":round(sh,3),
            "sortino":round(so,3), "final_equity":round(eq.iloc[-1],2)}


def apply_vt(raw_equity: pd.Series, target_pct: float = VT_TARGET_PCT) -> pd.Series:
    dr    = raw_equity.pct_change().fillna(0)
    vol20 = dr.rolling(VT_LOOKBACK).std() * np.sqrt(252) * 100
    scale = (target_pct / vol20.shift(1)).clip(upper=1.0).fillna(1.0)
    return (1 + dr * scale).cumprod() * INITIAL_CAPITAL


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).rolling(period, min_periods=period).mean()
    l = (-d.clip(upper=0)).rolling(period, min_periods=period).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def yr_rets(eq: pd.Series) -> dict:
    out = {}
    for yr in sorted(eq.index.year.unique()):
        y = eq[eq.index.year == yr]
        if len(y) >= 2:
            out[yr] = round((y.iloc[-1] / y.iloc[0] - 1) * 100, 2)
    return out


def save_nav(strategy_id: str, raw_eq: pd.Series, vt_eq: pd.Series | None = None):
    out = OUT_BASE / strategy_id
    out.mkdir(parents=True, exist_ok=True)
    cols = {"total_equity": raw_eq}
    if vt_eq is not None:
        cols["vt_equity"] = vt_eq
    pd.DataFrame(cols).to_csv(out / "daily_nav.csv")


# =============================================================================
# B1 / B2 — C7 SP500 + NQ100 MR + Defensive  (PIT-filtered)
# =============================================================================

def simulate_b1_b2_pit(
    mr_signals, nq100_prices, sp500_prices, qqq_df, etf_prices,
    defensive_syms, pit_snapshots, strategy_id, c7_alloc_pct=C7_ALLOC_PCT,
    weight_sink=None,
) -> pd.Series:
    # weight_sink (opt-in, default None -> no behavior change): when a dict is
    # passed, record the per-symbol end-of-day holdings (market value, by sleeve)
    # so the daily target book can be reconstructed for SignalDeck fidelity checks.
    print(f"\n  Simulating {strategy_id} [PIT, C7={c7_alloc_pct:.0%}]...", flush=True)
    c7_pos_alloc = c7_alloc_pct / C7_N_POSITIONS

    all_dates = sorted(
        set(qqq_df.index) | set().union(*[set(d.index) for d in nq100_prices.values()])
    )
    all_dates = [d for d in all_dates if d >= pd.Timestamp(TEST_START)]

    sp500_close  = pd.DataFrame({s: d["Close"] for s, d in sp500_prices.items()})
    sp500_open   = pd.DataFrame({s: d["Open"]  for s, d in sp500_prices.items()})
    ret_126d     = sp500_close.pct_change(C7_LOOKBACK)
    # Precompute effective-first-bar date per ticker for min-history filter.
    # Accounts for recycled tickers (e.g. SNDK = old SanDisk 1995-2016 + new WDC
    # spinoff 2025): if the price series has a gap > 365 calendar days, the
    # effective start is the bar after the last such gap (not the 1995 origin).
    # Genuine IPOs/spinoffs (GEV, CARR) have no gap so their first bar is used.
    _HISTORY_DAYS = pd.Timedelta(days=int((C7_LOOKBACK + 252) * 365.25 / 252))  # ≈ 18 months
    def _effective_first_bar(sym: str) -> pd.Timestamp:
        s = sp500_prices[sym].index
        diffs = pd.Series(s).diff().dt.days
        big_gaps = diffs[diffs > 365]
        return s[big_gaps.index[-1]] if len(big_gaps) > 0 else s.min()
    _first_bar = {s: _effective_first_bar(s) for s in sp500_prices}
    qqq_ma200    = qqq_df["Close"].rolling(200).mean()
    mr_ma20      = {s: d["Close"].rolling(MR_MA_PERIOD, min_periods=MR_MA_PERIOD).mean()
                   for s, d in nq100_prices.items()}
    sig_by_entry = mr_signals.groupby("entry_date")

    cash = INITIAL_CAPITAL
    c7_pos, mr_pos, mr_gh = {}, {}, {}
    def_alloc = {s: 0.0 for s in defensive_syms}
    nav_rows = []; tc = [0]; c7_ctr = 0

    def mr_ghost_ok(sym, dt, ghost):
        if sym not in nq100_prices or dt not in nq100_prices[sym].index:
            ghost["hold_bars"] += 1; return ghost["hold_bars"] >= MR_MAX_HOLD
        row = nq100_prices[sym].loc[dt]
        bh  = row.get("High", np.nan); bc = row["Close"]
        ma  = mr_ma20[sym].get(dt, np.nan) if sym in mr_ma20 else np.nan
        ghost["hold_bars"] += 1; h = ghost["hold_bars"]
        return ((np.isfinite(bh) and bh >= ghost["entry_px"]*(1+MR_TARGET_PCT)) or
                (h >= 2 and np.isfinite(ma) and bc > ma) or h >= MR_MAX_HOLD)

    for today in all_dates:
        dt      = pd.Timestamp(today)
        dt_str  = dt.strftime("%Y-%m-%d")

        for s in list(mr_gh.keys()):
            if mr_ghost_ok(s, dt, mr_gh[s]): del mr_gh[s]

        # C7 rebalance (weekly)
        do_c7 = (c7_ctr % C7_REBAL_BARS == 0); c7_ctr += 1
        if do_c7 and dt in qqq_df.index:
            prev_dates = [d for d in all_dates if d < dt]
            prev_dt = prev_dates[-1] if prev_dates else dt
            qqq_px  = qqq_df.loc[prev_dt,"Close"] if prev_dt in qqq_df.index else np.nan
            qqq_ma  = qqq_ma200.loc[prev_dt] if prev_dt in qqq_ma200.index else np.nan
            bull    = not np.isnan(qqq_ma) and qqq_px > qqq_ma

            for s, pos in list(c7_pos.items()):
                if s not in sp500_prices or dt not in sp500_prices[s].index: continue
                raw = sp500_prices[s].loc[dt,"Open"]
                if np.isnan(raw) or raw <= 0: continue
                x = xp(raw); sh = pos["shares"]; comm = cm(sh)
                cash += sh*x - comm
                del c7_pos[s]

            # Use prev_dt for ranking: signal must be based on info available at today's open.
            # ret_126d.loc[dt] would include today's close (not yet known at open) — lookahead.
            rank_dt = prev_dt if prev_dt in ret_126d.index else dt
            if bull and rank_dt in ret_126d.index:
                pit = pit_snapshots.get(dt_str, frozenset())
                teq = cash + sum(
                    mr_pos[s]["shares"] * nq100_prices[s].loc[dt,"Close"]
                    for s in mr_pos if s in nq100_prices and dt in nq100_prices[s].index
                    and not np.isnan(nq100_prices[s].loc[dt,"Close"])
                )
                notional = teq * c7_alloc_pct / C7_N_POSITIONS
                avail = ret_126d.loc[rank_dt].dropna()
                if pit: avail = avail[avail.index.isin(pit)]
                # Min-history filter: require stock's first bar to predate rank_dt by ≥18 months.
                # Uses first_valid_index() rather than notna().cumsum() so the filter
                # is FETCH_START-agnostic — established stocks always pass regardless of
                # how far back we loaded data, while genuine spinoffs (e.g. SNDK) are
                # correctly excluded because their merged-dataset history is < 18 months.
                _cutoff = rank_dt - _HISTORY_DAYS
                _old_enough = pd.Series(
                    {s: _first_bar.get(s, rank_dt) < _cutoff for s in avail.index},
                    dtype=bool,
                )
                avail = avail[_old_enough.reindex(avail.index, fill_value=False)]
                if C7_SECTOR_CAP_MAX > 0:
                    # Greedy walk down the ranked list, skipping a name once its
                    # sector bucket already holds C7_SECTOR_CAP_MAX positions.
                    # "Unknown" bucket (unclassified/delisted tickers) is exempt
                    # from the cap -- each unknown name counts as its own
                    # singleton bucket rather than being lumped together.
                    ranked = avail.sort_values(ascending=False)
                    picked, sector_ct = [], {}
                    for s in ranked.index:
                        sec = _SECTOR_MAP.get(s, "Unknown")
                        key = sec if sec != "Unknown" else f"__unk_{s}"
                        if sector_ct.get(key, 0) >= C7_SECTOR_CAP_MAX:
                            continue
                        picked.append(s)
                        sector_ct[key] = sector_ct.get(key, 0) + 1
                        if len(picked) == C7_N_POSITIONS:
                            break
                    selected = picked
                else:
                    selected = list(avail.nlargest(C7_N_POSITIONS).index)
                for rank, s in enumerate(selected):
                    if s not in sp500_prices or dt not in sp500_open.index: continue
                    raw = sp500_open.loc[dt,s] if s in sp500_open.columns else np.nan
                    if np.isnan(raw) or raw <= 0: continue
                    e = ep(raw); sh = notional / e; comm = cm(sh); cost = sh*e + comm
                    if cash < cost: continue
                    cash -= cost; tc[0] += 1
                    c7_pos[s] = {"trade_id":tc[0],"entry_date":dt,"entry_px_raw":raw,
                                 "entry_px":e,"shares":sh,"notional":sh*e,
                                 "entry_comm":comm,"cost_basis":cost,"rank":rank+1}

        # MR exits
        for s in list(mr_pos.keys()):
            pos = mr_pos[s]
            if s not in nq100_prices or dt not in nq100_prices[s].index:
                # ticker delisted / data ended mid-hold
                # floor at stop_price so a delisted stock that gapped past stop
                # doesn't take worse-than-stop losses
                if s in nq100_prices and len(nq100_prices[s]) > 0:
                    last = max(nq100_prices[s].index)
                    raw  = nq100_prices[s].loc[last, "Close"]
                    if np.isfinite(raw):
                        exit_px = max(raw, pos["stop_price"])
                        sh = pos["shares"]; cash += sh*xp(exit_px) - cm(sh)
                del mr_pos[s]
                continue
            row = nq100_prices[s].loc[dt]
            bh  = row.get("High",np.nan); bl = row.get("Low",np.nan); bc = row["Close"]
            ma  = mr_ma20[s].get(dt,np.nan) if s in mr_ma20 else np.nan
            pos["hold_bars"] += 1; hold = pos["hold_bars"]
            xpr, xreason = None, None
            if np.isfinite(bl) and bl <= pos["stop_price"]:
                xpr = xp(pos["stop_price"]); xreason = "Stop Loss"
            elif np.isfinite(bh) and bh >= pos["target_price"]:
                xpr = xp(pos["target_price"]); xreason = "Target"
            elif hold >= 2 and np.isfinite(ma) and bc > ma:
                xpr = xp(bc); xreason = "MA20 Exit"
            elif hold >= MR_MAX_HOLD:
                xpr = xp(bc); xreason = "Max Hold"
            if xpr:
                sh = pos["shares"]; comm = cm(sh)
                cash += sh*xpr - comm
                if xreason == "Stop Loss": mr_gh[s] = {"entry_px":pos["entry_px_raw"],"hold_bars":hold}
                del mr_pos[s]

        # NAV snapshot
        c7_mkt = sum(c7_pos[s]["shares"] * sp500_prices[s].loc[dt,"Close"]
                     for s in c7_pos if s in sp500_prices and dt in sp500_prices[s].index
                     and not np.isnan(sp500_prices[s].loc[dt,"Close"]))
        mr_mkt = sum(mr_pos[s]["shares"] * nq100_prices[s].loc[dt,"Close"]
                     for s in mr_pos if s in nq100_prices and dt in nq100_prices[s].index
                     and not np.isnan(nq100_prices[s].loc[dt,"Close"]))
        def_mkt = sum(def_alloc[s] * etf_prices[s].loc[dt,"Close"]
                      for s in defensive_syms
                      if def_alloc[s] > 0 and s in etf_prices and dt in etf_prices[s].index)
        nav_rows.append({"date": dt, "total_equity": round(cash+c7_mkt+mr_mkt+def_mkt, 2)})

        if weight_sink is not None:
            holdings = []
            for s in c7_pos:
                if s in sp500_prices and dt in sp500_prices[s].index:
                    mv = c7_pos[s]["shares"] * sp500_prices[s].loc[dt,"Close"]
                    if np.isfinite(mv): holdings.append(("c7_momentum", s, mv))
            for s in mr_pos:
                if s in nq100_prices and dt in nq100_prices[s].index:
                    mv = mr_pos[s]["shares"] * nq100_prices[s].loc[dt,"Close"]
                    if np.isfinite(mv): holdings.append(("nq100_mr", s, mv))
            for s in defensive_syms:
                if def_alloc[s] > 0 and s in etf_prices and dt in etf_prices[s].index:
                    mv = def_alloc[s] * etf_prices[s].loc[dt,"Close"]
                    if np.isfinite(mv): holdings.append(("defensive", s, mv))
            weight_sink[dt] = {"nav": cash+c7_mkt+mr_mkt+def_mkt, "cash": cash,
                               "holdings": holdings}

        # MR entries
        if dt in sig_by_entry.groups:
            for _, sig in sig_by_entry.get_group(dt).iterrows():
                s = sig["ticker"]
                if len(mr_pos) >= 5 or s in mr_pos or s in mr_gh: continue
                if s not in nq100_prices or dt not in nq100_prices[s].index: continue
                raw = nq100_prices[s].loc[dt,"Open"]
                if np.isnan(raw) or raw <= 0: continue
                e = ep(raw); alloc = min((cash+c7_mkt+mr_mkt+def_mkt)*0.10, cash)
                sh = alloc/e; notional = sh*e; comm = cm(sh); cost = notional+comm
                if cash < cost: continue
                cash -= cost; tc[0] += 1
                mr_pos[s] = {"trade_id":tc[0],"signal_date":sig["signal_date"],
                             "entry_date":dt,"entry_px_raw":raw,"entry_px":e,
                             "shares":sh,"notional":notional,"entry_comm":comm,
                             "cost_basis":cost,"stop_price":raw*(1-MR_STOP_PCT),
                             "target_price":raw*(1+MR_TARGET_PCT),
                             "hold_bars":0,"signal_rank":int(sig["rank_10d"])}

        # Defensive rebalance (weekly)
        if do_c7 and defensive_syms:
            for s, sh in list(def_alloc.items()):
                if sh > 0 and s in etf_prices and dt in etf_prices[s].index:
                    raw = etf_prices[s].loc[dt,"Close"]
                    if not np.isnan(raw):
                        cash += sh*xp(raw) - cm(sh)
                def_alloc[s] = 0.0
            def_cash = cash
            if def_cash > 100:
                w = 1.0 / len(defensive_syms)
                for s in defensive_syms:
                    if s not in etf_prices or dt not in etf_prices[s].index: continue
                    raw = etf_prices[s].loc[dt,"Close"]
                    if np.isnan(raw) or raw <= 0: continue
                    e = ep(raw); sh = def_cash*w/e; cost = sh*e + cm(sh)
                    if cost > cash: continue
                    cash -= cost; def_alloc[s] = sh

    # close remaining at EOB
    for s, pos in c7_pos.items():
        if s in sp500_prices:
            last = max(sp500_prices[s].index)
            raw = sp500_prices[s].loc[last,"Close"]; sh = pos["shares"]
            cash += sh*xp(raw) - cm(sh)
    for s, pos in mr_pos.items():
        if s in nq100_prices:
            last = max(nq100_prices[s].index)
            raw = nq100_prices[s].loc[last,"Close"]; sh = pos["shares"]
            cash += sh*xp(raw) - cm(sh)
    # close defensive ETF positions (bug fix: was missing, causing artificial EOB nav drop)
    for s, sh in def_alloc.items():
        if sh > 0 and s in etf_prices and len(etf_prices[s]) > 0:
            last = max(etf_prices[s].index)
            raw  = etf_prices[s].loc[last, "Close"]
            if np.isfinite(raw):
                cash += sh*xp(raw) - cm(sh)
    # update last nav row
    if nav_rows:
        nav_rows[-1]["total_equity"] = round(cash, 2)

    eq = pd.DataFrame(nav_rows).set_index("date")["total_equity"]
    return eq


# =============================================================================
# B5 — QQQ (60%) + SP500 MR (40%) inline-computed, PIT-filtered
# =============================================================================

def simulate_b5_pit(sp500_prices, qqq_df, gld_df, pit_snapshots, weight_sink=None) -> pd.Series:
    # weight_sink (opt-in, default None -> no behavior change): see simulate_b1_b2_pit.
    print(f"\n  Simulating B5_PIT [QQQ sleeve + SP500 MR PIT]...", flush=True)

    all_dates = sorted(set(qqq_df.index))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(TEST_START)]

    sp_close  = pd.DataFrame({s: d["Close"] for s, d in sp500_prices.items()})
    sp_open   = pd.DataFrame({s: d["Open"]  for s, d in sp500_prices.items()})
    sp_rsi    = sp_close.apply(lambda c: compute_rsi(c, 14))
    sp_ret10  = sp_close.pct_change(10)
    qqq_ma200 = qqq_df["Close"].rolling(B5_QQQ_MA).mean()
    sp_ma20   = {s: d["Close"].rolling(MR_MA_PERIOD, min_periods=MR_MA_PERIOD).mean()
                 for s, d in sp500_prices.items()}

    cash = INITIAL_CAPITAL; qqq_sh = 0.0; gld_sh = 0.0
    mr_pos = {}; mr_gh = {}
    nav_rows = []; tc = [0]; prev_regime = None

    def sp_ghost_ok(sym, dt, ghost):
        if sym not in sp500_prices or dt not in sp500_prices[sym].index:
            ghost["hold_bars"] += 1; return ghost["hold_bars"] >= MR_MAX_HOLD
        row = sp500_prices[sym].loc[dt]
        bh  = row.get("High",np.nan); bc = row["Close"]
        ma  = sp_ma20[sym].get(dt,np.nan) if sym in sp_ma20 else np.nan
        ghost["hold_bars"] += 1; h = ghost["hold_bars"]
        return ((np.isfinite(bh) and bh >= ghost["entry_px"]*(1+MR_TARGET_PCT)) or
                (h >= 2 and np.isfinite(ma) and bc > ma) or h >= MR_MAX_HOLD)

    for i, today in enumerate(all_dates):
        dt     = pd.Timestamp(today)
        dt_str = dt.strftime("%Y-%m-%d")
        prev_dt = pd.Timestamp(all_dates[i-1]) if i > 0 else dt

        for s in list(mr_gh.keys()):
            if sp_ghost_ok(s, dt, mr_gh[s]): del mr_gh[s]

        qqq_px  = qqq_df.loc[prev_dt,"Close"] if prev_dt in qqq_df.index else np.nan
        qqq_ma  = qqq_ma200.loc[prev_dt] if prev_dt in qqq_ma200.index else np.nan
        bull    = not np.isnan(qqq_ma) and qqq_px > qqq_ma
        if not np.isnan(qqq_ma) and qqq_ma > 0:
            in_tz = abs(qqq_px - qqq_ma) / qqq_ma <= B5_TZ_PCT
        else:
            in_tz = False
        mr_max = B5_MR_TZ_POS if in_tz else B5_MR_MAX_POS
        regime = "BULL" if bull else "BEAR"

        if regime != prev_regime:
            if gld_sh > 0 and gld_df is not None and dt in gld_df.index:
                raw = gld_df.loc[dt,"Open"]
                if not np.isnan(raw): cash += gld_sh*xp(raw) - cm(gld_sh)
                gld_sh = 0.0
            if qqq_sh > 0 and dt in qqq_df.index:
                raw = qqq_df.loc[dt,"Open"] if "Open" in qqq_df.columns else qqq_df.loc[dt,"Close"]
                if not np.isnan(raw): cash += qqq_sh*xp(raw) - cm(qqq_sh)
                qqq_sh = 0.0
            target = INITIAL_CAPITAL * B5_QQQ_ALLOC
            if bull and dt in qqq_df.index:
                raw = qqq_df.loc[dt,"Open"] if "Open" in qqq_df.columns else qqq_df.loc[dt,"Close"]
                if not np.isnan(raw) and raw > 0:
                    e = ep(raw); sh = min(target, cash*0.60)/e; cost = sh*e + cm(sh)
                    if cash >= cost: cash -= cost; qqq_sh = sh
            elif not bull and gld_df is not None and dt in gld_df.index:
                raw = gld_df.loc[dt,"Open"]
                if not np.isnan(raw) and raw > 0:
                    e = ep(raw); sh = min(target, cash*0.60)/e; cost = sh*e + cm(sh)
                    if cash >= cost: cash -= cost; gld_sh = sh
            prev_regime = regime

        # SP500 MR exits
        for s in list(mr_pos.keys()):
            pos = mr_pos[s]
            if s not in sp500_prices or dt not in sp500_prices[s].index:
                # ticker delisted / data ended mid-hold — close at last available price,
                # floored at stop_price so a delisted stock never takes worse-than-stop losses
                if s in sp500_prices and len(sp500_prices[s]) > 0:
                    last = max(sp500_prices[s].index)
                    raw  = sp500_prices[s].loc[last, "Close"]
                    if np.isfinite(raw):
                        exit_px = max(raw, pos["stop_price"])
                        sh = pos["shares"]; cash += sh*xp(exit_px) - cm(sh)
                del mr_pos[s]
                continue
            row = sp500_prices[s].loc[dt]
            bh  = row.get("High",np.nan); bl = row.get("Low",np.nan); bc = row["Close"]
            ma  = sp_ma20[s].get(dt,np.nan) if s in sp_ma20 else np.nan
            pos["hold_bars"] += 1; hold = pos["hold_bars"]
            xpr, xreason = None, None
            if np.isfinite(bl) and bl <= pos["stop_price"]:
                xpr = xp(pos["stop_price"]); xreason = "Stop Loss"
            elif np.isfinite(bh) and bh >= pos["target_price"]:
                xpr = xp(pos["target_price"]); xreason = "Target"
            elif hold >= 2 and np.isfinite(ma) and bc > ma:
                xpr = xp(bc); xreason = "MA20 Exit"
            elif hold >= MR_MAX_HOLD:
                xpr = xp(bc); xreason = "Max Hold"
            if xpr:
                sh = pos["shares"]; cash += sh*xpr - cm(sh)
                if xreason == "Stop Loss": mr_gh[s] = {"entry_px":pos["entry_px_raw"],"hold_bars":hold}
                del mr_pos[s]

        # NAV
        qqq_mkt = qqq_sh * qqq_df.loc[dt,"Close"] if dt in qqq_df.index and qqq_sh > 0 else 0
        gld_mkt = (gld_sh * gld_df.loc[dt,"Close"]
                   if gld_df is not None and dt in gld_df.index and gld_sh > 0 else 0)
        mr_mkt  = sum(mr_pos[s]["shares"] * sp500_prices[s].loc[dt,"Close"]
                      for s in mr_pos if s in sp500_prices and dt in sp500_prices[s].index
                      and not np.isnan(sp500_prices[s].loc[dt,"Close"]))
        total_eq = cash + qqq_mkt + gld_mkt + mr_mkt
        nav_rows.append({"date": dt, "total_equity": round(total_eq,2)})

        if weight_sink is not None:
            holdings = []
            if qqq_sh > 0 and dt in qqq_df.index and np.isfinite(qqq_mkt) and qqq_mkt:
                holdings.append(("qqq", "QQQ", qqq_mkt))
            if gld_sh > 0 and gld_df is not None and dt in gld_df.index and np.isfinite(gld_mkt) and gld_mkt:
                holdings.append(("gld", "GLD", gld_mkt))
            for s in mr_pos:
                if s in sp500_prices and dt in sp500_prices[s].index:
                    mv = mr_pos[s]["shares"] * sp500_prices[s].loc[dt,"Close"]
                    if np.isfinite(mv): holdings.append(("sp500_mr", s, mv))
            weight_sink[dt] = {"nav": total_eq, "cash": cash, "holdings": holdings}

        # SP500 MR entries — PIT filtered inline
        if (dt in sp_ret10.index and dt in sp_rsi.index and len(mr_pos) < mr_max
                and i + 1 < len(all_dates)):
            pit = pit_snapshots.get(dt_str, frozenset())
            rsi_row = sp_rsi.loc[dt]; ret_row = sp_ret10.loc[dt]; px_row = sp_close.loc[dt]
            elig = (rsi_row < 25) & (px_row >= 10) & rsi_row.notna() & ret_row.notna()
            # PIT filter: only consider tickers actually in SP500 on this date
            if pit:
                elig = elig & elig.index.isin(pit)
            for s in mr_pos: elig[s] = False
            for s in mr_gh:  elig[s] = False
            if elig.any():
                cands = ret_row[elig].sort_values().head(mr_max - len(mr_pos))
                next_dt = pd.Timestamp(all_dates[i+1])
                for s in cands.index:
                    if s not in sp500_prices or next_dt not in sp500_prices[s].index: continue
                    raw = sp500_prices[s].loc[next_dt,"Open"]
                    if np.isnan(raw) or raw <= 0: continue
                    e = ep(raw); alloc = min(total_eq*0.10, cash)
                    sh = alloc/e; cost = sh*e + cm(sh)
                    if cash < cost: continue
                    cash -= cost; tc[0] += 1
                    mr_pos[s] = {"trade_id":tc[0],"signal_date":dt,"entry_date":next_dt,
                                 "entry_px_raw":raw,"entry_px":e,"shares":sh,"notional":sh*e,
                                 "entry_comm":cm(sh),"cost_basis":cost,
                                 "stop_price":raw*(1-MR_STOP_PCT),"target_price":raw*(1+MR_TARGET_PCT),
                                 "hold_bars":0,"signal_rank":0}

    return pd.DataFrame(nav_rows).set_index("date")["total_equity"]


# =============================================================================
# SSD_B2_DG20 — vol-gate switcher (vol > 20% -> B2), VT-10% in B5 mode
# =============================================================================

def simulate_ssd_dg20(b5_raw: pd.Series, b2_raw: pd.Series) -> pd.Series:
    common  = b5_raw.index.intersection(b2_raw.index).sort_values()
    b5_r    = INITIAL_CAPITAL * b5_raw.reindex(common) / b5_raw.reindex(common).iloc[0]
    b2_r    = INITIAL_CAPITAL * b2_raw.reindex(common) / b2_raw.reindex(common).iloc[0]
    b5_vol  = b5_raw.reindex(common).pct_change().rolling(SSD_VOL_LOOKBACK).std() * np.sqrt(252) * 100

    cur_eq = INITIAL_CAPITAL; rows = []; in_b2 = False
    for i, date in enumerate(common):
        if i == 0:
            rows.append({"date": date, "total_equity": cur_eq}); continue
        prev  = common[i-1]
        vol   = b5_vol.loc[prev] if prev in b5_vol.index else np.nan
        in_b2 = (not np.isnan(vol) and vol > SSD_VOL_GATE_DG20)

        if in_b2:
            ret = b2_r.loc[date] / b2_r.loc[prev] - 1; scale = 1.0
        else:
            if not np.isnan(vol) and vol > 0:
                scale = min(1.0, SSD_VOL_TARGET / vol)
            else:
                scale = 1.0
            b5_ret  = b5_r.loc[date] / b5_r.loc[prev] - 1
            b2_ret  = b2_r.loc[date] / b2_r.loc[prev] - 1
            ret = scale * b5_ret + (1-scale) * b2_ret

        cur_eq *= (1 + ret)
        rows.append({"date": date, "total_equity": round(cur_eq, 2)})

    return pd.DataFrame(rows).set_index("date")["total_equity"]


# =============================================================================
# S8_DMVC35 — QQQ/MA20 gate: bull -> SSD_B2_DG20, bear -> B1_35+VT12
# =============================================================================

def simulate_s8_pit(ssd_eq: pd.Series, b1_35_vt_eq: pd.Series, qqq_df: pd.DataFrame) -> pd.Series:
    common   = ssd_eq.index.intersection(b1_35_vt_eq.index).sort_values()
    ssd_r    = INITIAL_CAPITAL * ssd_eq.reindex(common) / ssd_eq.reindex(common).iloc[0]
    b1_r     = INITIAL_CAPITAL * b1_35_vt_eq.reindex(common) / b1_35_vt_eq.reindex(common).iloc[0]
    qqq_ma20 = qqq_df["Close"].rolling(S8_QQQ_MA).mean()

    cur_eq = INITIAL_CAPITAL; rows = []
    for i, date in enumerate(common):
        if i == 0:
            rows.append({"date": date, "total_equity": cur_eq}); continue
        prev   = common[i-1]
        qqq_px = qqq_df.loc[prev,"Close"] if prev in qqq_df.index else np.nan
        qqq_ma = qqq_ma20.loc[prev] if prev in qqq_ma20.index else np.nan
        bull   = not np.isnan(qqq_ma) and qqq_px > qqq_ma
        ret    = (ssd_r.loc[date] / ssd_r.loc[prev] - 1) if bull else (b1_r.loc[date] / b1_r.loc[prev] - 1)
        cur_eq *= (1 + ret)
        rows.append({"date": date, "total_equity": round(cur_eq, 2)})

    return pd.DataFrame(rows).set_index("date")["total_equity"]


# =============================================================================
# MR_VG12 — standalone NQ100 MR with Norgate prices + VT-12%
# =============================================================================

def simulate_mr_pit(mr_signals, nq100_prices, test_start: str = MR_TEST_START) -> pd.Series:
    print(f"\n  Simulating MR_VG12_PIT [NQ100 REG2E + Norgate prices]...", flush=True)

    all_dates_set = set()
    for df in nq100_prices.values(): all_dates_set.update(df.index.tolist())
    all_dates = sorted(d for d in all_dates_set if d >= pd.Timestamp(test_start))

    mr_ma20      = {s: d["Close"].rolling(MR_MA_PERIOD, min_periods=MR_MA_PERIOD).mean()
                   for s, d in nq100_prices.items()}
    sig_by_entry = mr_signals.groupby("entry_date")

    cash = INITIAL_CAPITAL; mr_pos = {}; mr_gh = {}; nav_rows = []; tc = [0]

    def ghost_ok(sym, dt, ghost):
        if sym not in nq100_prices or dt not in nq100_prices[sym].index:
            ghost["hold_bars"] += 1; return ghost["hold_bars"] >= MR_MAX_HOLD
        row = nq100_prices[sym].loc[dt]
        bh  = row.get("High",np.nan); bc = row["Close"]
        ma  = mr_ma20[sym].get(dt,np.nan) if sym in mr_ma20 else np.nan
        ghost["hold_bars"] += 1; h = ghost["hold_bars"]
        return ((np.isfinite(bh) and bh >= ghost["entry_px"]*(1+MR_TARGET_PCT)) or
                (h >= 2 and np.isfinite(ma) and bc > ma) or h >= MR_MAX_HOLD)

    for today in all_dates:
        dt = pd.Timestamp(today)

        for s in list(mr_gh.keys()):
            if ghost_ok(s, dt, mr_gh[s]): del mr_gh[s]

        for s in list(mr_pos.keys()):
            pos = mr_pos[s]
            if s not in nq100_prices or dt not in nq100_prices[s].index:
                # ticker delisted / data ended mid-hold
                # use last available price but floor at stop_price so a delisted
                # stock that gapped past the stop doesn't take worse-than-stop losses
                if s in nq100_prices and len(nq100_prices[s]) > 0:
                    last = max(nq100_prices[s].index)
                    raw  = nq100_prices[s].loc[last, "Close"]
                    if np.isfinite(raw):
                        exit_px = max(raw, pos["stop_price"])
                        sh = pos["shares"]; cash += sh*xp(exit_px) - cm(sh)
                del mr_pos[s]
                continue
            row = nq100_prices[s].loc[dt]
            bh  = row.get("High",np.nan); bl = row.get("Low",np.nan); bc = row["Close"]
            ma  = mr_ma20[s].get(dt,np.nan) if s in mr_ma20 else np.nan
            pos["hold_bars"] += 1; hold = pos["hold_bars"]
            xpr, xreason = None, None
            if np.isfinite(bl) and bl <= pos["stop_price"]:
                xpr = xp(pos["stop_price"]); xreason = "Stop Loss"
            elif np.isfinite(bh) and bh >= pos["target_price"]:
                xpr = xp(pos["target_price"]); xreason = "Target"
            elif hold >= 2 and np.isfinite(ma) and bc > ma:
                xpr = xp(bc); xreason = "MA20 Exit"
            elif hold >= MR_MAX_HOLD:
                xpr = xp(bc); xreason = "Max Hold"
            if xpr:
                sh = pos["shares"]; cash += sh*xpr - cm(sh)
                if xreason == "Stop Loss": mr_gh[s] = {"entry_px":pos["entry_px_raw"],"hold_bars":hold}
                del mr_pos[s]

        mr_mkt = sum(mr_pos[s]["shares"] * nq100_prices[s].loc[dt,"Close"]
                     for s in mr_pos if s in nq100_prices and dt in nq100_prices[s].index
                     and not np.isnan(nq100_prices[s].loc[dt,"Close"]))
        nav_rows.append({"date": dt, "total_equity": round(cash + mr_mkt, 2)})

        if dt in sig_by_entry.groups:
            for _, sig in sig_by_entry.get_group(dt).iterrows():
                s = sig["ticker"]
                if len(mr_pos) >= 5 or s in mr_pos or s in mr_gh: continue
                if s not in nq100_prices or dt not in nq100_prices[s].index: continue
                raw = nq100_prices[s].loc[dt,"Open"]
                if np.isnan(raw) or raw <= 0: continue
                e = ep(raw); alloc = min(INITIAL_CAPITAL*0.10, cash)
                sh = alloc/e; notional = sh*e; comm = cm(sh); cost = notional+comm
                if cash < cost: continue
                cash -= cost; tc[0] += 1
                mr_pos[s] = {"trade_id":tc[0],"signal_date":sig["signal_date"],
                             "entry_date":dt,"entry_px_raw":raw,"entry_px":e,
                             "shares":sh,"notional":notional,"entry_comm":comm,
                             "cost_basis":cost,"stop_price":raw*(1-MR_STOP_PCT),
                             "target_price":raw*(1+MR_TARGET_PCT),
                             "hold_bars":0,"signal_rank":int(sig["rank_10d"])}

    return pd.DataFrame(nav_rows).set_index("date")["total_equity"]


# =============================================================================
# MAIN
# =============================================================================

def _s8_leg_fracs(fracs, rec, weight):
    """Accumulate weight * (market_value / leg_nav) per (sleeve, symbol) into fracs."""
    if rec is None or weight == 0:
        return
    leg_nav = rec.get("nav", 0.0)
    if leg_nav <= 0:
        return
    for sleeve, sym, mv in rec["holdings"]:
        fracs[(sleeve, sym)] = fracs.get((sleeve, sym), 0.0) + weight * (mv / leg_nav)


def export_s8_daily_targets(b5_sink, b2_sink, b1_35_sink, raw_b5, raw_b1_35,
                            qqq_df, s8_nav, out_path):
    """Reconstruct S8's per-day target book from the leg weight sinks, using the
    exact same daily gates the engine uses (all prev-day info, no lookahead):
      - S8 switch:  bull = QQQ[t-1] > SMA(QQQ, S8_QQQ_MA=20)[t-1]
      - SSD gate:   in_b2 = b5_vol[t-1] > SSD_VOL_GATE_DG20; else scale = min(1, 10/vol)
      - VT (bear):  vt_scale[t] = clip(VT_TARGET / vol20_b1_35[t-1], <= 1)
    Output columns: date, symbol, sleeve, mode, target_weight, gross_exposure, nav.
    target_weight is the symbol's fraction of S8 NAV; gross_exposure = 1 - cash.
    """
    b5_vol     = raw_b5.pct_change().rolling(SSD_VOL_LOOKBACK).std() * np.sqrt(252) * 100
    dr_b1_35   = raw_b1_35.pct_change().fillna(0)
    vol20_b135 = dr_b1_35.rolling(VT_LOOKBACK).std() * np.sqrt(252) * 100
    vt_scale   = (VT_TARGET_PCT / vol20_b135.shift(1)).clip(upper=1.0).fillna(1.0)
    qqq_ma20   = qqq_df["Close"].rolling(S8_QQQ_MA).mean()

    dates = list(s8_nav.index)
    rows = []
    for i, dt in enumerate(dates):
        if i == 0:
            continue
        prev   = dates[i-1]
        nav    = float(s8_nav.loc[dt])
        qqq_px = qqq_df.loc[prev, "Close"] if prev in qqq_df.index else np.nan
        qqq_ma = qqq_ma20.loc[prev] if prev in qqq_ma20.index else np.nan
        bull   = (not np.isnan(qqq_ma)) and qqq_px > qqq_ma

        fracs = {}
        if bull:
            vol   = b5_vol.loc[prev] if prev in b5_vol.index else np.nan
            in_b2 = (not np.isnan(vol)) and vol > SSD_VOL_GATE_DG20
            if in_b2:
                _s8_leg_fracs(fracs, b2_sink.get(dt), 1.0)
                mode = "bull_SSD(B2)"
            else:
                scale = min(1.0, SSD_VOL_TARGET / vol) if (not np.isnan(vol) and vol > 0) else 1.0
                _s8_leg_fracs(fracs, b5_sink.get(dt), scale)
                _s8_leg_fracs(fracs, b2_sink.get(dt), 1.0 - scale)
                mode = "bull_SSD(B5/B2)"
        else:
            vs = float(vt_scale.loc[dt]) if dt in vt_scale.index else 1.0
            _s8_leg_fracs(fracs, b1_35_sink.get(dt), vs)
            mode = "bear_B1_35xVT"

        gross = sum(fracs.values())
        for (sleeve, sym), frac in sorted(fracs.items()):
            if abs(frac) < 1e-9:
                continue
            rows.append({"date": dt.strftime("%Y-%m-%d"), "symbol": sym,
                         "sleeve": sleeve, "mode": mode,
                         "target_weight": round(frac, 6),
                         "gross_exposure": round(gross, 6),
                         "nav": round(nav, 2)})

    df = pd.DataFrame(rows, columns=["date", "symbol", "sleeve", "mode",
                                     "target_weight", "gross_exposure", "nav"])
    df.to_csv(out_path, index=False)
    return df


def main():
    print("=" * 70)
    print("  All-5 Survivorship-Bias-Free PIT Comparison")
    print("=" * 70)

    if not NORGATE_ROOT: print("ERROR: NORGATE_DATA_ROOT not set"); sys.exit(1)
    if not SP500_REPO:   print("ERROR: SP500_DATA_ROOT not set"); sys.exit(1)

    # ── 1. PIT universes ─────────────────────────────────────────────────────
    print("\n[1] Building PIT universes...")
    from helpers.pit_universe import (
        get_sp500_tickers_in_period, get_nq100_tickers_in_period,
        build_sp500_pit_snapshots,
    )
    sp500_pit = get_sp500_tickers_in_period(FETCH_START, END_DATE, SP500_REPO)
    nq100_pit_blend = get_nq100_tickers_in_period(FETCH_START, END_DATE)
    nq100_pit_mr    = get_nq100_tickers_in_period(MR_FETCH_START, END_DATE)
    print(f"  SP500 PIT (2016-2026): {len(sp500_pit)}")
    print(f"  NQ100 PIT (2016-2026): {len(nq100_pit_blend)}")
    print(f"  NQ100 PIT (2004-2026): {len(nq100_pit_mr)}")

    # ── 2. Load prices ────────────────────────────────────────────────────────
    print("\n[2] Loading Norgate prices...")
    sp500_prices  = load_sp500_prices_norgate(sp500_pit)
    nq100_blend   = load_nq100_prices_norgate(nq100_pit_blend, FETCH_START)
    nq100_mr      = load_nq100_prices_norgate(nq100_pit_mr, MR_FETCH_START)
    etf_prices    = load_etf_norgate(["IAU","TLT","UUP","QQQ","GLD"])
    qqq_df        = etf_prices["QQQ"]
    gld_df        = etf_prices.get("GLD")
    if qqq_df is None: print("ERROR: QQQ missing"); sys.exit(1)

    # ── 3. MR signals ─────────────────────────────────────────────────────────
    print("\n[3] Loading REG2E MR signals...")
    mr_sig = pd.read_parquet(ROOT / "MR" / "outputs" / "signals" / "REG2E_signals.parquet")
    mr_sig["signal_date"] = pd.to_datetime(mr_sig["signal_date"]).dt.tz_localize(None).dt.normalize()
    mr_sig["entry_date"]  = pd.to_datetime(mr_sig["entry_date"]).dt.tz_localize(None).dt.normalize()
    mr_sig = mr_sig.sort_values(["signal_date","rank_10d"]).reset_index(drop=True)
    print(f"  {len(mr_sig):,} signals | {mr_sig['ticker'].nunique()} tickers")

    # ── 4. SP500 PIT snapshots ────────────────────────────────────────────────
    print("\n[4] Building SP500 PIT snapshots...")
    all_dates = sorted(set(qqq_df.index) | set().union(*[set(d.index) for d in nq100_blend.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(TEST_START)]
    rebal_dates = [all_dates[i] for i in range(0, len(all_dates), C7_REBAL_BARS)]
    pit_snapshots = build_sp500_pit_snapshots(
        [d.strftime("%Y-%m-%d") for d in rebal_dates], SP500_REPO
    )
    print(f"  {len(pit_snapshots)} snapshots | avg members: "
          f"{np.mean([len(v) for v in pit_snapshots.values()]):.0f}")

    # ── 5. B1_PIT (C7=40%, IAU/TLT/UUP) ─────────────────────────────────────
    print("\n[5/9] B1_PIT...")
    raw_b1 = simulate_b1_b2_pit(mr_sig, nq100_blend, sp500_prices, qqq_df, etf_prices,
                                  ["IAU","TLT","UUP"], pit_snapshots, "B1_VG12_PIT")
    m_b1_raw = calc_metrics(raw_b1)
    vt_b1 = apply_vt(raw_b1)
    m_b1  = calc_metrics(vt_b1)
    save_nav("B1_VG12_PIT", raw_b1, vt_b1)
    print(f"  B1_VG12_PIT raw:  CAGR={m_b1_raw['cagr']:+.2f}%  MaxDD={m_b1_raw['max_dd']:.2f}%  Calmar={m_b1_raw['calmar']:.3f}")
    print(f"  B1_VG12_PIT VT12: CAGR={m_b1['cagr']:+.2f}%  MaxDD={m_b1['max_dd']:.2f}%  Calmar={m_b1['calmar']:.3f}")

    # ── 6. B2_PIT (C7=40%, TLT/UUP) ──────────────────────────────────────────
    # Opt-in per-day target-book export (EXPORT_DAILY_TARGETS=1) for SignalDeck
    # fidelity: capture the S8 sub-legs' end-of-day holdings (B2 / B5 / B1_35).
    _export    = bool(os.environ.get("EXPORT_DAILY_TARGETS"))
    b2_sink    = {} if _export else None
    b5_sink    = {} if _export else None
    b1_35_sink = {} if _export else None
    print("\n[6/9] B2_PIT...")
    raw_b2 = simulate_b1_b2_pit(mr_sig, nq100_blend, sp500_prices, qqq_df, etf_prices,
                                  ["TLT","UUP"], pit_snapshots, "B2_VG12_PIT",
                                  weight_sink=b2_sink)
    m_b2_raw = calc_metrics(raw_b2)
    vt_b2 = apply_vt(raw_b2)
    m_b2  = calc_metrics(vt_b2)
    save_nav("B2_VG12_PIT", raw_b2, vt_b2)
    print(f"  B2_VG12_PIT raw:  CAGR={m_b2_raw['cagr']:+.2f}%  MaxDD={m_b2_raw['max_dd']:.2f}%  Calmar={m_b2_raw['calmar']:.3f}")
    print(f"  B2_VG12_PIT VT12: CAGR={m_b2['cagr']:+.2f}%  MaxDD={m_b2['max_dd']:.2f}%  Calmar={m_b2['calmar']:.3f}")

    # ── 7. B5_PIT → SSD_B2_DG20_PIT ─────────────────────────────────────────
    print("\n[7/9] B5_PIT -> SSD_B2_DG20_PIT...")
    raw_b5  = simulate_b5_pit(sp500_prices, qqq_df, gld_df, pit_snapshots, weight_sink=b5_sink)
    raw_ssd = simulate_ssd_dg20(raw_b5, raw_b2)
    m_ssd   = calc_metrics(raw_ssd)
    save_nav("SSD_B2_DG20_PIT", raw_ssd)
    print(f"  SSD_B2_DG20_PIT:  CAGR={m_ssd['cagr']:+.2f}%  MaxDD={m_ssd['max_dd']:.2f}%  Calmar={m_ssd['calmar']:.3f}")

    # ── 8. S8_DMVC35_PIT: bull=SSD_DG20, bear=B1_35+VT12 ────────────────────
    print("\n[8/9] S8_DMVC35_PIT (B1_35 bear mode + SSD_DG20 bull mode)...")
    raw_b1_35 = simulate_b1_b2_pit(mr_sig, nq100_blend, sp500_prices, qqq_df, etf_prices,
                                    ["IAU","TLT","UUP"], pit_snapshots, "B1_35_PIT",
                                    c7_alloc_pct=C7_ALLOC_35, weight_sink=b1_35_sink)
    vt_b1_35   = apply_vt(raw_b1_35)
    raw_s8     = simulate_s8_pit(raw_ssd, vt_b1_35, qqq_df)
    m_s8       = calc_metrics(raw_s8)
    save_nav("S8_DMVC35_PIT", raw_s8)
    print(f"  S8_DMVC35_PIT:    CAGR={m_s8['cagr']:+.2f}%  MaxDD={m_s8['max_dd']:.2f}%  Calmar={m_s8['calmar']:.3f}")
    if _export:
        _tgt_path = OUT_BASE / "S8_DMVC35_PIT" / "s8_daily_targets.csv"
        _tdf = export_s8_daily_targets(b5_sink, b2_sink, b1_35_sink, raw_b5, raw_b1_35,
                                       qqq_df, raw_s8, _tgt_path)
        _g = _tdf.groupby("date")["gross_exposure"].first() if len(_tdf) else pd.Series([0.0])
        print(f"  [export] s8_daily_targets.csv: {len(_tdf):,} rows | "
              f"{_tdf['date'].nunique() if len(_tdf) else 0} days | "
              f"{_tdf['symbol'].nunique() if len(_tdf) else 0} symbols | "
              f"gross {_g.min():.2f}-{_g.max():.2f} -> {_tgt_path}")

    # ── 9. MR_VG12_PIT (2004-2026) ───────────────────────────────────────────
    print("\n[9/9] MR_VG12_PIT (Norgate prices, REG2E signals)...")
    raw_mr = simulate_mr_pit(mr_sig, nq100_mr, MR_TEST_START)
    vt_mr  = apply_vt(raw_mr)
    m_mr_raw = calc_metrics(raw_mr)
    m_mr     = calc_metrics(vt_mr)
    save_nav("MR_VG12_PIT", raw_mr, vt_mr)
    print(f"  MR_VG12_PIT raw:  CAGR={m_mr_raw['cagr']:+.2f}%  MaxDD={m_mr_raw['max_dd']:.2f}%  Calmar={m_mr_raw['calmar']:.3f}")
    print(f"  MR_VG12_PIT VT12: CAGR={m_mr['cagr']:+.2f}%  MaxDD={m_mr['max_dd']:.2f}%  Calmar={m_mr['calmar']:.3f}")
    # Sanity check: how many signals actually executed vs expected
    mr_sigs_in_range = mr_sig[(mr_sig["entry_date"] >= pd.Timestamp(MR_TEST_START)) &
                               (mr_sig["entry_date"] <= pd.Timestamp(END_DATE))]
    can_execute = sum(1 for _, r in mr_sigs_in_range.iterrows()
                      if r["ticker"] in nq100_mr and
                      r["entry_date"] in nq100_mr.get(r["ticker"], pd.DataFrame()).index)
    print(f"  MR diagnostic: {len(mr_sigs_in_range)} signals in range, {can_execute} have Norgate data on entry_date")

    # ── 10. Yearly returns from frozen original navs ──────────────────────────
    BASE = ROOT / "output" / "rebuild_blended"
    MR_DIR = ROOT / "output" / "rebuild_MR" / "MR_VG12"

    def load_yr_orig(path, col="total_equity"):
        if not Path(path).exists(): return {}
        nav = pd.read_csv(path, index_col=0, parse_dates=True)
        nav.index = nav.index.tz_localize(None).normalize()
        return yr_rets(nav[col].astype(float))

    yr_orig = {
        "B1_VG12":     load_yr_orig(BASE / "B1_VG12"    / "daily_nav.csv"),
        "B2_VG12":     load_yr_orig(BASE / "B2_VG12"    / "daily_nav.csv"),
        "SSD_B2_DG20": load_yr_orig(BASE / "SSD_B2_DG20" / "daily_nav.csv"),
        "S8_DMVC35":   load_yr_orig(BASE / "S8_DMVC35"  / "daily_nav.csv"),
        "MR_VG12":     load_yr_orig(MR_DIR / "daily_nav.csv", col="vg12_equity"),
    }
    yr_pit = {
        "B1_VG12":     yr_rets(vt_b1),
        "B2_VG12":     yr_rets(vt_b2),
        "SSD_B2_DG20": yr_rets(raw_ssd),
        "S8_DMVC35":   yr_rets(raw_s8),
        "MR_VG12":     yr_rets(vt_mr),
    }
    pit_metrics = {
        "B1_VG12":     m_b1,
        "B2_VG12":     m_b2,
        "SSD_B2_DG20": m_ssd,
        "S8_DMVC35":   m_s8,
        "MR_VG12":     m_mr,
    }

    # ── 11. Print full comparison ─────────────────────────────────────────────
    strats = ["B1_VG12","B2_VG12","SSD_B2_DG20","S8_DMVC35","MR_VG12"]
    print("\n" + "="*70)
    print("  SURVIVORSHIP-BIAS IMPACT - ALL 5 STRATEGIES")
    print("  Original = Polygon + static JSON list (survivors only)")
    print("  PIT      = Norgate + SP500/NQ100 point-in-time universe")
    print("="*70)

    for metric, label in [("cagr","CAGR %"), ("max_dd","MaxDD %"), ("calmar","Calmar"), ("sharpe","Sharpe")]:
        row = f"  {label:<12}"
        for s in strats:
            o = FROZEN[s][metric]; p = pit_metrics[s][metric]; d = p - o
            sign = "+" if d >= 0 else ""
            row += f"  {s[:8]:<8}: {o:>6.2f} -> {p:>6.2f} ({sign}{d:.2f})   "
        print(row[:120])

    print("\n  Summary table:")
    hdr = f"  {'Strategy':<14} {'Orig CAGR':>10} {'PIT CAGR':>10} {'d-CAGR':>8} {'Orig Cal':>9} {'PIT Cal':>9} {'d-Cal':>7}"
    print(hdr)
    print("  " + "-"*75)
    for s in strats:
        o = FROZEN[s]; p = pit_metrics[s]
        print(f"  {s:<14} {o['cagr']:>+10.2f}% {p['cagr']:>+9.2f}% {p['cagr']-o['cagr']:>+7.2f}pp "
              f"{o['calmar']:>9.3f} {p['calmar']:>9.3f} {p['calmar']-o['calmar']:>+7.3f}")

    print("\n  Yearly returns (PIT vs Original):")
    all_years_set: set = set()
    for v in yr_orig.values(): all_years_set.update(v.keys())
    for v in yr_pit.values():  all_years_set.update(v.keys())
    all_years = sorted(all_years_set)

    row_hdr = f"  {'Year':<5}"
    for s in strats: row_hdr += f" {'Orig':>6} {'PIT':>6} {'d':>5}  "
    print(row_hdr[:130])
    print("  " + "-"*125)

    def fmt(v):  return f"{v:>+6.1f}%" if not np.isnan(v) else "   -- "
    def fmd(v):  return f"{v:>+5.1f}" if not np.isnan(v) else "  -- "

    for yr in all_years:
        row = f"  {yr:<5}"
        for s in strats:
            o = yr_orig[s].get(yr, float("nan"))
            p = yr_pit[s].get(yr, float("nan"))
            d = p - o if not (np.isnan(o) or np.isnan(p)) else float("nan")
            row += f" {fmt(o)} {fmt(p)} {fmd(d)}  "
        print(row[:130])

    print("="*70)

    # ── 12. Save comparison CSV ───────────────────────────────────────────────
    rows = []
    for yr in all_years:
        r = {"year": yr}
        for s in strats:
            r[f"{s}_orig"]  = yr_orig[s].get(yr, np.nan)
            r[f"{s}_pit"]   = yr_pit[s].get(yr, np.nan)
            r[f"{s}_diff"]  = r[f"{s}_pit"] - r[f"{s}_orig"] if not (np.isnan(r[f"{s}_orig"]) or np.isnan(r[f"{s}_pit"])) else np.nan
        rows.append(r)

    comp_path = OUT_BASE / "survivorship_comparison_all5.csv"
    pd.DataFrame(rows).to_csv(comp_path, index=False)

    metrics_rows = []
    for s in strats:
        o = FROZEN[s]; p = pit_metrics[s]
        metrics_rows.append({
            "strategy": s,
            "orig_cagr": o["cagr"],  "pit_cagr": p["cagr"],  "delta_cagr": round(p["cagr"]-o["cagr"],2),
            "orig_maxdd": o["max_dd"],"pit_maxdd": p["max_dd"],"delta_maxdd": round(p["max_dd"]-o["max_dd"],2),
            "orig_calmar": o["calmar"],"pit_calmar": p["calmar"],"delta_calmar": round(p["calmar"]-o["calmar"],3),
            "orig_sharpe": o["sharpe"],"pit_sharpe": p["sharpe"],"delta_sharpe": round(p["sharpe"]-o["sharpe"],3),
        })
    pd.DataFrame(metrics_rows).to_csv(OUT_BASE / "metrics_comparison_all5.csv", index=False)

    print(f"\n  CSVs saved to: {OUT_BASE}")
    print("="*70)


if __name__ == "__main__":
    main()
