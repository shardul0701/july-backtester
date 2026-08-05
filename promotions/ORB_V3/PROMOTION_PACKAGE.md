# ORB v3 (MNQ) — Promotion Package

**Strategy:** ORB v3 — 15-minute opening-range retest-confirm, overnight-context gated, on MNQ 5-minute bars
**Instrument:** MNQ (Micro E-mini Nasdaq-100), 1 contract fixed, $2/point
**The ask:** review for a **micro-size forward test**. **Not** capital promotion.
**Assembled:** 2026-08-03. Every number below was recomputed from source for this package, not copied from a prior report.

---

## 0. Read this first — three things that are not in our favour

Putting these at the top on purpose. A gate table that leads with passes and buries the failures wastes the reviewer's time.

1. **The test split is consumed.** ORB v3's 1.0% stop was selected because it was "the tightest level that still improves PF on **both splits simultaneously**" — that is selection on test-split performance, and it was done twice on the same 2024–2026 slice (v2 chose 1.5% on 07-19, v3 chose 1.0% on 07-29). **The headline "test PF 2.42" is in-sample.** Gates 1.6 and 3.2 fail on this. Do not read the dev/test split in this package as a clean out-of-sample result.

2. **The only genuinely untouched window is negative.** The frozen window ends 2026-07-16; the daily data overlay now runs to 2026-07-30, giving 3 untouched signals: **−41.07 pts (−$82.15)**. Small N, but it is the only evidence nobody could have fitted to, and it is soft.

3. **The edge is regime-dependent and we can prove it against ourselves.** Pre-2021, same filter, scale-invariant threshold, sample size restored: **N=63, WR 44.4%, Exp −7.60, PF 0.74.** This is not a timeless ORB law. It is tied to the post-2020 market-structure shift. If 2021+ sign-flips, that is the regime ending, not a bug.

**What we are asking to be believed is narrower than "this strategy works":** that the mechanics are honest, the costs are real, the 2021–2026 result is not an execution artifact, and that a micro forward test is the correct next measurement. Points 1–3 are exactly why we are **not** asking for capital.

---

## 1. Frozen specification

Canonical source: [`source/orb_combined_base.py`](source/orb_combined_base.py) (262 lines, locked 2026-07-29).
Note: "combined" in the filename means the *three ORB filters* combined — it is **not** ORB+gap-and-go. This package is ORB v3 **alone**.

| Parameter | Value | Where chosen |
|---|---|---|
| `MAG_TH` | `0.0015` (0.15% of open) | Dev slice **only** (2021-01-01 → 2023-12-31). Clean. |
| `STOP_PCT` | `0.01` (1.0% intraday) | ⚠️ Dev **and test** (see §0.1). Consumed. |
| `COST` | `1.25` pts round-turn | Fixed, conservative — see §4 |
| Point value | `$2.00` | MNQ contract spec |

**Mechanics, one signal per session, hold to RTH close unless stopped:**

1. **Opening range** — first 15 minutes (3 × 5-min bars) of RTH. `hi = high[:3].max()`, `lo = low[:3].min()`.
2. **Breakout** — first bar after the OR whose *close* is beyond `hi` (long) or `lo` (short).
3. **Retest-confirm** — price must touch back to the broken level, *then* close beyond it again. Entry is the **next bar's open**. This is what makes it pickier than plain-breakout ORB and cuts signal count hard.
4. **Context gate** — direction must agree with overnight bias: RTH open above **both** the midnight and 08:30 ET reference closes for longs, below both for shorts.
5. **Magnitude gate** — overnight move (open vs. the *nearer* of the two references) ≥ 0.15% **as a percentage of open price**. The percentage form is the fix for a real scaling bug in the earlier absolute 40-point version (NQ rose ~11.5× 2010→2025, so a fixed point threshold was ~11.5× more selective in 2010 — it starved pre-2021 sample size to 14–19 trades and made a real regime absence look like proof of nothing).
6. **Stop** — 1.0% of entry, bar-by-bar low/high scan from the entry bar inclusive to RTH close.
7. **Exit** — RTH close, or the stop, whichever comes first. **No target.**

**Why the absence of a target matters for auditing:** with no target there is no same-bar target-vs-stop ordering question, so this strategy is structurally immune to the resolve-by-code-order bug that killed the intraday vol-squeeze and inflated Sleeve A. And because the stop never moves, there is no protection-free window. Those two absences are the reason Gate 2 comes back as clean as it does — it is structural, not luck.

