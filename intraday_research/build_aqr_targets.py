"""
build_aqr_targets.py

NOTE: must be run from inside a checkout of the main july-backtester repo,
dropped into intraday_research/ (it relies on that directory depth to
resolve ROOT, and imports aqr_robustness_tests.py + aqr_alpha_engine_search.py
which already live there) -- not standalone in this promotions/ folder.
Same convention as the other promotion scripts in this repo
(e.g. promotions/S8_DMVC35/rebuild_blended_pit.py).

Builds the live/forward-test target weight book for the AQR residual
momentum sleeve (variant A or B), in the same format as the existing
target books under paper_state/target_books/*.csv:

    date,ticker,target_weight,target_value,sleeves

This is the AQR sleeve's own book -- i.e. the weights for ITS allocated
capital, not the full AQR+MR blend. To run the full 70/30 blend live,
pair this with the existing MR_VG12_PIT live target book at 30% capital
(same pattern S8 uses to net multiple sleeves into one book, except here
the two sleeves are tracked as two separate target-book CSVs, matching
how paper_state/target_books/ already manages B1/B2/MR as separate files).

Does NOT place orders. Does NOT touch Alpaca. Pure target-weight computation.

Signal logic is byte-for-byte the same code path as aqr_b_equity_cache.py
(same data loaders, same residual-momentum math, same SMA175 bear gate) --
re-implementing this from scratch would reopen the exact bug class
(off-by-indexing on rebalance dates) that was caught and fixed in that
script. AQR-A here uses the base SMA175 gate only, WITHOUT the RULE+GBM
crash overlay -- consistent with the backtest finding that the overlay is
additive, not load-bearing (see README §4 Parameter Sensitivity).

AQR-B ONLY also carries the timegate stop-loss from
intraday_research/aqr_b_stop_loss_share/aqr_b_new_baseline_report.py
(STOP_PCT=10%, GATE_FRAC=70%) -- per-name, per-rebalance-period: once a
name is down >= 10% from its entry price (the close at the start of the
current rebalance period) AND >= 70% of the period has elapsed, it is
permanently zeroed for the rest of that period (no redistribution to
survivors -- matches the backtest's cash-drag behaviour exactly).
AQR-A does not get this stop (confirmed not to help there).

The backtest can compute the stop exactly because it knows each period's
full length in hindsight (frac = arange(L)/(L-1) with real L = d1-d0).
Live, the current period hasn't ended yet, so its final length is
unknown. This live port approximates the expected period length as
HOLD_K*21 trading days -- the same 21-trading-days/month convention this
file already uses for fm_bars/sk_bars -- and computes how far into that
estimate today sits. Real quarters run ~60-64 trading days vs the 63-day
estimate, so the gate-arming day (~70% of the period) shifts by at most a
day or two versus what the backtest would have used for this specific
period once it's known in full -- an acceptable, disclosed approximation
that avoids re-deriving it after the fact.

Usage:
    rtk python intraday_research/build_aqr_targets.py B --capital 70000
    rtk python intraday_research/build_aqr_targets.py A --capital 70000
"""
import sys
import argparse
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
import importlib.util as _ilu


def _load(m, f):
    s = _ilu.spec_from_file_location(m, Path(__file__).parent / f)
    mod = _ilu.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


_art = _load("art", "aqr_robustness_tests.py")
_aes = _load("aes", "aqr_alpha_engine_search.py")

build_pit_universe = _art.build_pit_universe
get_pit_members = _art.get_pit_members
get_all_ever_members = _art.get_all_ever_members
load_merged_prices = _art.load_merged_prices
compute_eligibility = _art.compute_eligibility
get_month_ends = _art.get_month_ends
_cap_weights_np = _aes._cap_weights_np

PARAMS = {
    "A": dict(FM=3, HOLD_K=1, TOP_N=10),   # 3M formation, monthly rebal, top 10
    "B": dict(FM=9, HOLD_K=3, TOP_N=25),   # 9M formation, quarterly rebal, top 25
}
SK = 1
W_BETA = 126
SMA_W = 175

# Timegate stop-loss (AQR-B only) -- see aqr_b_stop_loss_share/aqr_b_new_baseline_report.py
STOP_PCT = 0.10
GATE_FRAC = 0.70

