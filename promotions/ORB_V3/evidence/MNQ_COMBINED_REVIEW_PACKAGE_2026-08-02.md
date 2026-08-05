# MNQ Combined Strategy — Review Package

**Date:** 2026-08-02 · **From:** Shardul · **For:** Zach (review)
**Strategy:** ORB v3 + Gap-and-Go, combined, MNQ only
**Ask:** review for **micro-size forward test**. This is explicitly **not** a capital-promotion request.

---

## 0. Read this first — what we are and aren't claiming

An independent Gate 0–5 audit (`ORB_GNG_PROMOTION_AUDIT_2026-08-02.md`) returned
**GO for micro-size forward test, NO-GO for capital promotion.** We are forwarding that verdict
unchanged rather than arguing past it.

The strategy passes the gates that killed everything else in this program — causality, execution
honesty, and placebo. What it has **not** got is an untouched sample to be judged on, because the
2024–2026 test window was consumed by parameter selection (§5, Finding 1). Forward testing is the
instrument that fixes that, which is precisely what we're asking for.

Three things below are deliberately unflattering: the consumed test window, 11 sessions of fresh
OOS data that came back negative, and a cross-instrument transfer test that failed. All are stated
in full. Nothing has been re-fitted to repair them.

**Known gaps in the gate ladder, stated up front rather than buried:** Gate 3.6 (Monte Carlo
equity path) has **not been run**; Gate 4.2 is **partially** met (sizing runs off base-module
level-fill logs, not the surgical honest-fill logs — a 0.04% difference, but a deviation);
Gate 2.7 (external replica) has no independent engine implementation yet. See §3.

**One correction was made to this package after it was first written, and it made the numbers
worse.** The source audit reported ORB as firing **0** times in the fresh OOS window. Rebuilding
the trade list from source for the tearsheet showed it fired **3** times; ORB self-updates with
the daily data overlay and had not been re-run. Corrected throughout: the fresh window is
**5 active sessions and −$1,636.41**, not 2 and −$1,554, and the headline including it is
**19.35% / CAGR 3.23% / Calmar 1.422**, not 19.44% / 3.24% / 1.426. §5 Finding 2 carries the
detail. Flagging it here because a reviewer should know which figures moved and why — and because
it removes the convenient reading that only the weaker leg was tested.

---

## 1. Strategy specification (frozen)

Two independent intraday legs on MNQ, both flat overnight, both behind hard stops, no targets.

### Leg A — ORB v3 (`orb_combined_base.py`, locked 2026-07-29)

| item | value |
|---|---|
| bars | 5-minute, RTH (09:30–16:00 ET) |
| opening range | first 3 bars, 09:30–09:45 |
| context filter | RTH open on the same side of **both** midnight and 08:30 ET closes, by ≥ `MAG_TH` |
| `MAG_TH` | **0.15%** of opening price (scale-invariant, not an absolute point threshold) |
| breakout | first 5m **close** beyond OR high/low, scanned from bar 3 |
| entry trigger | price must **retest** the OR level, then close beyond it again |
| entry fill | **next bar's open** (earliest possible 09:50) |
| stop | **1.0%** from entry, fixed at entry, **never moves** |
| exit | stop, else last RTH bar close |
| direction | **long and short** |
| cost | 1.25 pts round-trip ($2.50) |

### Leg B — Gap-and-Go (`gap_and_go_base.py`, frozen)

| item | value |
|---|---|
| bars | 5-minute, RTH |
| gap | prior RTH close → today's open **> +0.30%** (`shift(1)`, causal) |
| confirmation | first 5m bar extends in the gap direction by **≥ 0.10%** |
| entry fill | **bar-1 open** |
| stop | **1.5%** from entry, fixed |
| exit | stop, else last RTH bar close |
| direction | **long only** |
| cost | 1.25 pts round-trip ($2.50) |

**Neither leg has a profit target.** That is the structural reason this pair is immune to the
same-bar target/stop ambiguity (Gate 1.3) that invalidated three other strategies in this program.

---

## 2. Headline numbers

