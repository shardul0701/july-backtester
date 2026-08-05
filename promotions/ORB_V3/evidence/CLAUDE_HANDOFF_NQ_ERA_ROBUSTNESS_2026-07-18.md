# Claude Handoff: NQ-Era Robustness Test

Date: 2026-07-18

## Scope

This validates, rather than re-searches, the frozen US-RTH MNQ strategies on
previously unused NQ history.

- Pre-period: 2010-06-07 through 2021-02-18, NQ continuous history.
- Post-period: 2021-02-19 through 2025-12-31, MNQ history.
- 2026 was excluded and remains untouched.
- Excluded Databento-degraded dates: `2014-06-12`, `2014-06-13`,
  `2014-09-23`, `2014-09-24`, `2014-09-25`, `2014-12-31`, `2020-02-28`,
  and `2020-06-30`.
- All outputs are index points after the existing 1.25-point round-trip cost.
  They are not NQ-versus-MNQ dollar PnL comparisons.

## Data

- Dataset: `processed_data/nq_mnq_ETH_clean_stitched.parquet`.
- Data construction/audit: `processed_data/build_nq_mnq_long_history.py` and
  `processed_data/NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md`.
- The NQ portion is difference-back-adjusted and retains `close_unadj`; the
  MNQ portion is the original trusted MNQ data, unchanged.
- The stitch audit verified ordering, duplicate/OHLC checks, overlap handling,
  roll logic, and source data gaps. The eight excluded sessions are upstream
  Databento degradation, not project-pipeline failures.

## Frozen Rules and Context

Runner: `research/extended_history_five_strategy_test.py`.

1. H03: 15m opening range, 5m close confirmation, next-open entry, RTH close.
2. H04 1m: 30m opening range, 1m close confirmation, next-open entry, RTH close.
3. H04 5m: 30m opening range, 5m close confirmation, next-open entry, RTH close.
4. H06: 15m opening range breakout, edge retest, renewed close, next-open entry, RTH close.
5. H09: first unambiguous 0.5% move from the open, next-open entry, opposite
   0.5% stop or RTH close. A bar touching both levels is rejected.

Primary context policy: existing deterministic `overnight_trend_align_move40`:
direction agrees with overnight position relative to both reference levels and
the matching overnight move is at least 40 points. The legacy Model A veto was
not used because it remains experimental.

## Implementation Validation

- Vectorized runner versus canonical row-by-row functions: 256 independent
  strategy-day cases, all 256 matched on signal/entry time, direction, entry,
  exit, and PnL.
- New-run post-MNQ results reproduce all frozen source trades and context
  fields exactly:

| Rule | Expected | Actual | Match |
|---|---:|---:|---|
| H03 | 1,212 | 1,212 | True |
| H04 1m | 1,203 | 1,203 | True |
| H04 5m | 1,194 | 1,194 | True |
| H06 | 1,090 | 1,090 | True |
| H09 frozen | 1,065 | 1,065 | True |

Machine-readable replication evidence:
`research/context_outputs/MNQ_five_strategies_extended_history_post_replication_audit.csv`.

## Results

Format: N, win rate, expectancy in points, profit factor.

| Rule and policy | Pre NQ 2010-2021 | Post MNQ 2021-2025 |
|---|---|---|
| H03 raw | 2,645, 45.7%, -3.27, 0.81 | 1,212, 54.0%, +8.51, 1.16 |
| H03 context | 17, 41.2%, -24.13, 0.69 | 94, 62.8%, +29.33, 1.44 |
| H04 1m raw | 2,629, 44.8%, -4.24, 0.75 | 1,203, 53.9%, +5.67, 1.11 |
| H04 1m context | 18, 44.4%, -39.50, 0.57 | 92, 62.0%, +38.06, 1.61 |
| H04 5m raw | 2,603, 45.3%, -3.89, 0.77 | 1,194, 54.4%, +4.38, 1.08 |
| H04 5m context | 19, 36.8%, -56.58, 0.43 | 95, 62.1%, +30.23, 1.48 |
| H06 raw | 2,410, 45.4%, -3.77, 0.78 | 1,090, 53.2%, +5.21, 1.09 |
| H06 context | 14, 42.9%, +26.63, 1.64 | 83, 62.7%, +39.88, 1.64 |

The four price-difference-based rules are negative raw before 2021. H06
context is positive but has only 14 pre-period trades, so it is insufficient
evidence; the other three pre-period context tests are negative.

## H09 Caveat and Native-Price Diagnostic

Frozen H09 uses a percentage threshold on additively back-adjusted continuous
OHLC. It must remain that way to reproduce the frozen post-2021 source trades,
but that exact threshold is not cross-era comparable: a fixed additive
adjustment changes a percentage-of-open threshold. Do not use frozen-H09
pre-period output as cross-era evidence.

Separate diagnostic: `H09_native_price_symmetric_0.5%`. It derives native
OHLC as adjusted OHLC minus `close - close_unadj`. The offset was checked
across all 4,139 RTH sessions: maximum within-session range was exactly 0.0
points, so the intraday derivation is valid.

| H09 native-price diagnostic | Pre NQ 2010-2021 | Post MNQ 2021-2025 |
|---|---|---|
| Raw | 2,091, 46.3%, -0.32, 0.98 | 1,106, 50.3%, +9.63, 1.19 |
| Context | 14, 21.4%, -9.00, 0.85 | 83, 63.9%, +64.11, 2.37 |

The diagnostic is also negative before 2021. It is not a replacement for the
frozen H09 specification.

## Current Conclusion

The evidence does not support a stable 2010-2025 edge. It supports a
post-2021 regime-specific interpretation. No rule was modified or promoted,
and no 2026 evaluation was performed from this result.

## Files to Audit

Read in this order:

1. `processed_data/NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md`
2. `processed_data/build_nq_mnq_long_history.py`
3. `research/extended_history_five_strategy_test.py`
4. `research/context_outputs/MNQ_five_strategies_extended_history_post_replication_audit.csv`
5. `research/context_outputs/MNQ_five_strategies_extended_history_summary.csv`
6. `research/context_outputs/MNQ_five_strategies_extended_history_yearly.csv`
7. `research/context_outputs/MNQ_five_strategies_extended_history_side.csv`
8. `research/context_outputs/MNQ_five_strategies_extended_history_outlier.csv`
9. `research/context_outputs/MNQ_five_strategies_extended_history_trades.csv`
10. `research/context_outputs/MNQ_five_strategies_extended_history_report.md`
11. `research/CONTEXT_MODEL_AUDIT_LOG.md`

Audit questions:

- Are signals based only on completed bars, entries next-bar-open, and exits
  causal? Is H09 ambiguous-bar rejection preserved?
- Are boundaries, exclusions, source handling, and context logic implemented
  correctly, without look-ahead?
- Does post-MNQ replication prove the runner and stitched MNQ slice reproduce
  the frozen output?
- Is the H09 native-price derivation and its diagnostic-only framing correct?
- Are results and the regime-specific conclusion faithful to the CSV outputs?
