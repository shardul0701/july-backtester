# Promotion audit — ORB v3 + gap-and-go (rule C combined)

**Date:** 2026-08-02 · **Auditor:** independent pass (Gate 5.4), script = `STRATEGY_REALITY_CHECKLIST.md`
**Question:** is this safe to promote to forward testing?
**Verdict:** **GO for micro-size forward test. NO-GO for capital promotion.** Two material findings, neither an artifact.

Everything below was recomputed from source, not read off prior reports.

---

## Gate 0 — Data integrity: PASS

- Stitch audit `NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md` = PASS; 8 Databento-degraded sessions
  excluded explicitly in `intraday_lib_long.GAP_SESSIONS` rather than trusted.
- Frozen→overlay seam checked directly: frozen last close 28,710.25 (2026-07-16 15:59) →
  overlay first open 28,589.25 (20:00) = **−0.42%**, a plausible overnight move. No scaling
  break, no roll discontinuity, price path continuous across the join.
- Overlay carries `close_unadj` / `symbol` / `source_family` — properly built, not a naive append.
- Survivorship: N/A (single instrument).

## Gate 1 — Causality: PASS on mechanics, **FAIL on selection**

**Mechanics (1.1–1.5): clean.**
- Overnight context (`_build_overnight_table`) reads midnight, 08:30 ET, and the 09:30 RTH open.
  Session tagging in `load_full_long` rolls at 18:00, so all three belong to the trade date and
  are all known by 09:30. Earliest possible ORB entry is bar 4 (09:50). **No lookahead.**
- OR built from bars `[0:3]`; breakout scanned from `i=3` forward; retest from `breakout_i+1`;
  entry at `j+1` **open**. Strictly forward-only, no peeking at the confirming bar's close.
- Gap-and-go `prior_close = close_last.shift(1)` — causal.
- **1.3 same-bar target/stop ambiguity: NOT APPLICABLE.** Neither leg has a target. The only
  exits are the stop and the session close. This is the structural reason this pair avoids the
  exact bug that killed Sleeve A and the vol-squeeze hypothesis.
- **1.4 entry-bar inclusion:** stop scan starts at `k=entry_i` (ORB) and `i=1` (GNG). Correct.

**1.6 selection lookahead: FAIL — the test window has been consumed.**

`orb_combined_base.py` docstring, v3 (2026-07-29):
> "1.0% is the tightest level that still improves PF on **BOTH splits simultaneously**"

That selects a parameter using test-split performance. It happened at least twice on the same
2024-2026 slice (v2 → 1.5% on 2026-07-19, v3 → 1.0% on 2026-07-29), on top of the original
`MAG_TH` confirmation. **"Test PF 2.42" is therefore an in-sample number, not OOS.**

Sizing the damage from the fragility grid (all variants still clear zero — this is a slope, not
a cliff): chosen stop 1.0% Exp +50.98 vs grid mean +45.4; chosen mag 0.15% Exp +50.98 vs grid
mean +45.8. **Expect roughly a 10–12% expectancy haircut versus the headline.**

Note also ORB uses its own dev/test split (2021-23 / 2024-26) rather than the canonical
`intraday_lib_long.SPLITS`. Justified by the regime argument, but it is split-shopping in form.

## Gate 2 — Execution honesty: PASS (strong)

Did not trust the stored CSV — the stored Gate-2 rows are byte-identical to baseline, which is
the exact signature of a no-op audit path (how Sleeve A hid). **Re-ran
`orb_v3_surgical_execution_test.py` directly:**

```
counters: {n_gap_fill: 0, gap_slip_pts: 0.0, n_stop: 31}
trades with changed P&L: 19 / 134
total delta (surgical − original): −2.56 pts   (+6831 → +6829)
```

31 stops genuinely resolved on real 1-minute bars with tick-grid rounding and gap-aware
worse-of fills. The path executes; the identity was real, not a silent skip. Damage = **0.04%
of P&L**. Same for gap-and-go (v0/v2/v3 identical, `n_gap_through=0`).

- **2.1 no protection-free windows:** the stop is set at entry and never moves — no arming,
  ratchet, or move-to-BE states. Structurally immune to the Sleeve A failure mode.
- **2.4 tick grid:** verified in the surgical variant.
- **2.5 costs:** 1.25 pts round-trip = $2.50 on MNQ, versus realistic ~$1.00 commission +
  0.25pt spread. Honest-to-conservative. Holds to 3x in the battery, 5x at portfolio level.
- Post-freeze fill arithmetic verified by hand: entry 28,175.50 − fill 27,752.87 − 1.25 =
  **−423.88** ✓ exact.
