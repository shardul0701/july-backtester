# STRATEGY REALITY CHECKLIST — mandatory gates before calling ANY strategy real

**Created 2026-07-31**, the day the Sleeve A Vol-Squeeze edge (PF 3.74–7.87, 16 years, byte-exact reconciled, multiply audited) was proven to be 100% execution artifact. Every check below exists because something in this research program actually failed it. The receipt is cited each time.

**Rule: a strategy is "real" only when it passes ALL gates. A strategy that passes 9/10 is not 90% real — it is unproven.** Order matters: run them in sequence; a failure at any gate stops the line.

---

## GATE 0 — Data integrity (before any backtest is even meaningful)

- [ ] **0.1 Continuous-futures roll sanity**: ~4 rolls/yr, forward-only, no flip-backs; spread symbols excluded from dedup. *(Receipt: 11,430 barcode rolls in MNQ injected thousands of fake ±1% jumps — old BO FVG pipeline, 2026-07-14.)*
- [ ] **0.2 Back-adjustment method verified**: panama/difference with anchor documented; unadjusted series inflates futures long returns ~1%/quarter in hike regimes.
- [ ] **0.3 Derived-timeframe freshness**: every resampled file regenerated from the CURRENT clean master. *(Receipt: stale MNQ timeframe files off by up to 25,255 pts.)*
- [ ] **0.4 Split/dividend consistency (equities)**: adjustment state identical across all data paths. *(Receipt: GC exit-overlay split bug — data_cache held split-UNadjusted AAPL/TSLA/NVDA…, reversed a PR's headline result.)*
- [ ] **0.5 Bad-print / spike audit** run and archived; stitch boundaries continuity-checked; gaps documented as real-vs-synthetic.
- [ ] **0.6 Survivorship-free**: PIT universes only; report the survivorship premium separately. *(Standing rule.)*

## GATE 1 — Causality (lookahead)

- [ ] **1.1 Decision-time audit**: every input to a bar-N decision must be knowable at bar N's decision moment. `shift=1` in all regime work — never shift=0.
- [ ] **1.2 Bar-shift test**: shift entries +1 bar; a real edge degrades gracefully, never flips sign. *(Used to confirm the vol-squeeze state; killed HMM regime model — its "edge" was a lookahead artifact.)*
- [ ] **1.3 Same-bar target/stop ambiguity**: any bar containing BOTH levels must be resolved with finer data or conservatively stop-first — never by code order. *(Receipt: composite v1's "CONFIRMED" PF 1.43 was actually 0.96–1.11 once resolved honestly.)*
- [ ] **1.4 Entry-bar inclusion**: exit checks must evaluate the entry bar itself. *(Receipt: engine entry-bar omission, v1.11.0 port bug ledger.)*
- [ ] **1.5 Pattern-detection lookahead**: swing/pivot/divergence features must use only completed structure. *(Receipt: double-bottom + RSI-confluence lookahead bugs, both fixed 2026-07-23; RSI sweep never rerun — flagged.)*
- [ ] **1.6 Selection lookahead**: universe/pair/parameter selection must be walk-forward, not full-history. *(Receipts: pairs stat-arb selection lookahead; context-ORB "holdout" 76% pre-peeked; Sleeve A MINI chosen on full history.)*

## GATE 2 — EXECUTION HONESTY ⟵ the gate that killed Sleeve A

*Causal ≠ executable. For every exit event ask two questions: (a) could this order have physically existed at that moment, and (b) where was price when it was placed?*

- [ ] **2.1 No protection-free windows**: enumerate every state transition of the exit mechanic (arm, ratchet, move-to-BE). For each transition that happens MID-BAR, the post-transition order must be enforced for the remainder of that bar at 1-min resolution. *(Receipt: Sleeve A arming-bar free pass — 77–90% of armed trades retraced inside the unprotected window; enforcement collapses PF 7.87 → ~1.0.)*
- [ ] **2.2 Gap-aware fills everywhere**: every stop-type fill = worse-of(theoretical level, breaching minute's open). Rerun the FULL backtest with this alone changed. **If the edge materially degrades, the profit was fantasy fills.** *(Receipt: Sleeve A NAKED variant — gap slippage 18,098/21,451 pts ≈ 100% of 16-year profit; 51–62% of trades had a gapped fill; CPI-day trade booked at BE was really -382 pts = 12.7× sized risk.)*
- [ ] **2.3 Implementable-variant convergence** (the surgical harness, `composite/v2/sleeve_a_surgical_execution_test.py` pattern): implement the mechanic 2–3 legitimate live ways (instant stop-move; bar-close-only management; reference-replica with honest fills). **If the variants diverge wildly from the backtest, the backtest monetizes an artifact. If they converge on it, the edge survives execution.** All variants must stay profitable.
- [ ] **2.4 Tick-grid levels**: all order levels rounded to the instrument's tick (convention: stops toward loss, targets away).
- [ ] **2.5 Production costs as headline**: realistic commission + slippage is THE number; paper/reference costs only as a labeled signal-quality benchmark. Stress ×10 commission and +ticks slippage.
- [ ] **2.6 Session mechanics**: fills across maintenance breaks, weekend gaps, halts, roll days modeled at the reopen, not the level.
- [ ] **2.7 External-replica cross-check**: if an independent implementation (TradingView, broker sim, another engine) shows materially worse results — treat it as a red flag FIRST, an artifact second. *(Receipt: the Pine port's ~breakeven Strategy Tester result was the truth; we explained it away.)*

## GATE 3 — Statistical robustness

- [ ] **3.1 Placebo with the SAME exit shape**: random entries + identical exits. Judge PF-over-placebo, never raw hit rate (convex exits inflate HR on their own). **Blind spot: placebo shares execution assumptions — it cannot detect Gate-2 failures. Run Gate 2 first.** *(Receipt: Sleeve A placebo HR 68–83% on random entries.)*
- [ ] **3.2 Matched-pair dev/replay split**: both periods must independently confirm any change. Track **replay-window consumption** — count decisions the replay era has already adjudicated; a replay window reused across many design choices is no longer OOS.
- [ ] **3.3 Parameter perturbation**: no cliffs across ±steps; AND check the **artifact-optimization tell** — if the "better" parameter direction correlates with more exposure to a mechanic quirk (e.g. smaller T1 → more arming events), verify honest execution preserves the ordering. *(Receipt: MINI beat STANDARD because it maximized the artifact.)*
- [ ] **3.4 Era robustness**: test pre-2021/pre-2018 explicitly; several NQ strategies are profitable 2021–2026 and negative before. Regime concentration must be declared, not discovered live.
- [ ] **3.5 Honest t-stats**: date-clustered errors for overlapping/clustered events. *(Receipt: pullback-resumption t collapsed 6→0.3 under clustering; EMA-crossover "edge" was market drift.)*
- [ ] **3.6 Monte Carlo, correct mode**: dollar-mode for fixed sizing, pct-mode for compounding; block bootstrap when regime clustering exists; MC approved = tearsheet P5 path clean.
- [ ] **3.7 Sample floors & transfer**: enough trades per cell; single-instrument single-signal = elevated overfit prior — seek cross-instrument/cross-timeframe transfer evidence and treat failure honestly. *(Receipt: composite-on-GC transfer negative.)*

## GATE 4 — Sizing & risk realism

- [ ] **4.1 Realized-loss vs sized-risk ratio**: max fill-honest single loss ÷ intended per-trade risk. If > ~2×, the sizing model understates risk. *(Receipt: CPI-day 12.7×.)*
- [ ] **4.2 Sizing sims on honest fills only** — never size off level-fill trade logs.
- [ ] **4.3 Concurrency caps**: multi-symbol sims must bound simultaneous exposure. *(Receipt: equities quick-check's uncapped pool made recovery_factor ~1884 — meaningless.)*
- [ ] **4.4 Naked/unbounded-risk states**: count every backtest state with no live protection possible; stress a flash move through each. If the strategy needs those windows to profit, that IS the strategy — price it as short-tail-risk, or reject.

## GATE 5 — Process

- [ ] **5.1 Checkpoint every phase** — sanity-check each phase's output before feeding the next.
- [ ] **5.2 Reconciliation ≠ validation**: byte-exact replication of a reference only inherits its assumptions. A reconciled port must still pass Gates 2–4 independently. *(Receipt: 0/1319 exact reconciliation of a flawed mechanic, celebrated as the finish line.)*
- [ ] **5.3 Minimal surgical variants for suspicion**: when a backtest smells wrong, change ONE assumption at a time with a validated-identical baseline. Never bundle fixes with redesigns — attribution dies. *(Receipt: Codex's confounded 1-min "correction".)*
- [ ] **5.4 Adversarial audit by a different mind/tool** before any capital, with this checklist as the script.
- [ ] **5.5 Live micro-size forward test as the final gate**: compare every live fill against the backtest's assumed fill, trade-by-trade. Divergence in fills = Gate 2 failure in production.

---

## The one-line version

**A real edge survives being executed.** If the P&L depends on where the backtest *says* orders filled rather than where orders *could* fill — or on windows where no order existed at all — there is no edge, no matter how many years, trades, audits, or reconciliations stand behind it.
