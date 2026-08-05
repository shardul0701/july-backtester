# Pre-registration — GNG entry-threshold loosening

**Date filed:** 2026-08-03 (before any result was computed)
**Author:** Claude, at user request
**Status at filing:** hypothesis only
**Status now:** EXECUTED 2026-08-03 — **hypothesis not supported, GNG stays frozen.**
See "Outcome" at the bottom, including a recorded defect in this document's own
selection metric.

## Motivation

Gap-and-Go's problem is not a broken mechanism, it is sample size. At 23 trades/year the
5.5-year total P&L 95% CI is [-$1,986, +$13,442] and P(total < 0) = 7.3% — the edge is not
statistically distinguishable from zero. A losing year in 2025 is fully predicted by the
trade distribution (bootstrap P(any year < 0) = 26.3%), so it is not evidence of breakage.

Every *filter* tested to date makes N smaller and has failed:
- short side — killed (val PF 1.06)
- uptrend gating — killed and inverted
- gap cap — non-monotonic across the sweep, already rejected as overfit
- first-bar-extension floor — noise (+$37 / -$118 / +$165 / -$1,978)
- cross-instrument (MES / M2K / MYM / GC) — no transfer, edge is NQ-specific

Loosening the *entry* thresholds is the only untested direction that attacks N directly.

## Hypothesis

Relaxing `GAP_PCT_THRESH` (currently 0.0030) and/or `MIN_EXT_PCT` (currently 0.0010) raises
trade count faster than it dilutes per-trade edge, moving GNG from "positive but not
statistically established" to "statistically established".

## Data and splits (canonical, defined in `intraday_lib_long.SPLITS`, unchanged)

| split | window | role |
| --- | --- | --- |
| train | 2010-06-07 .. 2018-12-31 | screening — the only split searched |
| val   | 2019-01-01 .. 2022-12-31 | confirmation |
| test  | 2023-01-01 .. 2026-07-16 | **held back**; touched at most once, only by a cell that has already passed train AND val |

All other mechanics frozen at production values: 5-minute bars, `side="long"`,
`STOP_PCT = 0.015`, `COST` as-is, MNQ point value $2.

## Grid

- `GAP_PCT_THRESH` ∈ {0.0010, 0.0015, 0.0020, 0.0025, **0.0030**, 0.0040}
- `MIN_EXT_PCT`    ∈ {0.0000, 0.0002, 0.0005, 0.0008, **0.0010**, 0.0015}

36 cells; bold = current production values (the base cell).

Both parameters are pure post-signal filters on `gap_pct` and `first_bar_ext`, and neither
affects direction selection. So the grid is evaluated by simulating once at the loosest cell
and subsetting — exactly equivalent to 36 separate simulations, and verified by
reproducing the base cell's known trade count.

## Gates — a cell is a candidate only if it passes ALL of G1-G5

- **G1 sample:** `N_train >= 1.25 x N_train(base)` — must materially raise N, not nibble.
- **G2 edge survives dilution:** `expectancy_train > 0` AND `PF_train >= 1.15`.
- **G3 out-of-sample:** `expectancy_val > 0` AND `PF_val >= 1.15`.
- **G4 statistical:** bootstrap (iid trade resample, 40k draws) `P(total P&L < 0) < 5%` on
  train+val pooled. This is the actual objective — base is 7.3% on 2021+.
- **G5 not a spike:** cell's train PF within 20% of the mean train PF of its 4-neighbour
  cells in the grid. An isolated peak is an overfit signature, not an edge.

## Decision rule — fixed in advance

- **>=1 cell passes G1-G5** → evaluate the single best such cell (by train PF) on `test`,
  once. Adopt only if `expectancy_test > 0` and `PF_test >= 1.15`.
- **0 cells pass** → GNG stays frozen at production thresholds. No further threshold work.
  Proceed with ORB v3 promotion to forward testing on its own merits.

Under no outcome does this test authorise re-tuning `STOP_PCT`. The promotion audit already
recorded that the test window was consumed by stop retuning (~10-12% haircut); spending more
of that budget is out of scope here.

## Known confounds, recorded in advance

- train (2010-2018) is a structurally different vol regime from val/test; a cell passing
  train and failing val may be regime, not overfit. Either way it fails the gates.
- The combined ORB+GNG book only exists from 2021, so train/val results are GNG-standalone
  and do not directly translate to combined-book metrics.
- Loosening raises exposure to the 09:30 open; TCA is unchanged in this test (COST is flat).
  A cell that passes on gates but relies on many marginal small-gap trades should be
  re-costed before adoption.

---

# Outcome (executed 2026-08-03)

Base cell reproduced exactly (N=128, $+5,773 on 2021-01-01..2026-07-31), so the
simulate-once-and-subset construction is verified.

**19 of 36 cells passed G1-G5.** By the filed rule (best surviving cell by train PF) the
adopted cell is **gap >= 0.40%, ext >= 0.05%**, and it passed the test gate:
test N=90, PF 1.342, expectancy +$39.78, total $+3,580. By the letter of the rule: ADOPT.

**It was not adopted.** The consequence check shows the winner is worse than production
everywhere that matters:

| | base 0.30/0.10 | winner 0.40/0.05 |
| --- | ---: | ---: |
| test PF | 1.386 | 1.342 |
| 2021+ N / PF / expectancy | 128 / 1.369 / $45.10 | 138 / 1.244 / $30.28 |
| 2021+ total | $+5,773 | $+4,178 |
| 2021+ bootstrap P(total < 0) | 7.26% | **14.83%** |
| 2021+ losing years | 1 | 2 (2023 -$251, 2025 -$2,119) |
| combined book Calmar | 1.418 | 1.257 |
| combined book net$/MaxDD$ | 8.291 | 6.554 |

## Recorded defect in this pre-registration

The selection metric — *highest train PF among surviving cells* — is **misaligned with the
hypothesis under test.** PF is maximised by taking fewer, higher-quality trades, which is the
opposite of "raise N". The rule therefore selected a cell that is *tighter* on gap (0.40% vs
0.30%) and only loosened the extension floor. A hypothesis about sample size should have
pre-registered a sample-size-aware objective (e.g. maximise pooled t-statistic, or minimise
bootstrap P(total<0), subject to the same gates).

This is recorded rather than corrected. Re-selecting a different cell now, after seeing all
36 results across val and test, would be exactly the p-hacking the split design exists to
prevent. The val and test splits are now consumed for this parameter family; a corrected
test would require data we do not have.

## Second finding — an earlier claim of mine was wrong

The framing that motivated this test ("GNG's edge is not statistically distinguishable from
zero, P(total<0) = 7.3%") was computed on the **2021+ window only**, because that is where
the combined ORB+GNG book starts. On the full history the base cell is far better
established: train+val pooled N=133, $+7,523, **P(total<0) = 0.18%**; adding test gives
N=215, $+11,245 across 2010-2026.

GNG's edge is real and established on 16 years. What is weak is specifically its
**2021-2026 slice**, which is also the only window where it can be evaluated as part of the
combined book. Both statements are true and they are not in conflict.

## Decision

- GNG stays frozen at production thresholds (gap 0.30%, ext 0.10%, stop 1.5%). No further
  threshold work.
- The loosening hypothesis is **not supported**: no route from this grid improves the traded
  book, and the route the rule selected degrades it.
- Proceed with **ORB v3 promotion to forward testing** on its own merits.
