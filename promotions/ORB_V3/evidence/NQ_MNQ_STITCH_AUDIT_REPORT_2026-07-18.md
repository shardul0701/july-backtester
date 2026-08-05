# Independent Audit: NQ+MNQ 16-Year Stitched Dataset

Audit date: 2026-07-18
Auditor: Claude (independent read-only review, no source/output files modified)
Scope: `processed_data/nq_mnq_ETH_clean_stitched.parquet`,
`processed_data/nq_mnq_RTH_clean_stitched.parquet`,
`processed_data/build_nq_mnq_long_history.py`, and their build report
(`processed_data/NQ_MNQ_LONG_HISTORY_BUILD_REPORT.md`).

## Executive Verdict

**Trustworthy for research use, with one caveat to apply.** Every number in
the build report independently reconciles exactly. The pipeline logic
(contract-roll detection, back-adjustment, NQ/MNQ overlap alignment, session
tagging) is sound and, for the parts it reuses, matches the already-audited
MNQ-only pipeline almost line-for-line. The one real finding — 6 fully
missing 2014 sessions and 2 truncated 2020 sessions — was traced to its root
cause and is **an upstream Databento source-data gap, not a defect in this
project's code**. It affects 8 of roughly 2,681 NQ-era trading days (0.3%)
and does not corrupt any adjacent price data. Treat those 8 dates as no-data
days (skip any signal/backtest computation on them) rather than distrusting
the file as a whole.

## Findings, Ordered By Severity

### F1 (LOW — data completeness, root-caused, not a code defect): 6 missing 2014 sessions + 2 truncated 2020 sessions

The build report's own integrity check (`session_rule_leaks`,
`duplicate_timestamps`, `ohlc_violations` — all 0) does not test for missing
data, so this gap was not surfaced anywhere in the report or the audit log.
Independently re-deriving session-by-session bar counts from the stitched
ETH/RTH files found:

- **Fully missing weekday sessions** (zero rows in both ETH and RTH):
  2014-06-12, 2014-06-13, 2014-09-23, 2014-09-24, 2014-09-25, 2014-12-31.
- **Intraday truncations**: 2020-02-28 (ETH stops at 10:58:00 ET, nothing
  after, during the COVID-crash volatility week) and 2020-06-30 (a 590-minute
  intraday hole, 10:10:00→20:00:00 ET).

**Root cause, confirmed against raw source** (`nq_1m_master.parquet`, the
cached Databento decode, checked *before* any of `build_nq_mnq_long_history.py`'s
contract-selection/roll logic runs):