Window 2021-01-01 → 2026-07-16, 1,428 RTH sessions, 1 contract, $100k nominal.
Independently recomputed from `daily_overlap_rule_pnl.csv` — every figure reconciles exactly.

| metric | as frozen | **incl. 11 fresh OOS sessions** |
|---|---:|---:|
| Total return | 20.99% | **19.35%** |
| CAGR | 3.52% | **3.23%** |
| Max drawdown | −2.27% | −2.27% |
| Calmar | 1.550 | **1.422** |
| Active sessions | 244 | **249** |
| Win rate | 63.5% | **63.1%** |
| Profit factor | 1.866 | **1.735** |
| Expectancy / active session | $86.03 | **$77.73** |

**Use the right-hand column.** The left column stops at the frozen cutoff; the right includes 11
sessions nobody had looked at (§5, Finding 2).

> **Corrected 2026-08-02.** An earlier draft of this table showed 246 signals / 19.44% / CAGR
> 3.24% / Calmar 1.426 in the right-hand column. That inherited an error in the source audit,
> which reported ORB as having fired **0** times in the fresh window; it actually fired **3**
> times. ORB self-updates with the daily overlay and had simply not been re-run. Corrected
> figures above; details in §5 Finding 2. Both columns are now rebuilt from source, and the
> frozen column reproduces the audit exactly (244 sessions / 10,495.07 pts / 20.99% / WR 63.5% /
> PF 1.866 / max DD 1,167.09 pts = $2,334.18).
>
> Every figure here is computed on the **tearsheet's own basis** (`trade_analyzer.calculations`,
> peak-relative drawdown, equity series starting one day before the first trade), so this table
> and the accompanying PDF print identical numbers. Frozen CAGR/Calmar of 3.52% / 1.550 agree
> with the audit's 3.51% / 1.543 to within rounding. A brief intermediate draft of this
> correction quoted 3.50% / 1.500 / −2.33%, which used initial-capital-relative rather than
> peak-relative drawdown and was wrong — −2.27% is the standard definition and is what stands.

Rows are **active sessions**, not individual leg trades: 134 ORB + 126 GNG = 260 trades falling
on 244 distinct frozen sessions (16 overlap days carry both legs). Every per-session figure in
this package uses that basis, and so does the tearsheet, so the two agree line-for-line.

**Read the $100k as a denominator, not an allocation — and read the Sharpe accordingly.** All
returns here are 1 contract per leg, unscaled, divided by a nominal $100k. Initial margin on one
MNQ is roughly $2–3k, so this is a deliberately, massively under-levered presentation chosen to
make the P&L comparable across our other sleeves, not a proposed capital plan. Two consequences
visible on the tearsheet: the **annualised Sharpe prints −1.3**, because a 3.23% return is being
measured against a 5% risk-free rate — that is a statement about the size of the denominator, not
about the edge; and **SPY buy-and-hold returns 99.98%** over the same window against this
strategy's 19.35%, for the same reason. The per-trade Sharpe (0.19) and Profit Factor (1.73) are
the scale-free figures. If sizing is the question, Gate 4.1 (§3) is where the realized-loss ÷
sized-risk work sits.

### Per-leg and combined

| series | sessions | active | total pts | Sharpe (ann) | max DD pts | Calmar |
|---|---:|---:|---:|---:|---:|---:|
| ORB v3 | 1,428 | 134 | 6,831.31 | 1.19 | 944.29 | 7.234 |
| Gap-and-go | 1,428 | 126 | 3,663.76 | 0.80 | 1,387.48 | 2.641 |
| **Combined** | 1,428 | 244 | **10,495.07** | **1.40** | 1,167.09 | **8.993** |

ORB N=134, WR 63.4%, Exp +50.98 pts, PF 2.340.
GNG (ORB window) N=126, WR 64.3%, Exp +29.08 pts, PF 1.520.

**Combination verdict: "combine with caveats."** Combined Sharpe (1.40) beats both standalone legs,
but max DD (1,167 pts) is worse than ORB alone (944). It improves risk-adjusted return; it does not
dominate on every axis. Treat it as an additive edge stack, not proof the legs are independent.

