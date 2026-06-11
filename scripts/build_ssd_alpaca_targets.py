"""Build an Alpaca-ready CURRENT target book for SSD_B2_DG20.

Fresh-CSV builder (reads data/polygon_daily, data/etf_prices via the S8 builder's
``load_symbol``) so it produces a CURRENT as-of book — unlike the Norgate-capped
``rebuild_blended_pit`` output (frozen at 2026-04-22).

SSD_B2_DG20 = vol-gated blend of B5 and B2 (see rebuild_blended_pit.simulate_ssd_dg20):

    B5 = 60% QQQ (QQQ>200MA bull) / 60% GLD (bear) + SP500 MR (RSI<25, 10d-rank)
    B2 = 40% C7 SP500 momentum (top-7 126d) + NQ100 MR + remaining-cash TLT/UUP

    DG20 gate (uses PRIOR-day B5 20d realised vol, no lookahead):
        vol > 20%  -> hold 100% B2
        vol <= 20% -> scale = min(1, 10/vol);  SSD = scale*B5 + (1-scale)*B2

Constants verified against rebuild_blended_pit.py. Mean-reversion is RSI-based
(consistent with the already-deployed S8 builder; both share the same MR engine).
This script computes target weights only — it does NOT submit orders.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse the proven primitives from the S8 builder (same data loaders + sims).
from build_s8_alpaca_targets import (
    ROOT,
    load_panel,
    load_symbol,
    read_json_list,
    close_matrix,
    simulate_mr_weight_book,
    simulate_momentum_weight_book,
    load_current_positions,
    build_order_preview,
    MOM_TOP_N,
)

OUT_DIR = ROOT / "output" / "live_ssd_alpaca"

# ── SSD / B5 constants (from rebuild_blended_pit.py) ──────────────────────────
SSD_VOL_GATE_DG20 = 20.0
SSD_VOL_LOOKBACK = 20
SSD_VOL_TARGET = 10.0
B5_QQQ_ALLOC = 0.60
B5_QQQ_MA = 200
C7_ALLOC = 0.40
DEFENSIVE_SYMS = ["TLT", "UUP"]


def _held_by_date(book: pd.DataFrame) -> dict[str, dict[str, float]]:
    """{date_str: {sym: held_weight}} from an MR weight book (existing positions only)."""
    if book.empty:
        return {}
    held = book[book["note"] == "existing_mr_position"]
    return {d: dict(zip(g["symbol"], g["target_weight"])) for d, g in held.groupby("date")}


def _weights_on(book: pd.DataFrame, date: str, component: str) -> dict[str, float]:
    """All target weights for `component` on `date` (existing + new entries), netted."""
    if book.empty:
        return {}
    sub = book[(book["date"] == date) & (book["component"] == component)]
    if sub.empty:
        return {}
    return sub.groupby("symbol")["target_weight"].sum().to_dict()


def b5_equity_curve(
    mr_sp_book: pd.DataFrame,
    sp_closes: pd.DataFrame,
    qqq: pd.DataFrame,
    gld: pd.DataFrame | None,
    start: str,
) -> pd.Series:
    """Approximate B5 daily equity from its sleeve + SP500-MR held weights.

    B5_ret[t] = 0.60 * sleeve_ret[t] + sum_i held_w[t-1][i] * ret_i[t]   (cash = 0)
    sleeve = QQQ when prior-day QQQ>200MA else GLD.
    """
    held = _held_by_date(mr_sp_book)
    qclose = qqq["Close"]
    gclose = gld["Close"] if gld is not None else None
    qma = qclose.rolling(B5_QQQ_MA).mean()

    dates = [d for d in sp_closes.index if d >= pd.Timestamp(start)]
    eq = 1.0
    out: dict[pd.Timestamp, float] = {}
    for i, d in enumerate(dates):
        if i == 0:
            out[d] = eq
            continue
        p = dates[i - 1]
        pk = str(p.date())

        bull = (p in qclose.index and p in qma.index
                and pd.notna(qma.get(p)) and qclose.get(p, np.nan) > qma.get(p))
        sleeve = qclose if (bull or gclose is None) else gclose
        if d in sleeve.index and p in sleeve.index and sleeve.get(p, 0) > 0:
            sl_ret = sleeve[d] / sleeve[p] - 1
        else:
            sl_ret = 0.0
        r = B5_QQQ_ALLOC * sl_ret

        for s, w in held.get(pk, {}).items():
            if s in sp_closes.columns and d in sp_closes.index and p in sp_closes.index:
                cp = sp_closes.at[p, s]
                cd = sp_closes.at[d, s]
                if pd.notna(cp) and pd.notna(cd) and cp > 0:
                    r += w * (cd / cp - 1)

        eq *= (1 + r)
        out[d] = eq
    return pd.Series(out)


def dg20_scale(b5_eq: pd.Series, as_of: pd.Timestamp) -> tuple[float, float, bool]:
    """Return (scale_b5, b5_vol_prior, in_b2_mode) using the PRIOR trading day's vol."""
    dr = b5_eq.pct_change()
    vol = dr.rolling(SSD_VOL_LOOKBACK).std() * np.sqrt(252) * 100
    prior = vol.index[vol.index < as_of]
    if len(prior) == 0:
        return 1.0, float("nan"), False
    v = vol.loc[prior[-1]]
    if pd.isna(v):
        return 1.0, float("nan"), False
    if v > SSD_VOL_GATE_DG20:
        return 0.0, float(v), True            # 100% B2
    scale = min(1.0, SSD_VOL_TARGET / v) if v > 0 else 1.0
    return float(scale), float(v), False


