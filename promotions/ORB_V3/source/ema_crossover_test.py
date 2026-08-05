"""EMA 9/20 crossover, 5m bars, RTH-session-bounded, trail to reverse-cross/EOD.

User hypothesis (2026-07-18): "whenever it's a non-choppy day, buy or sell
when the 9/20 EMA crossover happens and keep trailing either to EOD or until
the crossover happens again." This is a CONTINUATION-shaped signal, the one
family that has repeatedly worked on this dataset (see
research/INTRADAY_RESEARCH_REPORT.md TL;DR) -- as opposed to the RSI-bounce /
divergence ideas from the same conversation, which are fade-shaped and have
failed 7 independent times.

This first pass tests the crossover UNFILTERED (no chop/trend-day gate yet)
to get a clean baseline before adding any filter -- same discipline as
orb_base.py being frozen before orb_or_width_filter.py added a regime gate
on top of it.

Mechanics:
- 5-minute bars built from the audited 2010-2026 long-history RTH file
  (intraday_lib_long.py), session-bounded (EMAs reset each session, bars
  aggregated causally: open=first, high=max, low=min, close=last).
- Signal on bar i's completed close; fill at bar i+1's open (no lookahead).
- A crossover that closes an existing position also opens the opposite one
  on the same signal bar (flip), matching "trail until crossover happens
  again."
- Flatten at the session's last bar close if still in a position at EOD.
- Cost: 1.25 pts round-trip (same model used everywhere else in this repo).

Split: train 2010-2018 / val 2019-2022 / test 2023-2026, fixed in
intraday_lib_long.py BEFORE this script was run. Only train+val are used to
decide anything; test is reported once, unmodified, at the end.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intraday_lib_long import load_rth_long, split_col  # noqa: E402

COST = 1.25
FAST, SLOW = 9, 20


def build_bars5(rth: pd.DataFrame) -> pd.DataFrame:
    rth = rth.copy()
    rth["ts"] = rth.index
    bin5 = rth["mos"] // 5
    g = rth.groupby([rth["session"], bin5])
    bars = g.agg(
        ts=("ts", "first"),
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"),
    )
    bars.index.set_names(["session", "bin5"], inplace=True)
    bars = bars.reset_index().sort_values(["session", "bin5"]).reset_index(drop=True)
    return bars


def add_emas(bars: pd.DataFrame) -> pd.DataFrame:
    bars["ema_fast"] = bars.groupby("session")["close"].transform(
        lambda s: s.ewm(span=FAST, min_periods=FAST).mean())
    bars["ema_slow"] = bars.groupby("session")["close"].transform(
        lambda s: s.ewm(span=SLOW, min_periods=SLOW).mean())
    return bars


def simulate(bars: pd.DataFrame) -> pd.DataFrame:
    """Per-session state machine: cross -> enter next bar's open, flip on
    opposite cross, flatten at last bar's close if still open at EOD."""
    trades = []
    for sess, day in bars.groupby("session", sort=False):
        day = day.reset_index(drop=True)
        n = len(day)
        pos = 0
        entry_price = entry_ts = None
        prev_diff = None
        for i in range(n):
            f, s = day.at[i, "ema_fast"], day.at[i, "ema_slow"]
            if pd.isna(f) or pd.isna(s):
                prev_diff = None
                continue
            diff = f - s
            if prev_diff is not None and i + 1 < n:
                cross_up = prev_diff <= 0 < diff
                cross_dn = prev_diff >= 0 > diff
                if pos == 1 and cross_dn:
                    px = day.at[i + 1, "open"]
                    trades.append({"session": sess, "dir": 1,
                                    "entry_ts": entry_ts, "exit_ts": day.at[i + 1, "ts"],
                                    "entry": entry_price, "exit": px,
                                    "pnl": (px - entry_price) - COST, "reason": "Reverse Cross"})
                    pos = 0
                elif pos == -1 and cross_up:
                    px = day.at[i + 1, "open"]
                    trades.append({"session": sess, "dir": -1,
                                    "entry_ts": entry_ts, "exit_ts": day.at[i + 1, "ts"],
                                    "entry": entry_price, "exit": px,
                                    "pnl": (entry_price - px) - COST, "reason": "Reverse Cross"})
                    pos = 0
                if pos == 0 and cross_up:
                    entry_price, entry_ts, pos = day.at[i + 1, "open"], day.at[i + 1, "ts"], 1
                elif pos == 0 and cross_dn:
                    entry_price, entry_ts, pos = day.at[i + 1, "open"], day.at[i + 1, "ts"], -1
            prev_diff = diff
        if pos != 0:
            px = day.at[n - 1, "close"]
            pnl = (px - entry_price) - COST if pos == 1 else (entry_price - px) - COST
            trades.append({"session": sess, "dir": pos, "entry_ts": entry_ts,
                            "exit_ts": day.at[n - 1, "ts"], "entry": entry_price,
                            "exit": px, "pnl": pnl, "reason": "EOD Flatten"})
    return pd.DataFrame(trades)