- ⚠️ `n_gap_through = 0` is a **window-specific observation, not a structural guarantee.**
  Gapped stop fills remain possible live.

## Gate 3 — Statistical robustness: PASS, with declared regime risk

- **3.1 placebo (the strongest evidence):** 500 draws, matched exit shape, hour-of-day matched.
  ORB real Exp +50.98 vs placebo band [−15.76, +14.47]; GNG +22.33 vs [−14.10, +15.07].
  Both **p = 0.0000**. Decisive.
- **3.3 fragility:** ORB — all 6 variants clear zero, but chosen stop is the grid **argmax** and
  0.75% is 32% worse (real slope). GNG — all 9 clear zero and the chosen stop is **not** the
  argmax (1.0% scores better), which is a good sign of non-fitting.
- **3.4 era robustness:** ORB is explicitly regime-dependent (pre-2021 PF 0.93) — declared, not
  discovered. GNG has **4 losing years of 17** (2012, 2014, 2016, and **2025 at −488.63**).
- **3.5** day-clustered bootstrap used throughout ✓.
- **3.7 samples:** ORB N=134, GNG N=126, overlap N=**16** (thin — supports not cutting exposure,
  does not support escalating it).
- Legs are near-uncorrelated: **daily r = +0.043**. The diversification claim is genuine.

## Gate 4 — Sizing realism: PASS

Realized-loss ÷ sized-risk ≈ **1.0x** (worst post-freeze loss 422.63 pts vs intended 1.5% =
422.6). No unbounded-risk states: both legs are flat overnight behind hard stops. Concurrency
bounded at 2 legs.

---

## FINDING 1 — Fresh out-of-sample data exists, was never looked at, and is negative

The frozen window ends 2026-07-16, but the daily overlay has extended the dataset to
**2026-07-31**. That is **11 sessions of genuinely untouched out-of-sample data** — no
parameter, filter, or split has ever seen it.

> **CORRECTED 2026-08-02 (same day, after first publication).** The original version of this
> table reported **ORB v3 = 0 signals**. That was wrong. It was not re-derived — ORB was not
> re-run against the grown overlay before the table was written. `orb_combined_base.py` loads
> via `load_rth_long()` → `_read_stitched()`, which appends the daily overlay, so **ORB
> self-updates with the data**; a fresh `run_orb_combined_base()` returns **137** trades from
> 2021 (134 frozen + 3 fresh), not 134. The corrected table is below. Every figure downstream
> of it in this section has been recomputed. See "Correction note" at the end of this finding.

| Leg | Signals | Result |
|---|---|---|
| ORB v3 | **3** | 2 winners, 1 stopped — **net −41.07 pts = −$82.14** (roughly flat) |
| Gap-and-go | **2** | **both losers, −777.13 pts = −$1,554.26** |
| **Combined** | **5 active sessions** | **−818.20 pts = −$1,636.41** |

| Session | Leg | Dir | Entry | Exit | Pts | Exit reason |
|---|---|---|---:|---:|---:|---|
| 2026-07-20 | GNG | Long | — | — | −353.25 | Session Close |
| 2026-07-23 | ORB | Short | 28,123.25 | 28,107.75 | +14.25 | Session Close |
| 2026-07-28 | ORB | Short | 27,207.25 | 27,479.32 | −273.32 | Stop Loss |
| 2026-07-30 | ORB | Long | 27,503.25 | 27,722.50 | +218.00 | Session Close |
| 2026-07-31 | GNG | Long | — | — | −423.88 | Stop Loss |

All five fills verified honest. Both ORB stops/exits reconcile exactly to
`(exit − entry) × dir − 1.25 = pnl`; the two GNG fills were checked for gap slippage and had
**zero**. These are real results, not artifacts.

**Impact:** −$1,636.41 is **7.8% of the combined strategy's entire 2021-2026 profit, given back
in 11 sessions.** Including it: total return 20.99% → **19.35%**, CAGR 3.52% → **3.23%**,
Calmar 1.550 → **1.422**. Max drawdown is **unchanged** at 1,167.09 pts / $2,334.18 = −2.27% —
the fresh losses did not carve a new low, they gave back a recent peak.

**Context that matters:** NQ fell 8.4% over that window (29,279 → 26,829). Gap-and-go is
**long-only**. This is precisely its known structural weakness, and it is directionally
consistent with 2025 being its losing year. N=2 is not statistically decisive — but it points
the wrong way.