---

## 2. Headline numbers — verified from source 2026-08-03

Reproduce with `python source/orb_combined_base.py`. Both rows below came out of that run.

| | Frozen (2021-01-01 → 2026-07-16) | **Full, incl. free OOS (→ 2026-07-30)** |
|---|---|---|
| Signals | 134 | **137** |
| Win rate | 63.4% | **63.5%** |
| Expectancy | +50.98 pts / $+101.96 | **+49.56 pts / $+99.13** |
| Profit factor | 2.340 | **2.264** |
| Net | +6,831.3 pts / $+13,662.63 | **+6,790.2 pts / $+13,580.48** |
| Max drawdown | −944.3 pts / $−1,888.59 | **−944.3 pts / $−1,888.59** (unchanged) |
| net$ / MaxDD$ | 7.234 | **7.191** |
| Losing years | 0 | **0** |
| Stop-outs | 19 | 20 |

**Use the right-hand column.** It includes the 3 untouched sessions. Max DD is *unchanged* because the fresh window gave back a recent peak rather than carving a new low.

**Benchmark expectancy to the haircut, not the headline.** Because the stop was tuned on test (§0.1), the fragility grid mean (+45.4 pts) is the honest forward expectation, not +50.98 — a **~10–12% haircut**. ORB's chosen stop sits at the grid argmax, which is exactly the shape you'd expect from test-set selection. The grid shows a slope, not a cliff (all variants clear zero), which is why this is a haircut and not a rejection.

**Yearly (full, incl. fresh):**

| Year | N | WR | Exp | PF | Net pts | Net $ |
|---|---|---|---|---|---|---|
| 2021 | 12 | 50.0% | +17.48 | 1.41 | +209.7 | +$419 |
| 2022 | 37 | 78.4% | +64.15 | 3.06 | +2,373.4 | +$4,747 |
| 2023 | 32 | 59.4% | +23.94 | 1.78 | +766.1 | +$1,532 |
| 2024 | 21 | 61.9% | +35.47 | 2.15 | +744.9 | +$1,490 |
| 2025 | 19 | 42.1% | +51.56 | 1.62 | +979.7 | +$1,959 |
| 2026 | 16 | 75.0% | +107.28 | 4.52 | +1,716.4 | +$3,433 |

2025 is the honest soft spot: WR drops to 42.1% while expectancy holds, i.e. the year was carried by payoff asymmetry (the stop working), not by hit rate.

**Pre-2021, disclosed:** N=63, WR 44.4%, Exp −7.60, PF 0.74. Not part of the pass/fail evaluation, included because it is the strongest evidence *against* the strategy being a timeless law.

---

## 3. Gate ladder — `evidence/STRATEGY_REALITY_CHECKLIST.md`

Every ✅ below is tied to an artifact I re-ran or a file I read for this package. None are inferred. (A previous version of this table had two unearned passes, 3.6 and 4.2, inferred from "it would obviously be done that way." Both were wrong. Hence this note.)

