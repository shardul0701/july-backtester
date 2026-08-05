# ORB v3 — Forward-Test Pre-Registration

**Written 2026-08-03, before any live fill exists.** The point of writing it now is that these thresholds cannot be renegotiated once the P&L starts arriving. If a criterion below is hit and the response is to argue with the criterion, the argument is invalid by construction.

Status: **not started.** No live fill has occurred. This file is the pre-commitment, not a result.

---

## 1. What this forward test can and cannot establish

Being explicit about this, because it determines every threshold below.

ORB v3 produces **~25 signals per year** (137 signals over 5.5 years). So:

- A 3-month forward test yields ~6 signals. A 12-month test yields ~25.
- **This test cannot statistically validate the edge.** At n=25 and the historical win rate, the confidence interval on expectancy is far too wide to distinguish "+45 pts/trade" from "zero."
- **What it can establish, and is designed for, is Gate 5.5: execution fidelity.** Do real fills match assumed fills? That is measurable at small n, because it is a per-fill measurement, not a distributional one. It is also the only gate that cannot be closed any other way.
- Secondary purpose: **regime canary.** The strategy's own docstring says the edge is tied to the post-2020 structure and should be watched for a sign flip. Forward data is how that gets watched.

Anyone reading a 6-month forward result as edge confirmation is misreading it. Anyone reading a 6-month *fill divergence* as noise is also misreading it.

---

## 2. Configuration — frozen for the duration

| Item | Value |
|---|---|
| Instrument | MNQ, front month, roll-aware |
| Size | **1 contract.** No scaling, no pyramiding |
| Declared capital base | $25,000 |
| `MAG_TH` | 0.0015 |
| `STOP_PCT` | 0.01 |
| Entry | Next 5-min bar's open after retest-confirm |
| Exit | RTH close or 1.0% stop |
| Flat by close | **Mandatory.** No overnight carry, ever |
| Benchmark expectancy | **+45.4 pts/trade** (the haircut, not +50.98) |

**No parameter may change during the test.** The stop in particular has already been tuned against the test split twice; a third pass would be indefensible. If the strategy underperforms, that is the measurement.

---

## 3. Hard kill criteria — any one of these ends the test

| # | Criterion | Threshold | Rationale |
|---|---|---|---|
| **K1** | **Execution divergence** | Median absolute (live fill − assumed fill) > **1 tick (0.25 pt)** across ≥ 10 fills | Gate 2 held at −2.56 pts / 0.04% in backtest. If live fills systematically diverge by more than a tick, the honest-fill model is wrong and every historical number inherits the error. This is a **Gate 2 failure in production** |
| **K2** | **Any overnight carry** | 1 occurrence | A missed flat-by-close is a *capital* event on margin, not a P&L event. Immediate halt, root-cause before resuming |
| **K3** | **Drawdown breach** | Live drawdown worse than **−$2,769** | That is the MC block-bootstrap tail-P95 (§5 of the promotion package). Breaching it means the live path is in the worst 5% of 10,000 simulated paths |
| **K4** | **Expectancy, once powered** | After **≥ 30 signals**, realised expectancy < 0 | 30 is the earliest point at which a negative mean is weak evidence rather than no evidence. Deliberately not applied earlier |
| **K5** | **Regime sign flip** | Any trailing 12-month window net negative | The docstring's own stated canary for the regime ending |
| **K6** | **Stop-mechanic failure** | Any stop that should have triggered on 1-min data but did not fill, or filled beyond 2 ticks past the stop level | The backtest recorded `n_stop_orig_would_miss = 0`. A live miss invalidates the stop model |

**On K1 and K6 the response is investigation, not tuning.** Both indicate the *model of reality* is wrong, which parameter changes cannot fix.

---

## 4. Pause-and-review triggers — not kills

| # | Trigger | Action |
|---|---|---|
| P1 | 4 consecutive losing signals | Review fills for a common execution cause. Resume if fills are clean (historical max losing streak makes this unremarkable on its own) |
| P2 | Realised expectancy < +22.7 pts (half the haircut benchmark) after ≥ 15 signals | Written interim assessment; no size change |
| P3 | Signal frequency < 15/yr annualised | Investigate data/roll/session handling — a signal drought is more likely a plumbing bug than a market change |
| P4 | Data-overlay gap or roll artifact detected | Halt until data is verified; roll gaps manufacture false ORB breakouts (~4×/yr if unhandled) |

---

## 5. Success definition — what "continue" looks like

At the **12-month or 30-signal mark, whichever is later**:

1. **K1–K6 all clear** — this is the primary bar.
2. Median absolute fill divergence ≤ 1 tick, with a full per-fill log (Gate 5.5 artifact).
3. Realised expectancy ≥ **+22.7 pts** (half the haircut benchmark). Deliberately lenient: at n=25–30 the CI is wide, and demanding the full +45.4 would be pretending to power the test doesn't have.
4. No overnight carry, no stop-mechanic failure.

**Meeting all four earns exactly one thing: a review for larger size.** It does not earn capital promotion automatically, and it does not retire the consumed-test-window finding — that only clears with genuinely fresh data accumulated over time.

**Failing on expectancy while passing K1–K6 is still a useful result:** it means execution is honest and the edge is soft, which points at regime rather than artifact. That distinction is worth the test on its own.

---

## 6. Logging requirements — Gate 5.5 artifact

Every signal, whether filled or not:

| Field | Why |
|---|---|
| `session`, `signal_ts`, `direction` | Reconciliation key |
| `assumed_entry` / `live_entry` / `entry_divergence_ticks` | K1 |
| `assumed_exit` / `live_exit` / `exit_divergence_ticks` | K1 |
| `stop_level`, `stop_hit` (bool), `stop_fill`, `stop_slip_ticks` | K6 |
| `exit_reason` (`rth_close` \| `stop`) | Mechanic audit |
| `flat_by_close` (bool) | K2 |
| `pnl_points`, `pnl_usd`, `commission_fees_usd` | Cost-model validation |
| `skipped_reason` (nullable) | **A signal that did not get taken is data.** Silent omission is how forward tests flatter themselves |

That last row is not boilerplate. A "0 signals / no information" cell is a claim like any other and needs the same scrutiny as a positive result — the prior audit of this very strategy reported "ORB fired 0 times" in the fresh window when it had actually fired 3 times, and that error survived a full audit pass.

---

## 7. Sign-off

| | |
|---|---|
| Pre-registered | 2026-08-03 |
| First live fill | *not yet occurred* |
| Amendments | **None permitted** to §3 once the first fill exists. §4 may be extended, never loosened |