def stats(pnl: pd.Series) -> dict:
    if len(pnl) == 0:
        return {"N": 0, "WR": np.nan, "Exp": np.nan, "PF": np.nan, "Total": 0.0, "MaxDD": 0.0}
    w, ls = pnl[pnl > 0], pnl[pnl <= 0]
    pf = w.sum() / abs(ls.sum()) if len(ls) and ls.sum() != 0 else np.inf
    eq = pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    return {"N": len(pnl), "WR": len(w) / len(pnl), "Exp": pnl.mean(),
            "PF": pf, "Total": eq.iloc[-1], "MaxDD": dd}


def print_stats(label: str, pnl: pd.Series) -> None:
    s = stats(pnl)
    if s["N"] == 0:
        print(f"  {label}: N=0")
        return
    print(f"  {label}: N={s['N']} WR={s['WR']:.1%} Exp={s['Exp']:+.2f} "
          f"PF={s['PF']:.2f} Total={s['Total']:+.0f} MaxDD={s['MaxDD']:.0f}")


def main():
    rth = load_rth_long()
    print(f"Loaded {len(rth):,} 1m RTH bars, {rth.index.min()} -> {rth.index.max()}")

    bars = build_bars5(rth)
    print(f"Built {len(bars):,} 5m bars across {bars['session'].nunique():,} sessions")

    bars = add_emas(bars)
    trades = simulate(bars)
    trades["session"] = pd.to_datetime(trades["session"])
    print(f"\n{len(trades):,} total trades (unfiltered, both directions)\n")

    lbl = split_col(pd.Series(trades["session"].unique()))
    trades["split"] = trades["session"].map(lbl)

    print("=== By split ===")
    for split in ["train", "val", "test"]:
        print_stats(split, trades.loc[trades["split"] == split, "pnl"])

    print("\n=== Train, by year ===")
    tr = trades[trades["split"] == "train"].copy()
    tr["year"] = tr["session"].dt.year
    for yr, grp in tr.groupby("year"):
        print_stats(str(yr), grp["pnl"])

    print("\n=== Val, by year ===")
    va = trades[trades["split"] == "val"].copy()
    va["year"] = va["session"].dt.year
    for yr, grp in va.groupby("year"):
        print_stats(str(yr), grp["pnl"])

    print("\n=== Train+Val combined, long vs short ===")
    dev = trades[trades["split"].isin(["train", "val"])]
    print_stats("Long", dev.loc[dev["dir"] == 1, "pnl"])
    print_stats("Short", dev.loc[dev["dir"] == -1, "pnl"])

    print("\n=== Train+Val pnl.describe() ===")
    print(dev["pnl"].describe())

    print("\n=== Train+Val, exit reason breakdown ===")
    print(dev.groupby("reason")["pnl"].agg(["count", "mean", "sum"]))

    out = Path(__file__).resolve().parent / "ema_crossover_trades.parquet"
    trades.to_parquet(out)
    print(f"\nsaved -> {out.name}")


if __name__ == "__main__":
    main()
