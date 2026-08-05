"""Gate-2 (execution honesty) surgical test for the ADOPTED ORB combined v3
(orb_combined_base.py: 15m-OR retest-confirm + trend-align + mag>=0.15% +
1.0%-of-price stop, hold to RTH close).

Per STRATEGY_REALITY_CHECKLIST.md Gate 2, after the Sleeve A kill
(composite/v2/SLEEVE_A_EXECUTION_KILL_REPORT.md). ORB v3's structural
exposure is FILLS ONLY: the stop never moves after entry (no arming states,
no protection-free windows), entry is a real next-5m-bar open, EOD exit is a
real close. But the stop scan fills at the theoretical level
(`exit_px = stop_px`) on the breaching 5m bar, with no gap-aware worse-of and
no tick rounding.

Surgical changes (nothing else touched):
  S1. Stop scan at 1-minute granularity: first 1-min bar from the entry
      minute onward whose low/high breaches the stop; fill =
      worse-of(stop level, that minute's open).
  S2. Stop level rounded to the 0.25 tick grid (long stop floored, short
      stop ceiled -- same convention as the Sleeve A harness).

ORIGINAL baseline is produced by importing run_orb_combined_base() itself and
must reproduce the locked v3 numbers (2021-2026: N=136 WR=63.4% Exp=+50.98
PF=2.34) before the deltas mean anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orb_combined_base as ob  # noqa: E402
from intraday_lib_long import load_rth_long  # noqa: E402
from ema_crossover_test import build_bars5, stats  # noqa: E402

TICK = 0.25


def _floor_tick(x):
    return np.floor(x / TICK) * TICK


def _ceil_tick(x):
    return np.ceil(x / TICK) * TICK


def run_orb_combined_surgical():
    """Copy of ob.run_orb_combined_base() with the stop resolved honestly on
    the session's 1-minute rows instead of at-level on 5m bars."""
    rth = load_rth_long()
    bars = build_bars5(rth)
    ont = ob._build_overnight_table()

    rth_by_sess = dict(tuple(rth.groupby("session", sort=False)))

    rows = []
    counters = dict(n_gap_fill=0, gap_slip_pts=0.0, worst_gap_pts=0.0,
                    n_stop=0, n_stop_orig_would_miss=0)
    for sess, day in bars.groupby("session", sort=False):
        if sess not in ont.index:
            continue
        d5 = day.reset_index(drop=True)
        res = ob._orb_retest_confirm(d5)
        if res is None:
            continue
        drc, entry_ts, entry_px, _orig_exit_px, _orig_stopped = res
        row = ont.loc[sess]
        trend_aligned = (drc == 1 and row["above_both"]) or (drc == -1 and row["below_both"])
        mag_pct = row["rise_pct"] if drc == 1 else row["decline_pct"]
        if not (trend_aligned and mag_pct >= ob.MAG_TH):
            continue

        # --- surgical stop resolution on 1-min rows ---
        raw_stop = entry_px * (1 - ob.STOP_PCT) if drc == 1 else entry_px * (1 + ob.STOP_PCT)
        stop_px = _floor_tick(raw_stop) if drc == 1 else _ceil_tick(raw_stop)
        m = rth_by_sess[sess]
        m = m[m.index >= entry_ts]  # 1-min frame is datetime-indexed; entry_ts is the 5m bar's first minute
        mo = m["open"].to_numpy(float)
        mh = m["high"].to_numpy(float)
        ml = m["low"].to_numpy(float)
        mc = m["close"].to_numpy(float)

        exit_px = mc[-1] if len(mc) else float(d5["close"].iloc[-1])
        stopped = False
        for k in range(len(m)):
            breach = (ml[k] <= stop_px) if drc == 1 else (mh[k] >= stop_px)
            if breach:
                fill = min(stop_px, mo[k]) if drc == 1 else max(stop_px, mo[k])
                if fill != stop_px:
                    counters["n_gap_fill"] += 1
                    gap = abs(fill - stop_px)
                    counters["gap_slip_pts"] += gap
                    counters["worst_gap_pts"] = max(counters["worst_gap_pts"], gap)
                exit_px, stopped = fill, True
                counters["n_stop"] += 1
                break
        pnl = (exit_px - entry_px) * drc - ob.COST
        rows.append({"session": sess, "dir": drc, "entry": entry_px,
                     "exit": exit_px, "pnl": pnl, "mag_pct": mag_pct,
                     "stopped": stopped})
    out = pd.DataFrame(rows)
    out["session"] = pd.to_datetime(out["session"])
    return out.set_index("session").sort_index(), counters


def _report(label, trades):
    valid = trades.loc["2021-01-01":"2026-07-16"]
    s = stats(valid["pnl"])
    dev = valid.loc[:"2023-12-31"]
    tst = valid.loc["2024-01-01":]
    sd, stt = stats(dev["pnl"]), stats(tst["pnl"])
    print(f"\n-- {label} --")
    print(f"  2021-2026: N={s['N']} WR={s['WR']:.1%} Exp={s['Exp']:+.2f} "
          f"PF={s['PF']:.2f} Total={s['Total']:+.0f} MaxDD={s['MaxDD']:.0f}")
    print(f"  dev 21-23: N={sd['N']} PF={sd['PF']:.2f} Exp={sd['Exp']:+.2f} | "
          f"test 24-26: N={stt['N']} PF={stt['PF']:.2f} Exp={stt['Exp']:+.2f}")
    for yr, grp in valid.groupby(valid.index.year):
        ys = stats(grp["pnl"])
        print(f"    {yr}: N={ys['N']} WR={ys['WR']:.1%} Exp={ys['Exp']:+.2f} "
              f"PF={ys['PF']:.2f} Total={ys['Total']:+.0f}")
    return s


def main():
    print("ORIGINAL (imported run_orb_combined_base)...")
    orig = ob.run_orb_combined_base()
    so = _report("ORIGINAL (v3 as adopted)", orig)

    print("\nSURGICAL (1-min gap-aware stop fills + tick-grid stop)...")
    surg, ctr = run_orb_combined_surgical()
    ss = _report("SURGICAL", surg)
    print(f"\n  counters: {ctr}")

    # per-trade fill deltas on the common valid window
    ov = orig.loc["2021-01-01":"2026-07-16"]
    sv = surg.loc["2021-01-01":"2026-07-16"]
    j = ov[["dir", "entry", "exit", "pnl", "stopped"]].join(
        sv[["exit", "pnl", "stopped"]], rsuffix="_s", how="inner")
    diff = j[abs(j["pnl"] - j["pnl_s"]) > 0.01]
    print(f"\n  trades with changed P&L: {len(diff)} / {len(j)}")
    if len(diff):
        diff = diff.assign(delta=diff["pnl_s"] - diff["pnl"]).sort_values("delta")
        print(diff[["dir", "entry", "exit", "exit_s", "pnl", "pnl_s", "delta"]].head(10).to_string())
        print(f"  total P&L delta (surgical - original): {diff['delta'].sum():+.2f} pts "
              f"over {len(j)} trades ({so['Total']:+.0f} -> {ss['Total']:+.0f})")


if __name__ == "__main__":
    main()