| Gate | Verdict | Evidence |
|---|---|---|
| **0** — hypothesis pre-registered, not mined | ✅ PASS | Public ORB literature; registry `evidence/CLAUDE_HANDOFF_NQ_ERA_ROBUSTNESS_2026-07-18.md` |
| **1.1–1.5** — no lookahead | ✅ PASS | Overnight context fully known by 09:30; earliest entry 09:50; OR→breakout→retest strictly forward; entry at *next* bar's open; entry bar included in stop scan |
| **1.3** — same-bar ambiguity | ✅ PASS (structural) | **No target exists** → ordering question cannot arise |
| **1.6** — parameters not fitted to test | ❌ **FAIL** | `STOP_PCT` chosen on both splits, twice (v2 07-19, v3 07-29). "Test PF 2.42" is in-sample. Also uses a custom dev/test split, not canonical `SPLITS` |
| **2** — execution honesty | ✅ **PASS (strong)** | `source/orb_v3_surgical_execution_test.py` **re-run 2026-08-03**: 31 stops resolved on actual 1-min bars, tick-grid + gap-aware worse-of fills → total damage **−2.56 pts (0.04%)** over 134 trades. `n_gap_fill=0`, `n_stop_orig_would_miss=0` |
| **2.7** — external/broker-sim replica | ⬜ **NOT RUN** | No independent replica exists. Honest gap |
| **3.1** — placebo / random-entry control | ✅ PASS | 500 draws, exit shape + hour-of-day matched. ORB **+50.98 vs band [−15.76, +14.47]**, p=0.0000 |
| **3.2** — clean OOS | ❌ **FAIL** | Same cause as 1.6 |
| **3.6** — Monte Carlo equity path | ✅ **PASS — closed for this package** | `orb_v3_monte_carlo.py`, run 2026-08-03. See §5 |
| **3.7** — cross-instrument transfer | 🟡 PARTIAL | Ranks NQ 2.26 > MES 1.40 > MYM 1.16 > M2K 0.96 > GC 0.90. Degrades with distance from NQ — consistent with a real NQ-specific mechanism, not generic trend-fitting, but it is not a clean transfer |
| **4.2** — sizing off honest-fill logs | 🟡 PARTIAL | Portfolio sizing imports the base module, so it sizes off LEVEL-fill logs, not surgical honest-fill logs. Gap is only 0.04% (§Gate 2) but it is a real deviation from the rule. Standalone ORB v3 metrics in §2 are unaffected |
| **5.4** — independent adversarial audit | ✅ PASS | `evidence/ORB_GNG_PROMOTION_AUDIT_2026-08-02.md` — everything recomputed from source, found 3 material issues incl. the consumed test window |
| **5.5** — live-vs-assumed fill log | ⬜ **OUTSTANDING** | **This is the entire ask.** Cannot be closed without a forward test |

**Score: 8 pass, 2 fail, 2 partial, 2 not run.** The two failures share one root cause (stop retuned on test), and it is priced as a ~10–12% expectancy haircut rather than hidden.

---

## 4. Cost model

`COST = 1.25` pts round-turn = **$2.50** per round-turn on MNQ, against a realistic all-in of roughly **$1.50** (commission + fees + 1 tick of slippage). So the backtest is charged **~1.7× realistic cost**. Cost is **2.5%** of the $99.13 per-trade expectancy — the edge is not a cost-model artifact in either direction.

One tick of MNQ (0.25 pt = $0.50) is ~0.5% of expectancy, so per-fill tick error is not material at this expectancy. It would become material if expectancy halved.

---

## 5. Gate 3.6 — Monte Carlo, newly run

`orb_v3_monte_carlo.py` → `orb_v3_monte_carlo_results.json`. 10,000 paths, seed 20260803, resampling realised per-trade dollar P&L.

**DOLLAR mode**, because sizing is fixed at 1 contract — there is no equity-proportional sizing to compound, so percentage/NAV mode would be the wrong model.