- All 6 missing dates have **zero rows in the raw master file** — the gap
  exists upstream of this project's code entirely. `original_data/nq_2010_2021_import/condition.json`
  (Databento's own per-session coverage metadata) flags exactly 19 sessions
  as `"degraded"` across the full import; the 6 zero-row dates are a subset
  of those 19 (the other 13 degraded sessions have partial-but-nonzero data,
  412–3,548 rows vs. a normal day's ~1,200–2,600). This one-to-one
  correspondence is conclusive.
- Both truncated dates reproduce **exactly** in the raw master (2020-02-28
  stops at 10:58:00 ET in the raw file too; the 2020-06-30 590-minute hole is
  present verbatim in the raw file) and both are also flagged `degraded` in
  `condition.json`.
- `rebuild_nq()` (the roll-selection function, `build_nq_mnq_long_history.py`
  lines ~74-127) cannot structurally drop a session that has zero raw rows —
  its dominant-contract and forward-only-roll logic only operates on rows
  that already exist. There is no code path by which working source data
  becomes a missing day.

**Boundary check (no hidden corruption at the gap edges):** the bar
immediately before and after each of the 8 gaps was compared. All 5 gap
transitions (three of the six 2014 dates cluster into one multi-day gap each)
produce moves of −0.38%, −0.53%, −0.51%, +0.26%, and +0.71% — ordinary
overnight/holiday-scale moves, not anomalies. A global scan of the entire
5,438,521-row file for the 15 largest point-jumps between any two
chronologically adjacent rows found none of them coincide with these 8 gap
dates; the largest jumps in the whole file are routine Friday-close→Sunday-open
weekend gaps (up to −4.32%) and known scheduled-release-minute moves (08:29→08:30
ET CPI/employment prints), all larger in magnitude than anything at the 8 gap
boundaries.

### F2 (INFORMATIONAL — precision note, not a defect): overlap alignment reconciles open/close tightly, high/low slightly less so

The 4,095-bar NQ/MNQ overlap window's single alignment offset (2,839.5
points, close-derived) was independently re-verified on **all four** OHLC
columns, not just close (the build report only reports close):

| Column | Median abs diff before | Max abs diff before | Median abs diff after | Max abs diff after |
|---|---|---|---|---|
| open | 2839.5 | 2841.5 | 0.25 | 2.25 |
| high | 2839.5 | 2844.0 | 0.25 | 4.50 |
| low | 2839.5 | 2841.5 | 0.25 | 5.00 |
| close | 2839.5 | 2841.75 | 0.25 | 2.25 |

Open and close collapse to a max 2.25-point residual after the offset — as
tight as the build report's close-only figure implied. High and low have
slightly larger residuals (max 4.50 and 5.00 points respectively), which at
a ~13,000-point index level is ≤0.04% — expected, since NQ and MNQ trade in
separate (though tightly arbitraged) order books and can print marginally
different intra-minute extremes even when opens/closes match almost exactly.
Not a red flag; noted for completeness since the build report didn't check it.

## What Was Verified Clean

1. **Row counts, range, monotonicity**: ETH 5,438,521 rows (2010-06-06
   18:00→2026-07-16 19:59 ET), RTH 1,589,712 rows — both exactly match the
   build report; both monotonic, zero duplicate timestamps.
2. **OHLC internal consistency**: zero violations (high<open/close/low,
   low>open/close) across both files.
3. **No large unexplained adjacent-minute jumps**: zero bars exceed 3% or 5%
   in either file; worst adjacent-minute move in the whole ETH series is
   ~2.43% (CPI-release-minute bars), consistent with normal market behavior.
4. **NQ→MNQ splice**: exactly one regime switch, at 2021-02-18 19:00:00 ET
   (`source_family` column: NQ before, MNQ from there on). Price continuity
   at the splice is unremarkable (16456.00→16454.25, a normal one-minute
   move) — no artificial jump introduced by the stitch itself.
5. **Overlap cross-check against the existing trusted MNQ file**: the entire
   MNQ portion of the stitched file (1,914,325 rows) matches
   `mnq_1m_ETH_clean_forward.parquet` bar-for-bar; 200 randomly sampled
   overlapping timestamps showed 200/200 exact `close` matches, max absolute
   diff 0.0.
6. **Contract-roll offsets**: of the 44 quarterly NQ rolls (2010-06-11
   through 2020-12-14, all `used_fallback_zero=False`), the three largest
   (2018-03-09 +27.25, 2019-03-08 +29.25, 2020-03-16 −12.75, the last
   spanning the COVID-crash roll) were independently recomputed from raw
   anchor-minute close prices and matched the audit CSV **exactly** in all
   three cases.
7. **Roll/back-adjustment methodology**: `rebuild_nq()` reuses the existing,
   already-audited MNQ pipeline's dominant-contract-by-volume selection,
   forward-only roll enforcement, and cumulative backward difference
   back-adjustment (`forward_data_pipeline.py::rebuild_forward`) essentially
   line-for-line, retargeted at NQ symbology. The NQ/MNQ cross-family overlap
   alignment (`audit_and_stitch`) reuses the same median-close-diff pattern
   as the existing `stitch_forward` overlap alignment, correctly adapted for
   prepending an older segment rather than appending a newer one.
8. **`expiry_rank()` decade-disambiguation** (new code, no precedent in the
   existing MNQ pipeline, needed because Databento lists NQ contracts up to
   ~13 months forward so e.g. `NQZ0` can appear tagged with calendar-year
   2019 for the Dec-2020 contract): verified correct against a real ambiguous
   row pulled from `nq_1m_master.parquet` — `NQZ0` at 2019-12-02, close
   8523.25, correctly resolves to December 2020 (matching the actual
   Nasdaq-100 futures price level at that time) rather than misresolving to
   December 2010 (price level ~2100, which would have been the naive/buggy
   result). Also confirmed correct on 4 unambiguous rows spanning 2010–2021.
9. **Session tagging and maintenance/weekend filtering**: identical
   `(index + 6h).date` CME-session convention and identical 4-condition
   session-rule filter (17:00 maintenance hour, Friday≥17:00, Saturday,
   Sunday<18:00) as the existing MNQ pipeline; zero session-rule leaks
   reported and independently confirmed.

## Minor Methodology Notes (not defects, carried forward from the code-review pass)

- The ETH large-jump integrity check in `build_nq_mnq_long_history.py`'s
  `integrity()` function uses an unmasked `pct_change()` rather than the
  gap-masked technique already proven out in `make_eth_rth_forward_files.py`.
  This audit closed that gap manually (see F1's boundary check) and found no
  hidden issue, but the project's own reusable `integrity()`/`audit()`
  functions would benefit from adopting the stricter gap-masked pattern so
  future builds don't need a manual re-check.
- There is no standalone audit script/report for the NQ/MNQ stitch analogous
  to `make_eth_rth_forward_files.py`'s dedicated `audit()` — the audit lives
  inline in the build script. Fine for a one-time historical build; would be
  worth extracting if this pipeline is re-run periodically.
- RTH slicing has no explicit `dayofweek<5` guard (relies on the upstream
  session-rule filter already having removed all weekend rows). Verified
  benign in practice — RTH's 09:30–16:00 window never overlaps the excluded
  hours — but structurally different from the reference pipeline's explicit,
  self-contained weekday check.

## Bottom Line

The dataset is safe to use as the basis for extending MNQ ORB research back
to 2010. No leakage, no contamination, no fabricated or corrupted data was
found anywhere in the 16-year series. The only real defect — 8 missing/
truncated trading sessions — originates in Databento's own source coverage
(confirmed via their `condition.json` degraded-session flags), not in this
project's pipeline, and is small enough (0.3% of NQ-era sessions) to simply
exclude from any date-indexed analysis rather than requiring a rebuild. No
fix to the pipeline code is required before using this data for research.