**Overlap:** only 16 of 1,428 sessions fire both legs (1.12%). Daily correlation **r = +0.043** —
the diversification is genuine. But those 16 days are 12.7% of GNG's trade days, and 16/16 agree in
direction. Small-N; supports not cutting exposure, does not support escalating it. Concurrency is
capped at **2 full-risk legs**.

⚠️ **Benchmark against +45 pts/trade for ORB, not +50.98.** See §5 Finding 1.

---

## 3. Gate compliance — `STRATEGY_REALITY_CHECKLIST.md`

Every item recomputed from source by an independent pass, not read off prior reports.

### Gate 0 — Data integrity: **PASS**

| # | check | status | evidence |
|---|---|---|---|
| 0.1 | Roll sanity | ✅ | `NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md` PASS; 16yr stitch clean |
| 0.2 | Back-adjustment | ✅ | documented; `close_unadj` carried through |
| 0.3 | Derived-TF freshness | ✅ | 5m bars built from current clean master via `build_bars5` |
| 0.4 | Split/dividend | n/a | futures |
| 0.5 | Bad-print / stitch seam | ✅ | frozen→overlay seam checked directly: 28,710.25 → 28,589.25 = **−0.42%**, plausible overnight move, no scaling break. 8 Databento-degraded sessions **excluded** via `GAP_SESSIONS`, not trusted |
| 0.6 | Survivorship | n/a | single instrument |

### Gate 1 — Causality: **PASS on mechanics, FAIL on selection**

| # | check | status | evidence |
|---|---|---|---|
| 1.1 | Decision-time audit | ✅ | overnight context reads midnight / 08:30 / 09:30 open; session tags roll at 18:00 so all three belong to the trade date and are known by 09:30. GNG uses `shift(1)` |
| 1.2 | Bar-shift test | ✅ | entry is already at *next bar open*, not signal-bar close |
| 1.3 | Same-bar ambiguity | **n/a** | **neither leg has a target** — structurally cannot hit this bug |
| 1.4 | Entry-bar inclusion | ✅ | stop scan starts at `k=entry_i` (ORB) and `i=1` (GNG) |
| 1.5 | Pattern lookahead | ✅ | OR from bars [0:3]; breakout scanned from i=3; retest from `breakout_i+1`; entry `j+1` open. Strictly forward |
| 1.6 | **Selection lookahead** | ❌ **FAIL** | **see §5 Finding 1 — test window consumed** |

### Gate 2 — Execution honesty: **PASS (strong)**

The stored Gate-2 CSV rows were byte-identical to baseline — the exact signature of a no-op audit
path. We did not trust it; we **re-ran `orb_v3_surgical_execution_test.py` directly**:

```
counters: {n_gap_fill: 0, gap_slip_pts: 0.0, n_stop: 31}
trades with changed P&L: 19 / 134
total delta (surgical − original): −2.56 pts    (6831 → 6829)
```

