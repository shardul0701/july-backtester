"""Shared infra for the 2010-2026 NQ+MNQ long-history stitched dataset.

Mirrors intraday_lib.load_rth()/load_full() exactly (same session/mos/vwap
conventions, same causal discipline) but reads the audited long-history
files instead of the 5.5yr MNQ-only ones, so every downstream script can
still call session_table() from intraday_lib.py unchanged.

Audit reference: processed_data/NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md
(PASS, trustworthy for research use). One finding applied here: 8 sessions
are fully/partially missing at the raw-source level (not a pipeline bug,
confirmed via Databento's own degraded-session flags) and are excluded
below rather than trusted as real data.

Daily freshness (added 2026-07-18): the frozen files above are a one-time
build that stops at 2026-07-16 and does not self-update. Fresh bars are kept
in processed_data/daily_fresh/nq_recent_bars.parquet (see
processed_data/update_nq_daily.py) and stitched onto the frozen base at read
time below -- the frozen parquet files themselves are never modified. Same
two-layer pattern as the equities merge (see memory: project_data_freshness_solution).
"""

from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "processed_data"
OVERLAY = DATA / "daily_fresh" / "nq_recent_bars.parquet"

FILES_LONG = {
    "RTH": "nq_mnq_RTH_clean_stitched.parquet",
    "ETH": "nq_mnq_ETH_clean_stitched.parquet",
}


def _read_stitched(filename: str) -> pd.DataFrame:
    """Read a frozen long-history file and append any overlay rows dated
    after the frozen file's own last timestamp. Frozen file is untouched."""
    frozen = pd.read_parquet(DATA / filename).sort_index()
    if not OVERLAY.exists():
        return frozen
    overlay = pd.read_parquet(OVERLAY).sort_index()
    fresh = overlay[overlay.index > frozen.index.max()]
    if fresh.empty:
        return frozen
    return pd.concat([frozen, fresh]).sort_index()

RTH_START = 9 * 60 + 30   # 09:30 ET
RTH_END = 16 * 60         # 16:00 ET (exclusive)

# processed_data/NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md finding F1 —
# fully/partially missing at the raw Databento source, root-caused via
# their own "degraded session" flags. Exclude rather than trust as real.
GAP_SESSIONS = {
    "2014-06-12", "2014-06-13", "2014-09-23", "2014-09-24", "2014-09-25",
    "2014-12-31", "2020-02-28", "2020-06-30",
}


def load_rth_long() -> pd.DataFrame:
    """1m RTH bars, 2010-06-07 -> 2026-07-16, session id, mos, cumulative VWAP.

    Same shape/columns as intraday_lib.load_rth() so session_table() and any
    other existing helper work unchanged on this frame.
    """
    df = _read_stitched(FILES_LONG["RTH"])
    mod = df.index.hour * 60 + df.index.minute
    rth = df[(mod >= RTH_START) & (mod < RTH_END)].copy()
    rth["session"] = pd.to_datetime(pd.Series(rth.index.date, index=rth.index))
    rth["mos"] = (rth.index.hour * 60 + rth.index.minute) - RTH_START

    gap = rth["session"].dt.strftime("%Y-%m-%d").isin(GAP_SESSIONS)
    rth = rth[~gap]

    tp = (rth["high"] + rth["low"] + rth["close"]) / 3.0
    pv = tp * rth["volume"]
    rth["vwap"] = pv.groupby(rth["session"].values).cumsum() / \
        rth["volume"].groupby(rth["session"].values).cumsum()
    return rth


def load_full_long() -> pd.DataFrame:
    """Full 24h Globex bars from the long ETH stitch, CME trade-date tagged."""
    df = _read_stitched(FILES_LONG["ETH"])
    d = pd.to_datetime(pd.Series(df.index.date, index=df.index))
    shift_days = (df.index.hour >= 18).astype(int)
    df["session"] = d + pd.to_timedelta(shift_days, unit="D")
    df["mod"] = df.index.hour * 60 + df.index.minute
    gap = df["session"].dt.strftime("%Y-%m-%d").isin(GAP_SESSIONS)
    return df[~gap]


# Fixed, chronological, no-repeat split. Decided BEFORE any hypothesis was
# run on this data -- do not move these dates after seeing results (see
# research/CLAUDE_AUDIT_HANDOFF.md F1/F2: the ORB context-model holdout was
# compromised exactly this way, by peeking then re-drawing conclusions).
SPLITS = {
    "train": ("2010-06-07", "2018-12-31"),
    "val":   ("2019-01-01", "2022-12-31"),
    "test":  ("2023-01-01", "2026-07-16"),
}


def split_col(sessions) -> pd.Series:
    """Return a Series of 'train'/'val'/'test' labels indexed by session date."""
    sess = pd.DatetimeIndex(pd.Series(sessions).to_numpy())
    out = pd.Series("unassigned", index=sess)
    for name, (start, end) in SPLITS.items():
        mask = (sess >= pd.Timestamp(start)) & (sess <= pd.Timestamp(end))
        out.iloc[mask] = name
    return out


if __name__ == "__main__":
    rth = load_rth_long()
    print(f"RTH long: {len(rth):,} rows, {rth.index.min()} -> {rth.index.max()}")
    sessions = rth["session"].drop_duplicates().sort_values()
    print(f"{len(sessions):,} sessions after excluding {len(GAP_SESSIONS)} gap dates")
    lbl = split_col(sessions)
    print(lbl.value_counts())
