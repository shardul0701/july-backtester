# ORB v3 (MNQ) — Promotion Bundle

Self-contained review package for **ORB v3**, a 15-minute opening-range retest-confirm strategy on MNQ 5-minute bars.

**The ask: review for a micro-size forward test. Not capital promotion.**

---

## Start here

| Read | For |
|---|---|
| **[PROMOTION_PACKAGE.md](PROMOTION_PACKAGE.md)** | The review document. Spec, gate ladder, verified numbers, open findings. **§0 leads with the three things that are not in our favour** |
| [FORWARD_TEST_PREREG.md](FORWARD_TEST_PREREG.md) | Kill criteria, written before any live fill exists so they can't be renegotiated later |
| [SIGNALDECK_INTEGRATION_HANDOFF.md](SIGNALDECK_INTEGRATION_HANDOFF.md) | Why this can't enter the scanner yet, and the MR blueprint for when it can |
| [REPRODUCTION.md](REPRODUCTION.md) | Exact commands, expected output, data hashes |

---

## Headline — verified from source 2026-08-03

Full window incl. the free out-of-sample overlay (2021-01-26 → 2026-07-30):

| | |
|---|---|
| Signals | **137** (~25/yr) |
| Win rate | 63.5% |
| Expectancy | +49.56 pts / **$+99.13** |
| Profit factor | 2.264 |
| Net | +6,790.2 pts / **$+13,580.48** |
| Max drawdown | −944.3 pts / **$−1,888.59** |
| net$ / MaxDD$ | 7.191 |
| Losing years | **0** (2021–2026) |
| MC P(net < 0) | **0.03%** (block bootstrap, 10k paths) |

**Benchmark forward expectancy to +45.4 pts/trade, not +50.98.** The stop was tuned against the test split, so the headline carries a ~10–12% haircut. That is stated up front rather than discovered in review.

Metrics are points and dollars, not CAGR/Calmar — for a fixed-contract futures strategy those are pure functions of the nominated equity base and therefore gameable. See PROMOTION_PACKAGE.md §5.

---

## Gate ladder at a glance

**8 pass · 2 fail · 2 partial · 2 not run**

| | Gates |
|---|---|
| ✅ Pass | 0, 1.1–1.5, 1.3 (structural), **2** (−2.56 pts / 0.04%), 3.1 (placebo p=0.0000), **3.6** (MC — newly closed for this bundle), 5.4 |
| ❌ Fail | **1.6, 3.2** — one shared root cause: `STOP_PCT` selected on the test split, twice |
| 🟡 Partial | 3.7 (transfer degrades with distance from NQ), 4.2 (sizing off level-fill logs, 0.04% gap) |
| ⬜ Not run | 2.7 (no broker-sim replica), **5.5 — this is the ask** |

---

## Scope

**ORB v3 alone.** Gap-and-go is *not* in this bundle — it stayed frozen at production thresholds after a pre-registered test came back unsupported, and on the 16 sessions where both legs fired they correlate **+0.965**. Prior work covering both legs together is kept in `evidence/` for provenance.

Note the filename `orb_combined_base.py`: "combined" there means the *three ORB filters* (retest-confirm + overnight context + magnitude), **not** ORB + gap-and-go.

---

## Status

| | |
|---|---|
| Audit verdict | **GO micro-forward-test, NO-GO capital** |
| Scanner integration | **Blocked** — [GitLab #2199](https://gitlab.com/zachisit/signaldeckapi/-/work_items/2199), 3 F0 decisions with Zach |
| Forward test | **Not started.** Runs outside the scanner, so it is not gated on #2199 |
| Live capital | **None.** No position has been taken |