def momentum_c7_weights(mom_book: pd.DataFrame, as_of: str) -> dict[str, float]:
    """C7 sleeve on as_of: top-momentum holdings * 0.40. Empty if in GLD/bear mode."""
    sub = mom_book[(mom_book["date"] == as_of) & (mom_book["component"] == "Momentum")]
    holds = sub.groupby("symbol")["target_weight"].sum().to_dict()
    holds = {s: w for s, w in holds.items() if s != "GLD"}   # bear C7 = cash, not GLD
    return {s: w * C7_ALLOC for s, w in holds.items()}


def build_ssd_book(
    mr_sp_book: pd.DataFrame,
    mr_nq_book: pd.DataFrame,
    mom_book: pd.DataFrame,
    b5_eq: pd.Series,
    qqq: pd.DataFrame,
    gld: pd.DataFrame | None,
    as_of: str | None,
) -> tuple[pd.DataFrame, dict]:
    avail = sorted(set(mr_sp_book["date"]) | set(mr_nq_book["date"]) | set(mom_book["date"]))
    if not avail:
        raise ValueError("No component target dates generated.")
    if as_of is None:
        as_of = avail[-1]
    elif as_of not in avail:
        prior = [d for d in avail if d <= as_of]
        if not prior:
            raise ValueError(f"No target date on or before {as_of}")
        as_of = prior[-1]
    ts = pd.Timestamp(as_of)

    scale_b5, b5_vol, in_b2 = dg20_scale(b5_eq, ts)

    # ── B5 as-of weights: 60% QQQ/GLD sleeve + SP500 MR ──────────────────────
    qclose, qma = qqq["Close"], qqq["Close"].rolling(B5_QQQ_MA).mean()
    pri = qclose.index[qclose.index <= ts]
    bull = len(pri) > 0 and pd.notna(qma.get(pri[-1])) and qclose.get(pri[-1]) > qma.get(pri[-1])
    sleeve_sym = "QQQ" if (bull or gld is None) else "GLD"
    b5 = {sleeve_sym: B5_QQQ_ALLOC}
    for s, w in _weights_on(mr_sp_book, as_of, "MR").items():
        b5[s] = b5.get(s, 0.0) + w

    # ── B2 as-of weights: C7*0.40 + NQ100 MR + remaining-cash TLT/UUP ────────
    b2: dict[str, float] = {}
    for s, w in momentum_c7_weights(mom_book, as_of).items():
        b2[s] = b2.get(s, 0.0) + w
    for s, w in _weights_on(mr_nq_book, as_of, "MR").items():
        b2[s] = b2.get(s, 0.0) + w
    invested = sum(b2.values())
    remaining = max(0.0, 1.0 - invested)
    if remaining > 1e-6:
        for s in DEFENSIVE_SYMS:
            b2[s] = b2.get(s, 0.0) + remaining / len(DEFENSIVE_SYMS)

    # ── DG20 blend: scale*B5 + (1-scale)*B2 (or 100% B2 when in_b2) ──────────
    net: dict[str, float] = {}
    for s, w in b5.items():
        net[s] = net.get(s, 0.0) + scale_b5 * w
    for s, w in b2.items():
        net[s] = net.get(s, 0.0) + (1.0 - scale_b5) * w

    rows = [{"symbol": s, "target_weight": round(w, 6)}
            for s, w in sorted(net.items(), key=lambda kv: -kv[1]) if abs(w) > 1e-6]
    book = pd.DataFrame(rows)
    book.insert(0, "date", as_of)
    book.insert(1, "strategy_id", "SSD_B2_DG20_ORDER_LEVEL_V1")

    meta = {
        "as_of": as_of,
        "mode": "B2_only_vol>20" if in_b2 else "B5_scaled",
        "b5_vol_prior_pct": None if pd.isna(b5_vol) else round(b5_vol, 2),
        "scale_b5": round(scale_b5, 4),
        "b2_weight": round(1.0 - scale_b5, 4),
        "regime": "bull" if bull else "bear",
        "sleeve": sleeve_sym,
        "gross_target_weight": round(float(book["target_weight"].sum()), 4),
        "position_count": int(len(book)),
    }
    return book, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mr-universe", default="tickers_to_scan/nasdaq_100.json")
    ap.add_argument("--sp-universe", default="tickers_to_scan/sp-500.json")
    ap.add_argument("--start-date", default="2016-09-01")
    ap.add_argument("--as-of", help="YYYY-MM-DD. Defaults to latest common target date.")
    ap.add_argument("--account-equity", type=float, default=100_000.0)
    ap.add_argument("--current-positions")
    ap.add_argument("--output-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    nq_syms = read_json_list(ROOT / args.mr_universe)
    sp_syms = read_json_list(ROOT / args.sp_universe)
    nq_data, miss_nq = load_panel(nq_syms)
    sp_data, miss_sp = load_panel(sp_syms)
    qqq = load_symbol("QQQ")
    if qqq is None:
        raise SystemExit("QQQ data not found.")
    gld = load_symbol("GLD")
    vix = load_symbol("VIX")

    # Component books (reuse S8's tested simulators)
    mr_sp_book, _ = simulate_mr_weight_book(sp_data, vix, qqq, args.start_date)   # B5 SP500 MR
    mr_nq_book, _ = simulate_mr_weight_book(nq_data, vix, qqq, args.start_date)   # B2 NQ100 MR
    mom_book, _ = simulate_momentum_weight_book(sp_data, qqq, gld, args.start_date)  # B2 C7

    sp_closes = close_matrix(sp_data)
    b5_eq = b5_equity_curve(mr_sp_book, sp_closes, qqq, gld, args.start_date)

    book, meta = build_ssd_book(mr_sp_book, mr_nq_book, mom_book, b5_eq, qqq, gld, args.as_of)

    current = load_current_positions(
        Path(args.current_positions) if args.current_positions else None, args.account_equity)
    preview = build_order_preview(book, current, args.account_equity)

    book.to_csv(out / "SSD_latest_target_weights.csv", index=False)
    preview.to_csv(out / "SSD_order_preview.csv", index=False)
    meta.update({"missing_nq": len(miss_nq), "missing_sp": len(miss_sp),
                 "account_equity": args.account_equity})
    (out / "SSD_run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Wrote {out / 'SSD_latest_target_weights.csv'}")
    print(f"Wrote {out / 'SSD_order_preview.csv'}")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
