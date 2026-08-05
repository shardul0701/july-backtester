# ORB v3 — Reproduction Map

Every number in [`PROMOTION_PACKAGE.md`](PROMOTION_PACKAGE.md) came from one of the commands below, run on 2026-08-03. Expected output is quoted so a mismatch is immediately visible rather than requiring interpretation.

---

## 1. Data dependency — pinned by hash

The bar data is **not** in this bundle (129 MB combined). It lives in the BO FVG research tree:

```
BO FVG/processed_data/nq_mnq_RTH_clean_stitched.parquet
BO FVG/processed_data/nq_mnq_ETH_clean_stitched.parquet
BO FVG/processed_data/daily_fresh/nq_recent_bars.parquet
```

SHA-256, so a reviewer can confirm identical inputs before comparing any number:

| SHA-256 | Bytes | File |
|---|---|---|
| `4711437c797ca0381967a18bd8623aa33f72fc9f1e5bbafd7e35ceebcbf4dd43` | 30,873,986 | `nq_mnq_RTH_clean_stitched.parquet` |
| `9eaabf38a66702943478dc90f11779b41a188e39adbd08bf600cbb041be68803` | 97,962,988 | `nq_mnq_ETH_clean_stitched.parquet` |
| `ffd97f26195ad194e46b473f24202fef2649d96f4d5e8cc0f97b271f4bdbe27e` | 427,434 | `daily_fresh/nq_recent_bars.parquet` |

**Two-layer design, and it matters for reproduction.** The frozen stitched files stop at **2026-07-16** and are never modified. Fresh bars accumulate in `daily_fresh/nq_recent_bars.parquet` and are appended **at read time** by `_read_stitched()` in `source/intraday_lib_long.py`. Consequence: **ORB v3 self-updates with the data.** Re-running it later than 2026-08-03 will legitimately return **more than 137 trades** — that is the free-OOS mechanism working, not a discrepancy. Compare like-for-like by slicing to `2026-07-30`.