**What the correction changes in the argument.** The original "ORB = 0" reading let this
section conclude that the fresh loss was *entirely* gap-and-go's known long-only weakness, with
ORB simply silent. That is no longer the story. ORB did trade, and it also gave back ground:
3 signals for −41.07 pts against a benchmark expectancy of roughly +45 pts/trade (Finding 2's
haircut figure), i.e. about **176 pts below expectation**. In ORB's favour, it went 2-for-3 on
direction and its single loss was a clean stop — this is a flat-to-slightly-soft result, not a
breakdown, and N=3 carries almost no statistical weight. But the honest summary is *both legs
underperformed in the only untouched window*, not *one leg was tested and the other wasn't*.
It also means ORB is no longer "zero OOS evidence" — it has 3 observations, and they are mildly
negative.

**Do not "fix" this.** Both GNG losses had unusually large gaps (0.99%, 1.25%) and a gap-cap
filter would have caught them — that filter was already tested and **rejected as overfit**.
Adding it now would be fitting to two trades. The same applies to ORB's 07-28 stop.

**Correction note — how this was found and one residual discrepancy.** The error surfaced while
rebuilding the trade list from source to feed the tearsheet: the independent rebuild produced
5 fresh active sessions where this report claimed 2. `run_orb_combined_base()` was re-run and
returned 137 trades from 2021 vs the 134 in the frozen window, with all three extra fills
reconciling exactly. Root cause was a missing re-run, not a data-scope or loader difference.

**Basis note on CAGR/Calmar.** The restated frozen figures (3.52% / 1.550) agree with the
original (3.51% / 1.543) to within rounding; max drawdown and total return agree exactly. Both
are now computed on the **tearsheet's own basis** — `trade_analyzer.calculations`, peak-relative
drawdown, equity series starting one day before the first trade (5.503 yrs frozen, 5.563 yrs
extended) — so this report and the generated tearsheet print identical numbers. An intermediate
draft of this correction briefly quoted 3.50% / 1.500 / −2.33%; that used **initial-capital-**
**relative** drawdown rather than peak-relative and was wrong. Peak-relative (−2.27%) is the
standard definition and is what stands.

## FINDING 2 — The `END = 2026-07-16` cutoff is now a live reproducibility hazard

`portfolio_overlap_sizing.py` filters GNG to `≤ 2026-07-16`; `survivors_gate23_battery.py`
does not. They therefore disagree on 2026 (1,074 vs 1,851 pts) purely because of the cutoff.
Anyone re-running the battery today silently gets different numbers than the frozen report.
The window needs to be an explicit, shared, dated constant.

## Minor

- `orb_combined_base.py` docstring claims **N=136**; actual is **N=134**.
- CVaR has two valid bases and they differ ~2x — `report.md`'s **−163.16 pts** is
  **per-calendar-session** (72-day tail of 1,428); the one-pager's **−$653.64** is
  **per-active-signal** (13-day tail of 244, ×$2). Both reproduce exactly. Label them or
  they will be confused.
- The one-pager's "era-robustness" line in the Gate Ladder callout is **too strong for
  gap-and-go** given 4 losing years. ORB earns it; GNG does not.

## Numbers reconciled ✓

Independently recomputed from `daily_overlap_rule_pnl.csv`: 1,428 sessions, 134 ORB fires,
126 GNG fires, 16 overlap; total +20.99%, CAGR +3.51%, MaxDD −2.27%, Calmar 1.543, 244 signals,
WR 63.5%, PF 1.866, Exp +$86.03, SQN 3.39. **Every figure on the one-pager is arithmetically
correct.**

---

## Verdict and conditions

Nothing here is an execution artifact. This pair passes the gates that killed everything else
in this program — causality, execution honesty, and placebo. The residual risks are **overfit
risk** (a consumed test window) and **regime risk** (declared for ORB, demonstrated for GNG),
and forward testing is exactly the instrument that measures both.

**GO — micro-size forward test**, on these conditions:

1. **Trade both legs, size them differently.** ORB carries the evidence; **gap-and-go goes on
   probation** at reduced or paper size (losing 2025 + 0-for-2 OOS + long-only into a downtrend).
2. **Benchmark against a haircut headline, not the backtest.** Expect ~+45 pts/trade, not
   +50.98. Anything at or above that is on-track.
3. **Pre-register kill criteria before the first fill** — e.g. ORB expectancy < 0 over 30+
   signals, or GNG down another 500 pts. Write them down now so they cannot be renegotiated later.
4. **Extend the window and re-run monthly.** The overlay is already producing free OOS data;
   fix the `END` constant so it is captured instead of silently truncated.
5. **Log every live fill against the backtest's assumed fill** (Gate 5.5). Divergence in fills
   is a Gate-2 failure in production.

**NO-GO for capital promotion** until the forward test produces its own untouched sample.
The 2024-2026 "test" numbers cannot be used as the promotion evidence — they were selected on.