OUT_DIR = ROOT / "paper_state" / "target_books"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=["A", "B"])
    ap.add_argument("--capital", type=float, default=70_000.0,
                     help="Capital allocated to this sleeve (default $70k = 70%% of a $100k blend)")
    ap.add_argument("--out", type=str, default=None,
                     help="Override output path (default: paper_state/target_books/AQR_<variant>_VG.csv)")
    args = ap.parse_args()

    variant = args.variant
    p = PARAMS[variant]
    FM, HOLD_K, TOP_N = p["FM"], p["HOLD_K"], p["TOP_N"]

    print("=" * 70)
    print(f"  AQR-{variant} Live Target Book Builder  (FM={FM}M, hold={HOLD_K}mo, TOP_N={TOP_N})")
    print("=" * 70)

    # ── 1. Data ──────────────────────────────────────────────────────────────
    print("\n[1] Loading universe + prices...")
    pit_events = build_pit_universe()
    all_members = get_all_ever_members(pit_events)
    close, dvol = load_merged_prices(sorted(set(all_members) | {"SPY", "QQQ"}))
    cols = list(close.columns)
    K = len(cols)
    daily_idx = close.index
    D = len(daily_idx)

    spy = close["SPY"].reindex(daily_idx, method="ffill").ffill()
    qqq = close["QQQ"].reindex(daily_idx, method="ffill").ffill()
    px_arr = close.values.astype(float)
    spy_arr = spy.values.astype(float)
    as_of = daily_idx[-1]
    print(f"  {D} days  |  {K} tickers  |  as-of {as_of.date()}")

    # ── 2. Month-ends + this variant's rebalance schedule ───────────────────
    print("\n[2] Building month-ends + rebalance schedule...")
    me_idx = pd.DatetimeIndex(get_month_ends(daily_idx))
    me_ts = [pd.Timestamp(d) for d in me_idx]
    M = len(me_ts)
    rebal = [i for i in range(M) if i % HOLD_K == 0]
    current_rebal_i = rebal[-1]
    current_rebal_t = me_ts[current_rebal_i]
    print(f"  {M} month-ends  |  current book set at rebalance {current_rebal_t.date()}")

    # ── 3. PIT mask, eligibility, dvol (at the current rebalance date) ──────
    print("\n[3] PIT mask + eligibility at current rebalance date...")
    dvol_63 = dvol.rolling(63, min_periods=30).median()
    dvol_me_row = dvol_63.reindex([current_rebal_t], method="ffill").values[0].astype(float)
    elig_row = compute_eligibility(close, dvol_63).reindex([current_rebal_t], method="ffill").fillna(False).values[0].astype(bool)
    members = get_pit_members(pit_events, current_rebal_t)
    pit_row = np.fromiter((c in members for c in cols), dtype=bool, count=K)
    valid_row = elig_row & ~np.isnan(dvol_me_row) & (np.nan_to_num(dvol_me_row) > 0)

    # ── 4. Bear filter — MONTHLY convention matching the backtest ────────────
    # aqr_b_equity_cache.py evaluates SMA175 at month-ends and forward-fills
    # daily (bear_me -> bear_d). The live builder must match: read the most
    # recent month-end reading, NOT iloc[-1] (which would be a daily check and
    # would flip on intra-month crosses that the backtest ignores).
    print("[4] SMA175 bear filter (last month-end reading, matching backtest)...")
    sma175s = spy.rolling(SMA_W, min_periods=120).mean()
    sma175q = qqq.rolling(SMA_W, min_periods=120).mean()
    last_me_t = me_ts[-1]   # most recent month-end (already computed in step 2)
    spy_me    = float(spy.reindex([last_me_t], method="ffill").iloc[0])
    qqq_me    = float(qqq.reindex([last_me_t], method="ffill").iloc[0])
    sma175s_me = float(sma175s.reindex([last_me_t], method="ffill").iloc[0])
    sma175q_me = float(sma175q.reindex([last_me_t], method="ffill").iloc[0])
    is_bear = bool((spy_me < sma175s_me) or (qqq_me < sma175q_me))
    print(f"  Last month-end : {last_me_t.date()}")
    print(f"  SPY={spy_me:.2f} vs SMA175={sma175s_me:.2f}  |  "
          f"QQQ={qqq_me:.2f} vs SMA175={sma175q_me:.2f}  ->  "
          f"{'BEAR (-> SHY)' if is_bear else 'BULL (-> momentum book)'}")

    # ── 5. Beta + residual momentum signal at the current rebalance date ────
    print(f"\n[5] {FM}M residual momentum at {current_rebal_t.date()}...")
    daily_ret_np = np.diff(np.log(np.where(px_arr > 0, px_arr, np.nan)), axis=0)
    spy_ret_np = np.diff(np.log(np.where(spy_arr > 0, spy_arr, np.nan)))
    loc = daily_idx.get_loc(current_rebal_t)

    beta_row = np.zeros(K)
    if loc >= W_BETA:
        ws = spy_ret_np[loc - W_BETA:loc]
        wk = daily_ret_np[loc - W_BETA:loc]
        vs = np.nanvar(ws)
        if vs >= 1e-10:
            cov = np.nanmean(wk * ws[:, None], axis=0) - np.nanmean(wk, axis=0) * np.nanmean(ws)
            beta_row = cov / vs

    fm_bars = FM * 21
    sk_bars = SK * 21
    sig_row = np.full(K, np.nan)
    if loc >= fm_bars + sk_bars:
        raw = px_arr[loc - sk_bars] / px_arr[loc - fm_bars - sk_bars] - 1.
        spr = spy_arr[loc - sk_bars] / spy_arr[loc - fm_bars - sk_bars] - 1.
        sig_row = raw - beta_row * spr

    # ── 6. Select top-N + dollar-vol weight + 5% cap ─────────────────────────
    print(f"\n[6] Selecting top-{TOP_N} + dollar-volume weighting...")
    v = valid_row & ~np.isnan(sig_row) & pit_row
    idx = np.where(v)[0]
    if not idx.size:
        print("  WARNING: no eligible names -- empty momentum book (will fall through to SHY if bear, else cash)")
        weights = np.zeros(K)
    else:
        k = min(TOP_N, idx.size)
        top = np.argpartition(-sig_row[idx], min(k - 1, idx.size - 1))[:k]
        sel_idx = idx[top]
        raw_dvol = dvol_me_row[sel_idx].astype(float)
        raw_dvol = np.where(np.isnan(raw_dvol) | (raw_dvol <= 0), 1e-9, raw_dvol)
        weights = np.zeros(K)
        weights[sel_idx] = _cap_weights_np(raw_dvol)

    # ── 6b. Timegate stop-loss (AQR-B only) ──────────────────────────────────
    stopped_idx = np.array([], dtype=int)
    if variant == "B" and not is_bear:
        active_idx = np.where(weights > 0)[0]
        entry_price = px_arr[loc, active_idx]
        good = entry_price > 0
        active_idx = active_idx[good]
        entry_price = entry_price[good]
        if active_idx.size:
            today_loc = D - 1
            n_elapsed = today_loc - loc + 1  # entry day through today, inclusive
            price_block = px_arr[loc:today_loc + 1, active_idx]
            dd = price_block / entry_price[None, :] - 1.0
            l_expected = HOLD_K * 21
            frac = np.clip(np.arange(n_elapsed) / max(l_expected - 1, 1), 0.0, 1.0)[:, None]
            breach_raw = (dd <= -STOP_PCT) & (frac >= GATE_FRAC)
            breached_ever = breach_raw.any(axis=0)
            stopped_idx = active_idx[breached_ever]
            print(f"\n[6b] Timegate stop-loss check (stop={STOP_PCT:.0%}, gate={GATE_FRAC:.0%}, "
                  f"{n_elapsed}/{l_expected} est. days into period)...")
            if stopped_idx.size:
                for i in stopped_idx:
                    j = np.where(active_idx == i)[0][0]
                    print(f"  STOPPED OUT: {cols[i]}  (dd={dd[-1, j]:+.1%} from entry {entry_price[j]:.2f})")
                weights[stopped_idx] = 0.0
            else:
                print("  No names breached the stop.")

    # ── 7. Build target book ──────────────────────────────────────────────────
    print("\n[7] Building target book...")
    rows = []
    if is_bear:
        rows.append({
            "date": as_of.strftime("%Y-%m-%d"),
            "ticker": "SHY",
            "target_weight": 1.0,
            "target_value": round(args.capital, 2),
            "sleeves": f"AQR_{variant}_SP500_PIT_BEAR",
        })
    else:
        nz = np.where(weights > 0)[0]
        nz = nz[np.argsort(-weights[nz])]
        for i in nz:
            w = float(weights[i])
            rows.append({
                "date": as_of.strftime("%Y-%m-%d"),
                "ticker": cols[i],
                "target_weight": round(w, 6),
                "target_value": round(w * args.capital, 2),
                "sleeves": f"AQR_{variant}_SP500_PIT",
            })

    book = pd.DataFrame(rows)
    out_path = Path(args.out) if args.out else OUT_DIR / f"AQR_{variant}_VG.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    book.to_csv(out_path, index=False)

    print(f"\n  Regime         : {'BEAR -> 100% SHY' if is_bear else f'BULL -> {len(rows)} names'}")
    wsum_note = (f"(should be ~1.0)" if stopped_idx.size == 0
                 else f"(<1.0 expected -- {stopped_idx.size} name(s) stopped out, cash drag, not redistributed)")
    print(f"  Weight sum     : {book['target_weight'].sum():.4f}  {wsum_note}")
    print(f"  Rebalance date : {current_rebal_t.date()}  (next rebalance at month-end #{current_rebal_i + HOLD_K if current_rebal_i + HOLD_K < M else 'TBD'})")
    print(f"  Saved          : {out_path}")
    print("\n  This is a TARGET WEIGHT BOOK ONLY. No orders were placed.")
    print(f"  To run the full 70/30 blend live: combine this book at {args.capital/1000:.0f}k capital")
    print(f"  with the existing MR_VG12_PIT live book at the matching 30%% capital split.")


if __name__ == "__main__":
    main()
