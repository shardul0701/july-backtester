# ORB v3 → SignalDeck Scanner: Integration Handoff

**Status: BLOCKED.** This is a blueprint, not a work order. No code should be written in `signaldeckapi` for ORB v3 until the three F0 decisions in §1 are answered.

Tracking ticket: **[GitLab #2199](https://gitlab.com/zachisit/signaldeckapi/-/work_items/2199)** — "scanner cannot run intraday futures strategies", filed 2026-08-03, verified against `signaldeckapi @ 38589323`.
Runbook: **[#2130](https://gitlab.com/zachisit/signaldeckapi/-/work_items/2130)** — equities-only; walked end-to-end with ORB v3 and it does not fit. #2130 itself needs no change.

---

## 1. Blocked on Zach — three decisions, in priority order

Nothing below §1 is actionable until these land. Each one changes the shape of the implementation, so guessing would mean building the wrong thing twice.

| # | Decision | Why it blocks | Options as we see them |
|---|---|---|---|
| **F0.1** | **Continuous-series strategy** for futures | There is **no back-adjusted/continuous series anywhere** in the scanner — only `generate_front_month_tickers`. Un-handled roll gaps manufacture false ORB breakouts roughly **4×/year**, which would look like signals, not errors | (a) back-adjusted continuous series built at ingest; (b) front-month with explicit roll-gap suppression; (c) per-contract with session-level stitching |
| **F0.2** | **Intraday trigger model** | `run_all_scanners` is a single-pass batch. Signal detection is a `df.tail(2)` crossover, so **a trigger that fired earlier in the session is silently missed**. ORB v3 fires between 09:50 and the close | (a) intra-session scheduled loop; (b) end-of-session replay computing the signal as it *would* have fired; (c) full event-driven path |
| **F0.3** | **Futures Definition-of-Done metrics** | Equity DoD metrics are **wrong** for futures. CAGR/Calmar on a nominated base are leverage artifacts — ORB v3 shows Calmar 1.368/1.394/1.441 at 1×/1.35×/2× contracts with no change in edge. Any Calmar/CAGR gate is gameable today | Our proposal: **net$/MaxDD$**, Sharpe(rf=0), expectancy in points+ticks, cost as a share of gross edge, **no buy-and-hold comparator** |

**F0.3 is not futures-specific in its impact.** The underlying problem — no declared capital base anywhere in the scanner (#2199 G7) — affects equities too. There is no `capital`/`equity_base`/`account_size` key in `scanner/config.py` or `scanner_config.py`; the only base in the codebase is a hardcoded `INITIAL_CAPITAL = 100_000.0` at `scanner/strategies/s8_nav.py:59`, and migration 096's `fill_simulation_config` carries `allocation_pct: 10.0` of an unstated base.

---

## 2. Landing pattern — copy DB_B4 exactly

`DB_B4_WF_PIT` is the precedent: **fully registered, tested, and metadata-migrated, but parked in `INTENTIONALLY_EXCLUDED_STRATEGY_KEYS`** pending approval. ORB v3 must land the same way.

This is not caution for its own sake. Scanner promotion creates **real trades** (`AQR_LIVE_EXECUTION_ENABLED=true` in prod), and the audit verdict is *GO micro-forward-test, **NO-GO capital***. Registering ORB v3 as live-eligible would skip straight past the thing being asked for.

```python
# scanner/run_all_scanners.py
INTENTIONALLY_EXCLUDED_STRATEGY_KEYS = {
    "DB_B4_WF_PIT": "pending Mo approval",
    "ORB_V3_MNQ": "intraday futures — blocked on #2199 F0.1/F0.2/F0.3; "
                  "audit verdict GO micro-forward-test / NO-GO capital",
}
```

---

## 3. What transfers from #2130 unchanged

Section C is fully reusable — it is platform-agnostic:

- **C1 metadata migration.** Template: `migrations/versions/096_register_db_b4_wf_pit_metadata.sql`. All six modal columns + `scanner_code`, owner `user_id = 1`. Key on emails + scanner_codes, **never raw user ids**, so it is a clean 0-row no-op in CI/e2e.
- **C2 author.** ORB v3 is intern-authored → `author_user_id = 49`. Add `"ORB_V3_MNQ": 49` to `STRATEGY_AUTHOR_USER_IDS` in `app/crud.py` **and** set it inline in the C1 migration as `COALESCE(author_user_id, 49)`. Both, per the C2 rewrite — the map self-heals a NULL-author re-create, a manual PATCH always wins.
- **C3 intraday flag.** Set **both** `"intraday": True` in `scanner_config.py` and `is_intraday = 1` in the C1 migration, or runtime and the app modal silently disagree.
  ⚠️ **But note what C3 does not do:** it is a partial-bar opt-out on the *daily* path. It does **not** add minute/hour bar fetching. Setting it does not make ORB v3 runnable — it would produce a strategy that believes it is intraday while receiving daily bars. **Do not set it as a workaround for F0.1/F0.2.**
- **C4 MR mechanics.** Feature branch off the current release branch (`v1.0.100` as of 2026-08-03), MR targets that branch and **never `main`**, `## Prod Migration` SQL block in the description.

Also reusable: the `MIN`/`H` timespan map already present at `scanner/services/polygon_service.py:203`, and `get_futures_contract_spec` (returns tick_size / tick_value / contract_size — the scanner just can't reach it today).

---

## 4. Gap inventory — the actual work, once unblocked

Grouped as filed in #2199. Nothing here is speculative; each was verified against the repo.

| Group | Gap | Required |
|---|---|---|
| **G1 data** | Single global `timeframe: "D"` at `scanner/config.py:11-12`, no per-strategy override | Per-strategy timeframe |
| | `scanner/services/polygon_service.py` hits the **stocks** aggs endpoint; scanner never imports `app/services/futures_polygon_service.py` (grep: zero hits) | Wire the futures service into the scanner |
| | Return-shape mismatch: `list[dict]` with split Date+Time vs DataFrame+DatetimeIndex | Adapter |
| | `DAYS_OF_DATA_TO_FETCH = 450` is global | Per-strategy history depth |
| | `price_adjustment: "total_return"` undefined for futures | Futures-valid adjustment mode |
| **G2 execution** | No intraday loop; `df.tail(2)` detection misses earlier-session triggers; `time.mktime(...timetuple())` drops tz; no RTH/session concept | F0.2 |
| **G3 calendar** | `is_market_open_today` reads the **equities** field of `/v1/marketstatus/now`; CME calendar differs | Futures calendar branch |
| **G4 stops** | Stop types are mutually exclusive. ORB v3 needs `fixed_pct` **and** `end_of_day` simultaneously | Composite stop type + A7 schema migration. ⚠️ Mismatched stop names **silently apply no stop** |
| **G5 sizing** | Accounting is share/notional; futures are contracts × point value on margin. No futures universe exists | Contract-aware sizing |
| **G6 benchmarks** | `pre_calculate_features` hardcodes SMA-200 etc. (≈2.5 sessions on 5-min bars) | F0.3 |
| **G7** | No declared capital base anywhere | First-class `declared_capital_base`, **effective-dated** so it can change without silently rebasing history, validated to fund ≥ 1 unit |
| **G8** | No margin concept — zero hits for `initial_margin`/`maintenance_margin`/`buying_power` across `app/` + `scanner/` | Intraday vs overnight margin |
| **G9** | Cost model is per-share and hardcoded per strategy (`s8_nav.py:61-62`) | Per-contract commission+fees, slippage in ticks |
| **G10** | Flat-by-close is intended, not enforced | Enforce **and alarm**. A miss is a capital event |

---

## 5. Sequencing

```
F0.1 / F0.2 / F0.3 answered (Zach)
        ↓
G1 data path  ─────────────►  futures bars reach the scanner at all
        ↓
G4 composite stop + G10 flat-by-close enforcement
        ↓
G5 contract sizing + G7 declared capital base + G8 margin + G9 cost model
        ↓
G2 intraday trigger + G3 CME calendar
        ↓
#2130 Section C (C1 migration, C2 author=49, C4 MR)  ← the only part ready today
        ↓
land parked in INTENTIONALLY_EXCLUDED_STRATEGY_KEYS
        ↓
forward test per FORWARD_TEST_PREREG.md  (Gate 5.5)
        ↓
review for size — NOT automatic capital promotion
```

**The forward test does not need any of this.** It runs outside the scanner. That is precisely why it is the ask, and why it is not gated on #2199.

---

## 6. Overlap to check before starting

**#2137** covers a futures adapter for the Sleeve A scanner integration. The data-path portion (G1) likely overlaps substantially. Worth reconciling scope before either is built twice — flagged, not resolved.