| # | check | status | evidence |
|---|---|---|---|
| 2.1 | No protection-free windows | ✅ | stop set at entry, **never moves** — no arming, ratchet, or move-to-BE state exists |
| 2.2 | Gap-aware fills | ✅ | worse-of(level, breaching minute's open) on real 1-min bars; **total damage −2.56 pts = 0.04% of P&L** |
| 2.3 | Implementable-variant convergence | ✅ | 31 stops resolved on actual 1-min bars; GNG v0/v2/v3 identical, `n_gap_through=0` |
| 2.4 | Tick-grid levels | ✅ | verified in surgical variant |
| 2.5 | Production costs | ✅ | 1.25 pts RT = $2.50 vs realistic ~$1.00 commission + 0.25pt spread → **conservative**. Holds at 3× in battery, 5× at portfolio level |
| 2.6 | Session mechanics | ✅ | flat overnight; no maintenance-break or roll-day fills |
| 2.7 | External replica | ⚠️ | not run — no independent engine implementation exists yet |

Post-freeze fill arithmetic verified by hand: entry 28,175.50 − fill 27,752.87 − 1.25 = **−423.88** ✓

⚠️ `n_gap_through = 0` is a **window-specific observation, not a structural guarantee.** Gapped stop
fills remain possible live. This is one of the things the forward test is for.

### Gate 3 — Statistical robustness: **PASS on what was run; 3.6 not run, 3.2 fails**

| # | check | status | evidence |
|---|---|---|---|
| 3.1 | **Placebo** | ✅ **strongest evidence** | 500 draws, matched exit shape + hour-of-day. ORB +50.98 vs band [−15.76, +14.47]; GNG +22.33 vs [−14.10, +15.07]. **Both p = 0.0000** |
| 3.2 | Matched-pair dev/replay | ❌ | window consumed — §5 Finding 1 |
| 3.3 | Parameter perturbation | ⚠️ | ORB: all 6 variants clear zero, but chosen stop **is the grid argmax** and 0.75% is 32% worse — a real slope. GNG: all 9 clear zero and chosen stop is **not** the argmax (1.0% scores better) — good non-fitting sign |
| 3.4 | Era robustness | ⚠️ | **ORB is explicitly regime-dependent** (pre-2021 PF 0.93) — declared up front, not discovered. **GNG has 4 losing years of 17** (2012, 2014, 2016, 2025 at −488.63) |
| 3.5 | Honest t-stats | ✅ | day-clustered bootstrap used throughout |
| 3.6 | Monte Carlo | ⬜ **NOT RUN** | no MC/bootstrap equity-path study exists for either leg. Day-clustered bootstrap CIs were run on expectancy (3.5), but that is not the same thing as an MC P5 equity path. **Open gap — flagging rather than claiming.** Would be dollar-mode (fixed 1-contract sizing) |
| 3.7 | **Sample floors & transfer** | ⚠️ **mixed** | **see §5 Finding 3 — cross-instrument transfer** |

### Gate 4 — Sizing & risk realism: **PASS, one partial**

| # | check | status | evidence |
|---|---|---|---|
| 4.1 | Realized-loss ÷ sized-risk | ✅ **≈1.0×** | worst post-freeze loss 422.63 pts vs intended 1.5% = 422.6. (Threshold is 2×) |
| 4.2 | Sizing on honest fills | ⚠️ **partial** | `portfolio_overlap_sizing.py` imports `orb_combined_base` / `gap_and_go_execution_audit` — i.e. it sizes off **base-module (level-fill) logs**, not the surgical honest-fill logs. Measured gap between the two is **−2.56 pts = 0.04% of P&L**, so immaterial here — but it is a technical deviation from the rule and we're stating it rather than rounding up |
| 4.3 | Concurrency caps | ✅ | bounded at 2 legs |
| 4.4 | Unbounded-risk states | ✅ **none** | both legs flat overnight behind hard stops |

### Gate 5 — Process

| # | check | status | evidence |
|---|---|---|---|
| 5.1 | Checkpoint each phase | ✅ | |
| 5.2 | Reconciliation ≠ validation | ✅ | gates re-derived independently, not inherited |
| 5.3 | Surgical variants | ✅ | one assumption at a time vs validated-identical baseline |
| 5.4 | **Adversarial audit** | ✅ | independent Gate 0–5 pass, 2026-08-02, this checklist as script |
| 5.5 | **Live micro-size forward test** | ⬜ **OUTSTANDING** | **this is what we're asking you to review for** |

---

## 4. Research already closed (so you don't re-ask)

Every one of these was pre-registered before data was touched, and every one came back negative.
None were re-fitted afterward.

| question | answer | detail |
|---|---|---|
| Add a short side to GNG? | **DEAD** | val N=79 **PF 1.06**, Exp +3.91; 6/13 qualifying years positive (46%). Failed 3 of 4 criteria |
| Gate GNG to uptrends only? | **DEAD — and inverted** | downtrend bucket is *better* in both splits (val long: uptrend +20.02 vs downtrend +87.70). We did **not** invert and gate to downtrend — N=14/23, data-discovered. Probable real driver is **volatility, not trend** |
| Is the GNG stop earning money? | **No — it's a variance reducer** | net effect **−104.4 pts**. Hurts trend years (2026 +1,074 w/ stop vs +1,942 without), rescues bad ones (2025 −489 vs −1,116). Kept for tail control, not profit |
| Does GNG's worst case wipe the account? | **No** | worst session −440.05 pts = −$880 = **0.88% of $100k**. P99 losing trade $855 = 0.86% |
| Add a gap-cap filter? | **Rejected as overfit** | already tested and rejected; re-adding now would be fitting to 2 trades |
| ORB "slow retest" filter? | **Adopted then DEMOTED** | broke on out-of-sample test (overlapping CIs) |
| Closing-auction continuation? | **Killed by placebo** | dev expectancy already −7.81; real result sits inside both placebo bands (p=0.94, p=0.88) |

---

## 5. Open findings — the three things to push on

### Finding 1 — The test window is consumed (Gate 1.6 / 3.2 FAIL)

`orb_combined_base.py` v3 docstring: *"1.0% is the tightest level that still improves PF on **both
splits simultaneously**."* That selects a parameter using test-split performance — and it happened
twice on the same 2024–2026 slice (v2 → 1.5% on 07-19, v3 → 1.0% on 07-29).

**"Test PF 2.42" is an in-sample number.** We are not presenting it as OOS evidence.

Sized from the fragility grid (a slope, not a cliff — all variants clear zero): chosen stop Exp
+50.98 vs grid mean +45.4; chosen mag +50.98 vs grid mean +45.8. → **expect a 10–12% expectancy
haircut. Benchmark to ~+45 pts/trade.**

ORB also uses its own dev/test split (2021-23 / 2024-26) rather than the canonical `SPLITS`.
Justified on regime grounds, but it is split-shopping in form.

### Finding 2 — Fresh OOS data exists, nobody had looked, and it's negative

The frozen window ends 2026-07-16; the daily overlay has since extended data to **2026-07-31** —
**11 genuinely untouched sessions.**

| leg | signals | result |
|---|---:|---|
| ORB v3 | **3** | 2 winners, 1 stopped — **net −41.07 pts = −$82.14** (roughly flat) |
| Gap-and-go | **2** | **both losers, −777.13 pts = −$1,554.26** |
| **combined** | **5 active sessions** | **−818.20 pts = −$1,636.41** |

| session | leg | dir | pts | exit |
|---|---|---|---:|---|
| 2026-07-20 | GNG | Long | −353.25 | Session Close |
| 2026-07-23 | ORB | Short | +14.25 | Session Close |
| 2026-07-28 | ORB | Short | −273.32 | Stop Loss |
| 2026-07-30 | ORB | Long | +218.00 | Session Close |
| 2026-07-31 | GNG | Long | −423.88 | Stop Loss |

That is **7.8% of the combined strategy's entire 2021–2026 profit, given back in 11 sessions.**
All five fills verified honest — the ORB fills reconcile exactly to
`(exit − entry) × dir − 1.25`, and both GNG fills were checked for gap slippage and had **zero**.
Real results, not artifacts. Max drawdown is unchanged: these losses gave back a recent peak
rather than carving a new low.

> **Corrected 2026-08-02.** The source audit — and the first draft of this package — reported
> **ORB = 0 signals** here. Wrong. `orb_combined_base.py` reads through `_read_stitched()`, which
> appends the daily overlay, so ORB self-updates with the data; it had simply not been re-run
> before the table was written. A fresh run returns **137** trades from 2021, not 134.

Context: NQ fell 8.4% over that window (29,279 → 26,829) and **GNG is long-only.** This is its known
structural weakness and is directionally consistent with 2025 being its losing year.

**What the correction changes in the argument.** With "ORB = 0" the reading was that the fresh
loss was *entirely* GNG's known long-only weakness while ORB sat silent and untested. That is no
longer the story. ORB traded and also gave back ground: 3 signals for −41.07 pts against a
benchmark expectancy of roughly +45 pts/trade (Finding 1's haircut figure), about **176 pts below
expectation**. In ORB's favour it went 2-for-3 on direction and its one loss was a clean stop —
flat-to-slightly-soft, not a breakdown, and N=3 carries almost no weight. But the honest summary
is **both legs underperformed in the only untouched window**, not *one was tested and the other
wasn't*. ORB is also no longer "zero OOS evidence"; it has 3 observations and they are mildly
negative. Neither N=2 nor N=3 is decisive — but both point the same way, and that is precisely
what the forward test is for.

**Do not "fix" this.** Both GNG losses had unusually large gaps (0.99%, 1.25%) and the gap-cap
filter that would have caught them was already tested and **rejected as overfit**. Same for ORB's
07-28 stop. Adding either now is fitting to a handful of trades.

### Finding 3 — Cross-instrument transfer failed (new, 2026-08-02)

Pre-registered, then the frozen GNG config was run **completely unchanged** on four other
instruments. Because the config was frozen on MNQ first, every bar is OOS by construction.

| instrument | window | N | WR | PF | result |
|---|---|---:|---:|---:|---|
| MES | 2021-02→2026-02 | 72 | 55.6% | **1.03** | FAIL |
| M2K | 2024-08→2026-07 | 57 | 50.9% | 1.27 | FAIL (WR only) |
| MYM | 2024-08→2026-07 | 36 | 44.4% | 0.59 | FAIL |
| GC | 2010-06→2026-07 | 98 | 45.9% | 0.77 | FAIL |

Bar was pre-registered as N≥25, PF≥1.20, WR≥55%, Exp>0. **Verdict: NO TRANSFER, 0 of 3 equity
instruments.**

**GC was an inverted falsification check** — a GC *pass* would have meant we were fitting "any
trending market." It fails, so the edge is **NQ-specific, not generic trend-fitting.** That is the
one reassuring part.

Two honest caveats: **M2K failed on one criterion only** (WR 50.9% vs the 55% bar) while passing PF
1.27 and Exp +2.51 — under a PF-only rule the headline would read WEAK TRANSFER. We are keeping the
pre-registered label. And the **0.30% gap threshold is held fixed** while MNQ is more volatile than
MES/MYM, so "no transfer" is partly confounded with "different event per instrument." Vol-scaling is
a future pre-registered test, not a rescue.

**For contrast, ORB v3 did partially transfer:** NQ 2.26 > MES 1.40 > MYM 1.16 > M2K 0.96 > GC 0.90
— a monotonic gradient tracking tech/momentum concentration. **ORB is the better-evidenced leg.**

### Minor / housekeeping

- `orb_combined_base.py` docstring says N=136; **actual is N=134**.
- `END = 2026-07-16` is a **reproducibility hazard**: `portfolio_overlap_sizing.py` filters GNG to
  that date, `survivors_gate23_battery.py` does not — they disagree on 2026 (1,074 vs 1,851 pts)
  purely from the cutoff. Needs one shared dated constant. **Being fixed.**
- CVaR has two valid bases ~2× apart: **−163.16 pts** = per-calendar-session (72-tail of 1,428);
  **−$653.64** = per-active-signal (13-tail of 244, ×$2). Both reproduce exactly — label the basis.
- The "era-robustness" claim is **too strong for GNG** (4 losing years). ORB earns it; GNG doesn't.

---

## 6. What we're asking for

**Review for a micro-size forward test**, on these conditions — all five carried over from the audit:

1. **Trade both legs, size them differently.** ORB carries the evidence. **Gap-and-go goes on
   probation** at reduced or paper size — losing 2025, 0-for-2 on fresh OOS, long-only into a
   downtrend, and no cross-instrument support.
2. **Benchmark to the haircut**, ~+45 pts/trade for ORB, not +50.98.
3. **Pre-register kill criteria before the first fill**, so they can't be renegotiated later.
   Proposed: ORB expectancy < 0 over 30+ signals, or GNG down another 500 pts → halt that leg.
4. **Fix the `END` constant and re-run monthly.** The overlay produces free OOS data continuously;
   right now it's being silently truncated.
5. **Log every live fill against the backtest's assumed fill** (Gate 5.5). Fill divergence is a
   Gate-2 failure in production and is the single most informative thing the forward test produces.

**Gaps we'll close in parallel, and would welcome your steer on priority:**

- **Gate 3.6 — run the Monte Carlo** (dollar-mode, block bootstrap given declared regime
  clustering; MC-approved = P5 equity path clean). Not run today.
- **Gate 4.2 — re-run sizing off the surgical honest-fill logs** rather than base-module logs.
  Expected to change little (0.04%), but it should be done properly rather than argued away.
- **Gate 2.7 — an external replica** (broker sim or independent engine) would be the strongest
  remaining pre-live check. We don't have one; if you'd rather see that before any fills, say so.

**Not asking for capital.** The 2024–2026 numbers cannot serve as promotion evidence — they were
selected on. Promotion should wait for the forward test to generate its own untouched sample.

---

## 7. Reproduction

All under `C:\Users\shard\Quant\Advance Projects\BO FVG\research\`.

| artifact | file |
|---|---|
| ORB v3 frozen base | `orb_combined_base.py` |
| Gap-and-go frozen base | `gap_and_go_base.py` |
| Gate 2 surgical harness | `orb_v3_surgical_execution_test.py` |
| Gate 3 placebo battery | `survivors_gate23_battery.py` |
| Portfolio combination | `portfolio_integration/portfolio_integration.py` → `report.md` |
| Independent audit | `ORB_GNG_PROMOTION_AUDIT_2026-08-02.md` |
| H1/H2/H3 pre-registration | `GNG_RESEARCH_PROTOCOL_2026-08-02.md` |
| Cross-instrument pre-registration | `GNG_CROSS_INSTRUMENT_PROTOCOL_2026-08-02.md` |
| Cross-instrument results | `GNG_CROSS_INSTRUMENT_RESULTS.md` |
| Gate checklist (the script) | `STRATEGY_REALITY_CHECKLIST.md` |
| **Tearsheet (primary, incl. fresh OOS)** | `tearsheets/MNQ_Combined_ORBv3_GapAndGo/…pdf` |
| **Tearsheet (frozen window, reconciles to the audit)** | `tearsheets/MNQ_Combined_ORBv3_GapAndGo_FROZEN_to_2026-07-16/…pdf` |

Data: MNQ/NQ 1-min stitched 2010-06→present, frozen parquet + daily overlay. Stitch audit
`NQ_MNQ_STITCH_AUDIT_REPORT_2026-07-18.md` (PASS, 8 known Databento source gaps excluded).

**Tearsheets.** Both are the 2-page v3 institutional layout from july-backtester issue #248 /
PR #249, generated with `report.py --layout v3 --equity 100000` (PR #249 is not yet on `main`,
so it needs that branch). Input is a **day-level active-session CSV** — one row per session,
overlap days summed — rebuilt from the two frozen base modules; the builder is
`build_mnq_combined_csv.py`. Every figure on the primary sheet matches §2's right-hand column
(net $19,353.73 / 19.35% / 249 sessions / PF 1.73 / WR 63.05% / CAGR 3.23% / max DD −2.27% /
Calmar 1.42 / expectancy $77.73).

Two input quirks worth knowing if you regenerate these, both of which silently produce
*plausible-looking wrong numbers* rather than errors:

- `EntryDate`/`ExitDate` must be **date-only**. `data_handler.calculate_daily_returns` reindexes
  the equity curve onto a midnight `freq='D'` range, so timestamps carrying an intraday time
  match nothing, the curve flatlines at `initial_equity`, and the sheet prints **max drawdown
  0.0% with Calmar `inf`**. The first build of this tearsheet did exactly that.
- `EntryPrice`/`ExitPrice` are **omitted**, not blanked. A single entry price is meaningless on
  the 16 two-leg sessions. Blanking the column made Annual Turnover print a misleading `0.0`;
  omitting it prints an honest `N/A`.
