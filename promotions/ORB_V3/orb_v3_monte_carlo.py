"""ORB v3 — Gate 3.6 Monte Carlo equity-path study (MNQ, 1 contract fixed).

Closes the Gate 3.6 gap recorded in MNQ_COMBINED_REVIEW_PACKAGE_2026-08-02.md
("NOT RUN — no MC equity-path study exists for either leg"). Day-clustered
bootstrap CIs on expectancy are NOT an MC equity path; this is the real thing.

Sizing is FIXED (1 contract, MNQ $2/point), so per
feedback_mc_dollar_vs_pct_mode this runs in DOLLAR mode — resampling realised
per-trade dollar P&L. Percentage/NAV-compounding mode would be wrong here:
there is no equity-proportional position sizing to compound.

Two samplings are reported deliberately:
  - iid   : trades independent. Clean, but destroys regime clustering.
  - block : circular block bootstrap, block = floor(sqrt(N)). Preserves
            win/loss streaks and regime runs. This is the honest one for a
            strategy whose own docstring calls its edge regime-dependent.

Reported metrics are POINTS and DOLLARS only (net$, MaxDD$, net$/MaxDD$).
CAGR/Calmar are deliberately NOT reported: per GitLab #2199 G6/G7 they are
pure functions of a nominated equity base for a futures strategy, so they are
gameable by declaring a smaller account and are not a valid approval gate.

Usage:  python orb_v3_monte_carlo.py
Input:  orb_v3_trades.csv (bundled; rebuilt by orb_combined_base.py)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

POINT_VALUE = 2.0        # MNQ: $2 per index point
N_PATHS = 10_000
SEED = 20260803

# Declared capital base for the micro forward test (GitLab #2199 G7).
# DERIVED, not a round number: MNQ intraday initial margin ~$2,500/contract.
# $25,000 funds exactly 1 contract with a 10x margin buffer, which also covers
# the worst historical drawdown ($1,889) 13x over. Stated so every dollar
# figure below has a declared denominator instead of an invisible one.
DECLARED_CAPITAL_BASE = 25_000.0
INTRADAY_MARGIN_PER_CONTRACT = 2_500.0


def _load_trades() -> pd.DataFrame:
    df = pd.read_csv(HERE / "orb_v3_trades.csv", parse_dates=["session"])
    return df.set_index("session").sort_index()


def _path_stats(pnl: np.ndarray) -> tuple[float, float]:
    """Return (net, max_drawdown) in the input unit. MaxDD is peak-relative
    and returned as a negative number (0.0 if the path never draws down)."""
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    return float(eq[-1]), float(np.min(eq - peak))


def _simulate(pnl: np.ndarray, mode: str, rng: np.random.Generator,
              n_paths: int = N_PATHS) -> dict:
    n = len(pnl)
    nets = np.empty(n_paths)
    dds = np.empty(n_paths)

    if mode == "iid":
        idx = rng.integers(0, n, size=(n_paths, n))
    elif mode == "block":
        block = max(1, int(np.sqrt(n)))
        n_blocks = int(np.ceil(n / block))
        starts = rng.integers(0, n, size=(n_paths, n_blocks))
        offs = np.arange(block)
        # circular wrap so end-of-series blocks are not under-represented
        idx = (starts[:, :, None] + offs[None, None, :]) % n
        idx = idx.reshape(n_paths, -1)[:, :n]
    else:
        raise ValueError(mode)

    for i in range(n_paths):
        nets[i], dds[i] = _path_stats(pnl[idx[i]])

    ratio = np.divide(nets, np.abs(dds), out=np.full_like(nets, np.nan),
                      where=np.abs(dds) > 1e-9)
    return {
        "mode": mode,
        "block_size": (max(1, int(np.sqrt(n))) if mode == "block" else None),
        "n_paths": n_paths,
        "n_trades": n,
        "net_p5": float(np.percentile(nets, 5)),
        "net_p25": float(np.percentile(nets, 25)),
        "net_p50": float(np.percentile(nets, 50)),
        "net_p95": float(np.percentile(nets, 95)),
        "prob_net_negative": float((nets < 0).mean()),
        "maxdd_p50": float(np.percentile(dds, 50)),
        "maxdd_p95": float(np.percentile(dds, 5)),   # 5th pct = worst tail
        "maxdd_worst": float(np.min(dds)),
        "ratio_p5": float(np.nanpercentile(ratio, 5)),
        "ratio_p50": float(np.nanpercentile(ratio, 50)),
        "min_equity_p5": float(DECLARED_CAPITAL_BASE + np.percentile(dds, 5)),
        "prob_ruin_declared_base": float(
            ((DECLARED_CAPITAL_BASE + dds) <= INTRADAY_MARGIN_PER_CONTRACT).mean()),
    }


def main() -> None:
    tr = _load_trades()
    pnl_pts = tr["pnl"].to_numpy(float)
    pnl_usd = pnl_pts * POINT_VALUE
    rng = np.random.default_rng(SEED)

    print(f"ORB v3 Monte Carlo — Gate 3.6")
    print(f"  trades: N={len(pnl_pts)}  window {tr.index.min().date()} .. "
          f"{tr.index.max().date()}")
    print(f"  realised: net={pnl_pts.sum():+.1f} pts (${pnl_usd.sum():+,.2f})")
    print(f"  DOLLAR mode (fixed 1 contract, MNQ ${POINT_VALUE:.0f}/pt)")
    print(f"  declared capital base ${DECLARED_CAPITAL_BASE:,.0f} "
          f"(= 10x ${INTRADAY_MARGIN_PER_CONTRACT:,.0f} intraday margin)")

    results = {}
    for mode in ("iid", "block"):
        r = _simulate(pnl_usd, mode, np.random.default_rng(SEED))
        results[mode] = r
        print(f"\n=== {mode.upper()} "
              f"{'(block=' + str(r['block_size']) + ')' if r['block_size'] else ''} ===")
        print(f"  net$    P5={r['net_p5']:+,.0f}  P25={r['net_p25']:+,.0f}  "
              f"P50={r['net_p50']:+,.0f}  P95={r['net_p95']:+,.0f}")
        print(f"  P(net < 0)            = {r['prob_net_negative']:.4%}")
        print(f"  MaxDD$  P50={r['maxdd_p50']:+,.0f}  tail-P95={r['maxdd_p95']:+,.0f}  "
              f"worst={r['maxdd_worst']:+,.0f}")
        print(f"  net$/MaxDD$  P5={r['ratio_p5']:.3f}  P50={r['ratio_p50']:.3f}")
        print(f"  Min equity P5 (declared base) = ${r['min_equity_p5']:,.0f}")
        print(f"  P(equity < 1 contract margin) = {r['prob_ruin_declared_base']:.4%}")

    payload = {
        "generated_utc": pd.Timestamp.utcnow().isoformat(),
        "seed": SEED,
        "point_value": POINT_VALUE,
        "declared_capital_base": DECLARED_CAPITAL_BASE,
        "intraday_margin_per_contract": INTRADAY_MARGIN_PER_CONTRACT,
        "sizing_mode": "dollar_fixed_1_contract",
        "window_start": str(tr.index.min().date()),
        "window_end": str(tr.index.max().date()),
        "realised_net_points": float(pnl_pts.sum()),
        "realised_net_usd": float(pnl_usd.sum()),
        "results": results,
    }
    out = HERE / "orb_v3_monte_carlo_results.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nsaved -> {out.name}")


if __name__ == "__main__":
    main()
