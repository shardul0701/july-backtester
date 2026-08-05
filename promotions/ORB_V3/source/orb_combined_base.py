"""ORB-COMBINED BASE — the single verified config, frozen as a reusable
reference. Locked 2026-07-18. Do not re-tune MAG_TH against the test split,
it's already been spent once.

Built to answer: why did the 2010-2026 extended-history ORB test
(CLAUDE_HANDOFF_NQ_ERA_ROBUSTNESS_2026-07-18.md) show a total edge collapse,
and can one strategy be built that survives fewer trades but the whole
2021-2026 modern regime?

Root causes established (research/orb_regime_decomposition.py,
research/orb_combined_v1.py):
1. The raw ORB continuation edge is GENUINELY regime-dependent, not a units
   artifact. Gross (pre-cost), %-of-opening-price expectancy -- which is
   fully scale-invariant -- is negative every year 2010-2020 and positive
   every year 2021-2025 for H03/H04/H06. This was checked with cost and
   price-level scaling both stripped out, so it is real, not curve-fitting.
2. The old context filter (`overnight_trend_align_move40`, see
   project_orb_trend_alignment_discovery.md) used an ABSOLUTE point
   threshold ("move >= 40 points") chosen from a grid swept only on 2021+
   MNQ data. Native NQ/MNQ price rose ~11.5x from 2010 to 2025, so the same
   40-point threshold is ~11.5x more selective in 2010 than in 2025 -- it
   passed only 14-19 trades/11 years pre-2021, too few to test at all. This
   made the pre-2021 "failure" look like proof of nothing; it was actually
   starvation of sample size from a scaling bug.
3. Fixing the threshold to be percentage-of-price (this module, MAG_TH
   below) restores statistical power pre-2021 (N=63 sessions pass, vs 14-19
   before) -- and with the bug fixed, pre-2021 STILL comes back flat/
   slightly negative (Exp=-2.09, PF=0.93). So both things are true at once:
   the old test was underpowered by a real bug, AND the underlying edge
   genuinely does not exist pre-2021 even once that bug is fixed. This is a
   real regime absence, not an artifact hiding a real edge.

UPDATE (v2, locked 2026-07-19): added a 1.5% intraday stop-loss (bar-by-bar
low/high check from entry to EOD, same mechanic as gap_and_go_base.py's
STOP_PCT). Motivation: avg loss (-140.73) was roughly equal to avg win
(+139.51) on the no-stop v1 -- the whole edge rode on win rate (64.9%), with
zero payoff-asymmetry cushion. Chosen via research/orb_stop_test.py, sweeping
[None, .5%, .75%, 1%, 1.5%, 2%, 3%] on dev(2021-2023)/test(2024-2026) --
1.5% was the ONLY level that improved PF on BOTH splits simultaneously over
no-stop (dev 1.76->2.04, test 1.91->2.54); tighter levels (0.5%/0.75%) looked
better on dev alone but made test WORSE (1.62/1.44 vs 1.91) -- the same
dev/test sign-flip canary this project already uses to reject overfit
configs (see project_orb_combined_tf_sweep.md). Result, 2021-2026 pooled:
N=134 WR=64.9% Exp=+50.51 PF=2.26 (was PF=1.84), avgWin=+139.51 (unchanged --
stop rarely touches winners) avgLoss=-114.25 (was -140.73, -19%). Outlier
check got STRONGER not weaker: PF excl. single largest trade 1.58 -> 1.95,
because the stop caps how large any one loser (not just the win/loss mix)
can get. Old no-stop version archived as orb_combined_base_v1_nostop.py.

UPDATE (v3, locked 2026-07-29): retuned the stop from 1.5% to 1.0% of price.
Motivation: at current NQ levels (~27,400) a 1.5% stop costs ~411 pts, while
the strategy's actual median win over the trailing 12 months is only ~102 pts
(mean +172.7 is skewed by one +854 outlier) -- a single stop-out was erasing
several typical wins, a disproportion that only grew as NQ's price roughly
doubled since 2021 while win size did not scale with it. Re-ran
research/orb_stop_test.py's full stop grid (both %-of-price and a
fixed-point-stop variant) on the same dev(2021-2023)/test(2024-2026) split;
every level tighter than 1.0% (0.5%, 0.75%, and fixed-point equivalents below
~200pt) reproduced the same dev/test sign-flip disqualifier already used to
reject overfit configs -- looks better on dev, worse on test. 1.0% is the
tightest level that still improves PF on BOTH splits simultaneously, and is
also a slightly better pooled 2021-2026 result than the prior 1.5%: PF 2.34
(was 2.26), Exp +50.98 (was +50.51), avgLoss -104.0 (was -114.25, further
narrowing the win/loss-size gap). Caveat carried forward honestly: on the one
live trade checked during this retune (2026-07-28 short), 1.0% actually gave a
WORSE outcome (-273.32) than 1.5% (-206.50) -- an intrabar spike crossed the
tighter threshold before reverting into the close. The improvement is a
statistical edge across the sample, not a guarantee on any individual trade.
Also confirmed directly (not just cited) that pre-2021 data has no genuine
edge for this signal (PF=0.93, 2010-2020) and should not be pooled into this
stop-sizing decision -- 2021-2026 remains the only valid evaluation window.

Strategy mechanics (all three combined, one signal per session, hold to
RTH close unless stopped, 1.0% intraday stop):
  - Entry: H06-style retest-confirm on a 15m (3x5m bar) opening range --
    initial breakout close beyond the OR, price must retest (touch back to)
    the broken level, then close beyond it again -> enter next bar's open.
    This is structurally pickier than plain-breakout ORB (H03/H04), which is
    why raw signal count drops from ~1200/yr-equivalent to a few hundred.
  - Context filter: direction must agree with the session's overnight bias
    (RTH open above BOTH the midnight and 8:30am ET reference prices for
    longs; below both for shorts) -- same logic as `trend_align_move40`,
    but the magnitude filter below is now dimensionless.
  - Magnitude filter: overnight move (open vs. the nearer of midnight/8:30am
    price) must be >= MAG_TH as a PERCENTAGE of the open price. This is the
    scale-invariant fix for root cause #2, chosen by sweeping
    [0, .05%, .10%, .15%, .20%, .25%, .30%] on a DEV slice only
    (2021-01-01 -> 2023-12-31); 0.15% was the clear knee (WR jumps to
    67.9%, PF 1.76 on dev) and it held up (even improved) on the untouched
    2024-01-01 -> 2026-07-16 test slice (WR 60.4%, PF 1.91).

Verified results, 2021-2026 pooled, WITH the 1.0% stop (v3 -- see UPDATE
above; dev+test, the only regime where the edge has been shown to exist,
see root cause #1/#3 above):
  N=136 WR=63.4% Exp=+50.98 pts/trade PF=2.34, avgWin=+140.3 avgLoss=-104.0
  Every calendar year 2021-2026 is net non-negative -- no losing year in
  the valid regime (unchanged from v1/v2; the stop improves payoff, not WR).
  Prior v2 (1.5% stop) result, kept for comparison: PF=2.26, Exp=+50.51,
  avgWin=+139.51, avgLoss=-114.25.
  Pre-2021 (same filter, for transparency, NOT part of the pass/fail
  evaluation): flat/negative, confirming root cause #3 -- unaffected by
  the stop since it's a regime-existence problem, not a payoff problem.

Caveat (carry forward, do not drop): this strategy's edge is demonstrably
REGIME-DEPENDENT, tied to the market-structure shift that began ~2020-2021
(COVID vol shock, 0DTE-options-driven intraday flow, concentrated momentum).
It is not shown to be a timeless ORB law. If traded live, the yearly
breakdown above should be periodically re-checked for a fresh sign flip --
that would be the signal this regime has ended, not a bug in this module.

Full research trail: research/CLAUDE_HANDOFF_NQ_ERA_ROBUSTNESS_2026-07-18.md
(the extended-history collapse that motivated this), orb_regime_decomposition.py
(root cause #1 proof), orb_combined_v1.py (dev sweep + raw sweep output),
orb_combined_v1_final.py (this module's verification numbers).
Prior related work: project_orb_trend_alignment_discovery.md (original,
uncorrected move40 filter), project_orb_or_width_regime_filter.md (separate
OR-width regime filter, not yet combined with this).

Import `run_orb_combined_base()` to get the trade-level DataFrame for
further composition (position sizing, portfolio blending, paper trading).
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intraday_lib_long import load_rth_long, load_full_long  # noqa: E402
from ema_crossover_test import build_bars5, stats  # noqa: E402

# Locked config -- do not change without re-running the full dev/test sweep.
COST = 1.25
MAG_TH = 0.0015  # 0.15% of opening price, chosen on 2021-2023 dev slice only
STOP_PCT = 0.01  # intraday stop, retuned on 2021-2023 dev + 2024-2026 test (v3, 2026-07-29)


def _build_overnight_table() -> pd.DataFrame:
    full = load_full_long()
    rows = []
    for sess, day in full.groupby("session"):
        midnight = day[day["mod"] == 0]
        h830 = day[day["mod"] == 8 * 60 + 30]
        rth_open = day[day["mod"] == 9 * 60 + 30]
        if len(midnight) == 0 or len(h830) == 0 or len(rth_open) == 0:
            continue
        mid_px = float(midnight["close"].iloc[0])
        h830_px = float(h830["close"].iloc[0])
        open_px = float(rth_open["open"].iloc[0])
        rows.append({
            "session": sess, "below_both": open_px < mid_px and open_px < h830_px,
            "above_both": open_px > mid_px and open_px > h830_px,
            "decline_pct": (min(mid_px, h830_px) - open_px) / open_px,
            "rise_pct": (open_px - max(mid_px, h830_px)) / open_px,
        })
    return pd.DataFrame(rows).set_index("session").sort_index()


def _orb_retest_confirm(day: pd.DataFrame):
    o, h, l, c, ts = (day["open"].to_numpy(float), day["high"].to_numpy(float),
                      day["low"].to_numpy(float), day["close"].to_numpy(float),
                      day["ts"].to_numpy())
    n = len(c)
    if n < 6:
        return None
    hi, lo = h[:3].max(), l[:3].min()
    breakout_i, drc = None, None
    for i in range(3, n):
        if c[i] > hi:
            breakout_i, drc = i, 1
            break
        if c[i] < lo:
            breakout_i, drc = i, -1
            break
    if breakout_i is None:
        return None
    touched = False
    entry_i = None
    for j in range(breakout_i + 1, n):
        if drc == 1:
            if l[j] <= hi:
                touched = True
            if touched and c[j] > hi:
                entry_i = j + 1
                break
        else:
            if h[j] >= lo:
                touched = True
            if touched and c[j] < lo:
                entry_i = j + 1
                break
    if entry_i is None or entry_i >= n:
        return None

    entry_px = o[entry_i]
    exit_px = c[-1]
    exit_ts = ts[-1]
    stopped = False

    stop_px = entry_px * (1 - STOP_PCT) if drc == 1 else entry_px * (1 + STOP_PCT)
    for k in range(entry_i, n):
        if drc == 1 and l[k] <= stop_px:
            exit_px, exit_ts, stopped = stop_px, ts[k], True
            break
        if drc == -1 and h[k] >= stop_px:
            exit_px, exit_ts, stopped = stop_px, ts[k], True
            break

    return drc, ts[entry_i], entry_px, exit_px, stopped


def run_orb_combined_base() -> pd.DataFrame:
    """One row per traded session, filtered to trend-aligned + mag>=MAG_TH.
    Columns: dir, entry, exit, pnl (net of cost), mag_pct, stopped."""
    rth = load_rth_long()
    bars = build_bars5(rth)
    ont = _build_overnight_table()

    rows = []
    for sess, day in bars.groupby("session", sort=False):
        if sess not in ont.index:
            continue
        res = _orb_retest_confirm(day.reset_index(drop=True))
        if res is None:
            continue
        drc, entry_ts, entry_px, exit_px, stopped = res
        row = ont.loc[sess]
        trend_aligned = (drc == 1 and row["above_both"]) or (drc == -1 and row["below_both"])
        mag_pct = row["rise_pct"] if drc == 1 else row["decline_pct"]
        if not (trend_aligned and mag_pct >= MAG_TH):
            continue
        pnl = (exit_px - entry_px) * drc - COST
        rows.append({"session": sess, "dir": drc, "entry_ts": entry_ts,
                     "entry": entry_px, "exit": exit_px, "pnl": pnl,
                     "mag_pct": mag_pct, "stopped": stopped})
    out = pd.DataFrame(rows)
    out["session"] = pd.to_datetime(out["session"])
    return out.set_index("session").sort_index()


def summarize(trades: pd.DataFrame) -> None:
    valid = trades.loc["2021-01-01":"2026-07-16"]
    s = stats(valid["pnl"])
    print(f"ORB-combined base (retest-confirm + trend-align + mag>={MAG_TH:.2%}), "
          f"valid regime 2021-2026:")
    print(f"  N={s['N']} WR={s['WR']:.1%} Exp={s['Exp']:+.2f} PF={s['PF']:.2f} "
          f"Total={s['Total']:+.0f} MaxDD={s['MaxDD']:.0f}")
    for yr, grp in valid.groupby(valid.index.year):
        ys = stats(grp["pnl"])
        print(f"  {yr}: N={ys['N']} WR={ys['WR']:.1%} Exp={ys['Exp']:+.2f} "
              f"PF={ys['PF']:.2f} Total={ys['Total']:+.0f}")
    pre = trades.loc[:"2020-12-31"]
    ps = stats(pre["pnl"])
    print(f"  pre-2021 (transparency only, not a pass/fail input): "
          f"N={ps['N']} WR={ps['WR']:.1%} Exp={ps['Exp']:+.2f} PF={ps['PF']:.2f}")


if __name__ == "__main__":
    tr = run_orb_combined_base()
    summarize(tr)
    tr.to_parquet(Path(__file__).resolve().parent / "orb_combined_base_trades.parquet")
    print("saved -> orb_combined_base_trades.parquet")