**Declared capital base $25,000**, and it is *derived*, not a round number: MNQ intraday initial margin ≈ $2,500/contract, so $25k funds exactly 1 contract at a 10× margin buffer and covers the worst historical drawdown ($1,889) 13× over. Stated explicitly so every dollar figure has a declared denominator (this is the GitLab #2199 G7 ask, applied to ourselves).

| | iid | **block (size 11)** |
|---|---|---|
| net$ P5 | +$6,355 | **+$6,774** |
| net$ P50 | +$13,298 | **+$13,581** |
| net$ P95 | +$21,863 | **+$20,493** |
| **P(net < 0)** | 0.0800% | **0.0300%** |
| MaxDD$ P50 | −$1,463 | −$1,564 |
| MaxDD$ tail-P95 | −$2,606 | −$2,769 |
| MaxDD$ worst path | −$5,149 | −$5,557 |
| net$/MaxDD$ P5 | 2.897 | 2.818 |
| **Min equity P5** | $22,394 | **$22,231** |
| P(equity < 1-contract margin) | 0.0000% | **0.0000%** |

**Read the block column** — it preserves win/loss streaks and regime runs, which matters for a strategy whose own docstring calls its edge regime-dependent.

Per our MC approval rule (positive P5, min equity well clear of zero): **passes**. P5 net is positive at **+$6,774**, min equity P5 sits **$19.7k above** the 1-contract margin floor, and no path in 10,000 came within margin of ruin. This also independently reproduces the pre-registration's P(total<0) = 0.04% from a different code path.

**CAGR and Calmar are deliberately absent.** For a fixed-contract futures strategy they are pure functions of the nominated equity base — ORB v3 shows Calmar 1.368 / 1.394 / 1.441 at 1× / 1.35× / 2× contracts on a $100k base with *nothing about the edge changing*. Any approval gate keyed on them is gameable by declaring a smaller account. Futures metrics here are **net$, MaxDD$, net$/MaxDD$, expectancy in points**. This is the same argument filed as #2199 G6/G7.

---

## 6. Research already closed — so it isn't re-asked

| Question | Answer | Where |
|---|---|---|
| Loosen thresholds to raise N? | **Not supported.** Pre-registered, 36-cell grid; rule's pick was worse than production on every metric that matters | `evidence/GNG_THRESHOLD_LOOSENING_PREREG_2026-08-03.md` |
| Different timeframe than 5m? | All 1–30m alternatives to frozen 5m/mag-0.15% **rejected** | `project_orb_combined_tf_sweep.md` |
| OR-width regime filter? | Mid-tercile beats extremes but **breaks the no-losing-year property** → rejected, kept as a future option |  `project_orb_or_width_regime_filter.md` |
| Retune the stop again? | **No — the test window is already spent twice.** Any further stop work needs data we don't have | §0.1 |
| Pair it with gap-and-go? | GNG **frozen**, not in this package. On the 16 both-fire sessions the legs correlate **+0.965**; diversification came from non-overlap, not from the legs differing | `evidence/GNG_THRESHOLD_LOOSENING_PREREG_2026-08-03.md` |
| Does it transfer to other instruments? | Degrades with distance from NQ (Gate 3.7). Edge is NQ-specific | §3 |

---

## 7. Open findings we have not closed

1. **`END = 2026-07-16` is a live reproducibility hazard.** Two scripts disagree on the 2026 slice purely because one filters to that cutoff and one doesn't. Needs a single shared dated constant. Not fixed here because touching it changes frozen numbers mid-review — flagged for the follow-up.
2. **Gate 2.7** — no external broker-sim replica exists.
3. **Docstring drift** — `source/orb_combined_base.py` says `N=136`; actual frozen N is **134** and full-window N is **137**. The code is right, the comment is stale.
4. **CVaR has two valid bases ~2× apart** — per-calendar-session (−163.16 pts, 72-tail of 1,428) vs per-active-signal (13-tail of 137). Both reproduce; always label which. Not a defect, a labelling trap.

---

## 8. Why this cannot enter the scanner yet

ORB v3 is a 5-minute intraday futures strategy. The SignalDeck scanner is **equities-daily-only** — this is a platform gap, not a checklist gap, and it is filed in full as **[GitLab #2199](https://gitlab.com/zachisit/signaldeckapi/-/work_items/2199)** (G1 data / G2 execution / G3 calendar / G4 stops / G5 sizing / G6 benchmarks, plus addendum G7–G10).

The `"intraday": True` flag added to the #2130 runbook as C3 does **not** close this: it is a partial-bar opt-out on the *daily* path and explicitly does not add minute/hour bar fetching.

**Three F0 decisions are blocked on Zach** before any scanner code should be written: continuous-series strategy, intraday trigger model, futures Definition-of-Done metrics. See [`SIGNALDECK_INTEGRATION_HANDOFF.md`](SIGNALDECK_INTEGRATION_HANDOFF.md) for the MR blueprint that becomes writable once those land.

**Consequence for this package:** the forward test has to run **outside** the scanner. That is what is being asked for.

---

## 9. The ask

**Review ORB v3 for a micro-size forward test.** Explicitly **not** capital promotion — the audit verdict is *GO micro-forward-test, NO-GO capital*, and this package forwards that unchanged rather than arguing past it.

Conditions we are pre-committing to, not offering:

1. **1 contract, micro size only.** Declared capital base $25,000 (§5).
2. **Benchmark to the haircut** — +45.4 pts/trade, not +50.98.
3. **Kill criteria pre-registered before the first fill** — [`FORWARD_TEST_PREREG.md`](FORWARD_TEST_PREREG.md), written before any live trade so they cannot be renegotiated afterwards.
4. **Log every live fill against its assumed fill** (Gate 5.5). Divergence is a Gate 2 failure in production, not a rounding difference.
5. **Re-run monthly** to harvest the data overlay's free OOS, and fix the `END` constant first (§7.1).
6. **No parameter changes during the forward test.** If it underperforms, that is the measurement, not a tuning prompt. The test window is already spent twice.

---

## 10. Reproduction

See [`REPRODUCTION.md`](REPRODUCTION.md) for exact commands, expected output, and the data dependency each script needs.