Data provenance: `evidence/NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md` — 16-year NQ+MNQ stitch, verdict PASS. 8 sessions are fully/partially missing at the raw Databento source level (confirmed against Databento's own degraded-session flags) and are **excluded** rather than trusted.

---

## 2. Headline numbers

```bash
cd "BO FVG/research"
python orb_combined_base.py
```

Expected (frozen window — the script's `summarize()` slices to 2026-07-16):

```
ORB-combined base (retest-confirm + trend-align + mag>=0.15%), valid regime 2021-2026:
  N=134 WR=63.4% Exp=+50.98 PF=2.34 Total=+6831 MaxDD=-944
  2021: N=12 WR=50.0% Exp=+17.48 PF=1.41 Total=+210
  2022: N=37 WR=78.4% Exp=+64.15 PF=3.06 Total=+2373
  2023: N=32 WR=59.4% Exp=+23.94 PF=1.78 Total=+766
  2024: N=21 WR=61.9% Exp=+35.47 PF=2.15 Total=+745
  2025: N=19 WR=42.1% Exp=+51.56 PF=1.62 Total=+980
  2026: N=13 WR=76.9% Exp=+135.19 PF=9.21 Total=+1758
  pre-2021 (transparency only, not a pass/fail input): N=63 WR=44.4% Exp=-7.60 PF=0.74
```

Writes `orb_combined_base_trades.parquet`. The bundled [`orb_v3_trades.csv`](orb_v3_trades.csv) is that parquet, sliced to `2021-01-01:` and exported — **137 rows**, because the CSV is not truncated at 2026-07-16 the way `summarize()` is.

⚠️ **Known hazard (promotion package §7.1):** `summarize()` hardcodes `2026-07-16`. Other scripts in the research tree do not, so two scripts can disagree on the 2026 slice purely from that cutoff. This needs one shared dated constant. Not fixed in this bundle because changing it mid-review would move frozen numbers.

---

## 3. Full window incl. free OOS + the 3 fresh sessions

```bash
cd promotions/ORB_V3
python - <<'PY'
import pandas as pd
tr = pd.read_csv("orb_v3_trades.csv", parse_dates=["session"]).set_index("session")
PV, FROZEN = 2.0, "2026-07-16"
for lbl, df in [("FROZEN", tr.loc[:FROZEN]), ("FULL", tr)]:
    p = df["pnl"]; eq = p.cumsum(); dd = (eq - eq.cummax()).min()
    w, l = p[p > 0], p[p <= 0]
    print(f"{lbl}: N={len(p)} WR={len(w)/len(p):.1%} Exp={p.mean():+.2f} "
          f"PF={w.sum()/abs(l.sum()):.3f} Net={p.sum():+.1f}pts (${p.sum()*PV:+,.2f}) "
          f"MaxDD={dd:.1f}pts (${dd*PV:+,.2f}) net$/MaxDD$={p.sum()/abs(dd):.3f}")
print(tr.loc[pd.Timestamp(FROZEN) + pd.Timedelta(days=1):].to_string())
PY
```

Expected:

```
FROZEN: N=134 WR=63.4% Exp=+50.98 PF=2.340 Net=+6831.3pts ($+13,662.63) MaxDD=-944.3pts ($-1,888.59) net$/MaxDD$=7.234
FULL:   N=137 WR=63.5% Exp=+49.56 PF=2.264 Net=+6790.2pts ($+13,580.48) MaxDD=-944.3pts ($-1,888.59) net$/MaxDD$=7.191
```

Fresh sessions — 3 signals, **−41.07 pts / −$82.15**:

| session | dir | entry | exit | pnl | stopped |
|---|---|---|---|---|---|
| 2026-07-23 | −1 | 28123.25 | 28107.75 | +14.25 | False |
| 2026-07-28 | −1 | 27207.25 | 27479.32 | −273.32 | **True** |
| 2026-07-30 | +1 | 27503.25 | 27722.50 | +218.00 | False |

The 07-28 stop-out is the trade referenced in the v3 docstring: at 1.0% it lost **−273.32** where the old 1.5% stop would have lost **−206.50**. An intrabar spike crossed the tighter threshold and then reverted into the close. Kept visible on purpose — the v3 stop change is a statistical improvement across the sample, not a per-trade guarantee, and this is the counterexample.

---

## 4. Gate 2 — execution honesty

```bash
cd "BO FVG/research"
python orb_v3_surgical_execution_test.py
```

Expected tail:

```
-- SURGICAL --
  2021-2026: N=134 WR=63.4% Exp=+50.96 PF=2.34 Total=+6829 MaxDD=-945
  dev 21-23: N=81 PF=2.26 Exp=+41.32 | test 24-26: N=53 PF=2.42 Exp=+65.69
  counters: {'n_gap_fill': 0, 'gap_slip_pts': 0.0, 'worst_gap_pts': 0.0,
             'n_stop': 31, 'n_stop_orig_would_miss': 0}
  trades with changed P&L: 19 / 134
  total P&L delta (surgical - original): -2.56 pts over 134 trades (+6831 -> +6829)
```

**−2.56 pts over 134 trades = 0.04%.** 31 stops re-resolved against actual 1-min bars with tick-grid rounding and gap-aware worse-of fills. `n_stop_orig_would_miss = 0` means no stop the base model claimed was reachable turned out not to be.

**Why byte-identical rows here would be a red flag, and why these aren't.** A no-op surgical test is exactly the signature that hid the Sleeve A execution artifact, so a zero delta is suspicious rather than reassuring. Here 19 of 134 trades *did* change, all in the adverse direction, all by sub-tick amounts from tick-grid rounding. That is what an honest fill model looks like.

Note `dev 21-23 PF 2.26 | test 24-26 PF 2.42` — the **test figure is in-sample** (promotion package §0.1). Do not read it as out-of-sample validation.

---

## 5. Gate 3.6 — Monte Carlo

```bash
cd promotions/ORB_V3
python orb_v3_monte_carlo.py
```

Deterministic — `seed = 20260803`, 10,000 paths, DOLLAR mode. Expected:

```
=== IID ===
  net$    P5=+6,355  P25=+10,280  P50=+13,298  P95=+21,863
  P(net < 0)            = 0.0800%
  MaxDD$  P50=-1,463  tail-P95=-2,606  worst=-5,149
  Min equity P5 (declared base) = $22,394
  P(equity < 1 contract margin) = 0.0000%

=== BLOCK (block=11) ===
  net$    P5=+6,774  P25=+10,811  P50=+13,581  P95=+20,493
  P(net < 0)            = 0.0300%
  MaxDD$  P50=-1,564  tail-P95=-2,769  worst=-5,557
  Min equity P5 (declared base) = $22,231
  P(equity < 1 contract margin) = 0.0000%
```

Writes `orb_v3_monte_carlo_results.json`.

**Cross-check worth noting:** P(net<0) of 0.03–0.08% here independently reproduces the pre-registration's **P(total<0) = 0.04%**, computed in a different codebase by a different method. Rebuilding a headline from source through a second pipeline is a cheap, high-yield check — the "ORB fired 0 times" error in the earlier audit only surfaced because a tearsheet rebuild disagreed.

---

## 6. File index

| Path | What |
|---|---|
| `PROMOTION_PACKAGE.md` | Main review document — spec, gate ladder, numbers, open findings |
| `FORWARD_TEST_PREREG.md` | Kill criteria, pre-registered before any fill |
| `SIGNALDECK_INTEGRATION_HANDOFF.md` | MR blueprint; blocked on #2199 F0 |
| `REPRODUCTION.md` | This file |
| `orb_v3_trades.csv` | 137 trades, 2021-01-26 → 2026-07-30 |
| `orb_v3_monte_carlo.py` | Gate 3.6 study |
| `orb_v3_monte_carlo_results.json` | Its output |
| `source/orb_combined_base.py` | **Canonical frozen strategy** (locked 2026-07-29) |
| `source/orb_v3_surgical_execution_test.py` | Gate 2 honest-fill test |
| `source/intraday_lib_long.py` | Data loader — frozen + overlay two-layer read |
| `source/ema_crossover_test.py` | `build_bars5()`, `stats()` helpers |
| `evidence/ORB_GNG_PROMOTION_AUDIT_2026-08-02.md` | Independent adversarial audit (Gate 5.4) |
| `evidence/MNQ_COMBINED_REVIEW_PACKAGE_2026-08-02.md` | Prior combined ORB+GNG package — provenance |
| `evidence/GNG_THRESHOLD_LOOSENING_PREREG_2026-08-03.md` | Why GNG is frozen and excluded |
| `evidence/STRATEGY_REALITY_CHECKLIST.md` | The gate definitions being scored against |
| `evidence/CLAUDE_HANDOFF_NQ_ERA_ROBUSTNESS_2026-07-18.md` | The extended-history collapse that motivated v3 |
| `evidence/NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md` | Data provenance, verdict PASS |

`source/` files are **copies** frozen at bundle time. The live originals are in `BO FVG/research/`; if they diverge, the originals are authoritative and the drift should be flagged.
