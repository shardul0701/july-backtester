# SignalDeck Audit — 13 Findings + Proposed Fixes (Handoff for Zach)

**Status: awaiting review — please read the instructions below before starting.**

## Instructions for Zach / Zach's agent

- This is a **read-only audit** of `zachisit/signaldeckapi` (`main` branch, GitLab
  read-only API, no clone, no edits made to that repo). Everything below was
  found by tracing code paths, not by running the app or querying the live DB —
  a few items explicitly need a live DB check or a decision only you can make;
  those are flagged.
- **Shardul is offline until ~11pm tonight (2026-08-19) and cannot be reached
  in the meantime.** Please do not wait for a reply before starting — start
  research/fixes now. **Please only respond/revert on this once your research
  and fixes are as complete as you're going to get them** (i.e. batch your
  findings/patches into one pass rather than partial updates), since there's
  no way to have a back-and-forth on this until Shardul is back online.
- Where a fix is small and unambiguous, a concrete before/after patch is
  included below — verify it against your actual current `main` (line numbers
  may have drifted) before applying. Where a fix requires a product decision
  or a live-DB check first, that's called out explicitly instead of a patch.
- 4 of the 13 findings touch real trade-engine state or money (**1, 9, 10,
  12** — 9 turned out to be self-healing, see below); the rest are
  UI/aggregation-layer bugs. Given `AQR_LIVE_EXECUTION_ENABLED=true` is live in
  production, **Findings 1 and 10 are the two worth triaging first.**

---

## Finding 1 (CONFIRMED) — portfolio-rebalance closes never compute P&L

**File:** `app/crud.py`, function `materialize_portfolio_positions`, close
branch (~line 3357).

This is the close path used by every *portfolio target-weight* strategy —
AQR-A, AQR-B, S8, B1, and any future strategy that rebalances a whole book
rather than firing single buy/sell signals. The close branch does a raw ORM
write and never computes `profit_pct` / `realized_pl_dollars`:

```python
# app/crud.py ~3357 (current, buggy)
trade.status = "CLOSED"
trade.exit_date = entry_date
trade.exit_price = float(exit_price)
trade.exit_reason = (
    "Portfolio rebalance — direction flip" if sym in flipped_syms
    else "Portfolio rebalance — dropped from target book"
)
closed.append(sym)
```

This is not routed through `update_trade()` or `/trades/batch`, the two
places that do recompute those fields — so `realized_pl_dollars` is left
`NULL` on every rebalance close, and the same gap fires on futures
direction-flip closes (`#2137`, same function, ~3300-3448) — a leveraged
position silently landing on `NULL` P&L is the highest-consequence instance
of this bug in the whole book.

**The sibling function already does this correctly** —
`close_open_trades_for_symbol` (`app/crud.py` ~4414):

```python
trade.status = "CLOSED"
trade.exit_reason = "Signal Reversal"
trade.exit_price = signal_price
trade.exit_date = signal_date
if trade.entry_price and signal_price:
    direction = -1 if trade.side == "SHORT" else 1
    trade.profit_pct = round(
        direction * (signal_price - trade.entry_price) / trade.entry_price, 8
    )
    _paper_pl = scanner_paper_realized_pl(trade.profit_pct, trade.shares)
    if _paper_pl is not None:
        trade.realized_pl_dollars = _paper_pl
```

**Proposed fix** — add the equivalent block right after the raw
status/exit_price/exit_date writes in the `materialize_portfolio_positions`
close loop:

```python
# app/crud.py ~3357 (proposed)
trade.status = "CLOSED"
trade.exit_date = entry_date
trade.exit_price = float(exit_price)
trade.exit_reason = (
    "Portfolio rebalance — direction flip" if sym in flipped_syms
    else "Portfolio rebalance — dropped from target book"
)
if trade.entry_price and exit_price:
    direction = -1 if trade.side == "SHORT" else 1
    trade.profit_pct = round(
        direction * (float(exit_price) - trade.entry_price) / trade.entry_price, 8
    )
    if trade.shares:
        # portfolio strategies allocate real capital per position, not the
        # sizeless $10k scanner-signal model — verify against Finding 10's
        # sizing decision before shipping this, since `shares` itself is
        # in dispute (fractional vs whole).
        trade.realized_pl_dollars = round(
            direction * (float(exit_price) - trade.entry_price) * trade.shares, 2
        )
    else:
        trade.realized_pl_dollars = scanner_paper_realized_pl(trade.profit_pct, trade.shares)
closed.append(sym)
```

⚠️ Note the caveat inline: confirm whether `shares` is reliably populated on
these rows before trusting the sized branch — if not, fall back to the
sizeless scanner formula (`scanner_paper_realized_pl`) the same way
`close_open_trades_for_symbol` does.

**Frontend note (not a backend bug, just why it shows as `—`):**
`spa/app.js` ~16317-16319 renders P&L as a direct pass-through of
`risk_unit` then `realized_pl_dollars`, no client-side recompute — neither
field is set by this function, so the cell renders `—`. No frontend change
needed once the backend sets these fields.

---

## Finding 2 (CONFIRMED) — aggregate stats use a different, inconsistent notional than Recent Trades

**File:** `app/services/dashboard_stats.py`, `calculate_net_pl_dollars`
(~line 150), stock branch — feeds Expectancy/Avg Win/Avg Loss/Best/Worst/Net
P&L on every summary card.

```python
# ~150 (current)
shares = t.shares if (t.shares and t.shares != 0) else 1
...
if t.status == 'CLOSED':
    exit_val = (t.exit_price or 0) * shares
    sell_cost = t.sell_fee or 0
    gross = (entry_val - exit_val) if side == 'SHORT' else (exit_val - entry_val)
    return gross - buy_cost - sell_cost
```

For a sizeless scanner/portfolio trade (`shares` NULL — true for AQR and
most managed strategies), this substitutes **1 share** and computes a
one-share price delta — while the *stored* `realized_pl_dollars` for the
same trade (when it does get set, e.g. via `close_open_trades_for_symbol`)
uses a **$10,000 paper-notional** basis
(`SCANNER_PAPER_NOTIONAL_PER_TRADE = 10000.0`, `app/crud.py` ~76). Same
trade, two numbers ~4 orders of magnitude apart, neither derived from the
other.

**Proposed fix** — prefer the stored value when present, matching the
forex/futures/crypto branches immediately above this one in the same file:

```python
# ~150 (proposed)
if t.status == 'CLOSED' and t.realized_pl_dollars is not None:
    return float(t.realized_pl_dollars)

shares = t.shares if (t.shares and t.shares != 0) else 1
...
```

This also fixes **Finding 4** below for free (they share this one call
site) and, once Finding 1 is fixed, makes Recent Trades and the summary
card agree.

---

## Finding 3 (HYPOTHESIS — needs one API check, not source-confirmable) — Bull Flag Best/Worst excludes JBS/HALO

`DashboardStatsService.__init__` (`dashboard_stats.py` ~34-39) drops any
`CLOSED` trade with a missing/pre-2000 `exit_date` from every aggregate:

```python
self._closed_trades = sorted(
    [t for t in trades_data
     if t.status == 'CLOSED' and t.exit_date and t.exit_date >= _MIN_DATE],
    key=lambda x: x.exit_date
)
```

...but `/stats/strategies/{id}/recent-trades` applies no such filter and no
status filter by default (`stats.py` ~1107-1164, `status` param defaults to
`None` = both OPEN and CLOSED). Two explanations for why JBS
(-$576.25)/HALO (+$2,024.25) show in Recent Trades but not Best/Worst, and
this audit can't tell which from source alone:

- **(a) Not a bug** — they're still `OPEN`, and Best/Worst is
  closed-trades-only by design.
- **(b) The bug** — they're `CLOSED` with valid `exit_price` but a
  `NULL`/pre-2000 `exit_date`.

**Fastest disambiguation, no DB pull needed:** the `StrategyRecentTrade`
schema (`schemas.py` ~3520) already returns a `status` field in the same
API response the dashboard is rendering — just check `status` for
JBS/HALO. If `CLOSED`, it's bug (b) and needs a source dig into whatever
close path could null `exit_date` while setting `exit_price` (not found in
this audit — every close path reviewed sets both together, so this may be a
manual-edit or CSV-import path outside what was checked).

---

## Finding 4 (CONFIRMED) — Finding 2 is the single source for the entire dollar-denominated summary card

`app/api/v1/endpoints/stats.py`, `get_strategy_performance_stats`
(~1006-1024) computes Net P&L, Expectancy, Avg Win, Avg Loss, Best, and
Worst from one shared list built off `calculate_net_pl_dollars`. Same for
`compute_equity_and_drawdown` (`dashboard_stats.py` ~413, feeds the
cumulative-P&L/Max Drawdown chart). **One fix (Finding 2's proposed patch
above) repairs Net P&L, Expectancy, Avg Win, Avg Loss, Best, Worst, and Max
Drawdown all at once** — no separate fix needed per field.

**Scope boundary, not affected:** the percent-based widgets
(`compute_summary`, `compute_monthly_pnl`, `compute_pnl_distribution`,
`compute_sharpe`, `compute_monte_carlo`, `compute_rolling_win_rate`) use
`calculate_roi_decimal` instead, whose sizeless-stock branch is a plain
`(exit-entry)/entry` with no notional assumption — Win Rate % and dashboard
Sharpe/Monte-Carlo are trustworthy even for AQR-style trades today.

`is_win()` also already falls through to a raw price comparison when
`shares`/`risk_unit`/`realized_pl_dollars` are all absent, so **Win/Loss
classification is fine for AQR** — only the dollar magnitudes are wrong.

---

## Finding 5 (confirmed mechanism, same open question as Finding 3)

`best_trade = max(_pnls)` / `worst_trade = min(_pnls)` (`stats.py`
~1023-1024) are confirmed to run only over `DashboardStatsService`'s strict
closed-trades filter (Finding 3's snippet). No new information beyond
Finding 3 — same one-field check (`status` on JBS/HALO via
`recent-trades`) resolves both at once.

---

## Finding 6 (CONFIRMED) — "Expectancy Momentum" widget silently flat (0.0) for every portfolio-style strategy

**File:** `dashboard_stats.py`, `compute_momentum` (~376-395).

```python
# current
recent_r_vals = [t.risk_unit for t in recent_trades if t.risk_unit is not None]
if recent_r_vals:
    expectancy_momentum = (sum(recent_r_vals) / len(recent_r_vals)) - avg_r
```

`risk_unit` is never set by `materialize_portfolio_positions` (Finding 1),
so for AQR-A/AQR-B/S8/B1 `recent_r_vals` is always empty and
`expectancy_momentum` silently stays at its `0.0` default — reads as "flat"
rather than "not computable." `win_rate_velocity` in the same function is
unaffected (uses `is_win()`, which has a fallback), so the pair can be
misleading together.

**Proposed fix** — return `None` instead of `0.0` when unset, matching how
`expectancy_dollars`/`avg_win`/etc. already signal "not computable"
elsewhere in this file:

```python
# proposed
recent_r_vals = [t.risk_unit for t in recent_trades if t.risk_unit is not None]
expectancy_momentum = (
    (sum(recent_r_vals) / len(recent_r_vals)) - avg_r
    if recent_r_vals else None
)
```

Frontend (`spa/`) will need to render `None`/`null` as "N/A" wherever this
widget is displayed — not audited in detail, flag for whoever picks this up.

---

## Finding 7 (CONFIRMED) — the QA endpoint that would catch Finding 1 exists but has zero consumers

`crud.get_trades_needing_audit` (`app/crud.py` ~4329-4367) finds `OPEN`
trades plus `CLOSED` trades with `NULL` `profit_pct`/`risk_unit` — exactly
Finding 1's symptom. Exposed via `GET /trades/pending-audit`
(`app/api/v1/endpoints/trades.py` ~40-134). Repo-wide search: **nothing
calls it** — no SPA widget, no admin page, no worker. It's live, correct,
and orphaned.

Also: `excluded_strategy_id: int = 22` defaults silently drop strategy 22
from results, no comment explaining why — **worth a 30-second check: what
is strategy 22, and is it a portfolio-rebalance strategy?** If so, even a
future caller of this endpoint would never see Finding 1's trades.

**No code patch proposed** — this is a decision (wire it into an
admin/QA view, or remove it if considered superseded by Scope B/C
reconciliation) rather than a mechanical fix.

---

## Finding 8 (CONFIRMED) — Scope B reconciliation exists for Finding 1, but the `risk_unit` piece has a gap for stopless strategies

`app/worker/signaldeck_trade_audit_worker.py`, `SCOPE B: RECONCILIATION`
block inside `run_audit_cycle` (~4177-4203) is live, runs every audit
cycle, and (per Finding 9 below) does cover AQR-A/AQR-B/S8/B1:

```python
if is_managed and _do_management and trade['status'] == 'CLOSED':
    if entry and exit_p and trade.get('profit_pct') is None and 'profit_pct' not in payload:
        payload['profit_pct'] = round(pnl, 8)
        report['closed_trades_reconciled'].append(trade_id)

    stop = to_native_type(trade.get('original_stop'))
    if entry and exit_p and stop and stop != 0 and trade.get('risk_unit') is None:
        ...
        payload['risk_unit'] = ru
        report['risk_units_backfilled'].append(trade_id)
```

The `risk_unit` backfill requires a non-null/non-zero `original_stop` —
portfolio-rebalance strategies are stopless by design, so that half of the
reconciliation likely never fires for these four strategies even though the
`profit_pct`/`realized_pl_dollars` half does (confirmed: the PUT this
triggers routes through `crud.update_trade()` →
`_compute_closed_trade_exit_metrics()`, which derives
`realized_pl_dollars` unconditionally whenever both prices are set).

**Two things to decide, not mechanically patchable without your input:**
1. Give the `risk_unit` backfill a fallback for stopless strategies (skip
   `risk_unit`, still set `realized_pl_dollars` via a direct PUT — it
   already does this part), or confirm the SPA's P&L cell degrades
   gracefully to `realized_pl_dollars` when `risk_unit` is permanently null
   (per Finding 1's frontend note, it does fall back — just confirm that's
   intentional, not accidental).
2. This is `user_id`-gated (`is_managed`), so it only helps *today's* four
   managed strategies — any future non-managed portfolio strategy needs
   Finding 1's actual fix (item 1), not this safety net.

---

## Finding 9 (RESOLVED) — Finding 1 IS self-healing for AQR-A/AQR-B/S8/B1: all four are `user_id=2` (managed)

Resolved from `migrations/versions/099_purge_intern_portfolio_trade_history_2207.sql`
(2026-08-04)'s own "PROD VERIFICATION" section, not a fresh query:

> "strategy_id 2549 AQR_MOMENTUM_MR_BLEND / 2561 S8_DMVC35 / 2562 B1_VG12 /
> 2564 AQR_A_MOMENTUM_MR_BLEND. Owner user_id=1, author_user_id=49. **ALL 106
> trades on these strategies belong to user_id=2 (scanner@signaldeck.local,
> the scanner service account).** No other user_id appears; deleted_at is
> NULL on all 106."

So Scope B (Finding 8) is actively reconciling `profit_pct`/
`realized_pl_dollars` for these four strategies nightly. **This downgrades
Finding 1 from "permanently blank" to "blank until next reconciliation
pass"** — still worth fixing at the source (Finding 1's patch) because:
(a) the `risk_unit` gap (Finding 8) likely never closes for these stopless
strategies even with reconciliation running, (b) anything reading the data
same-day, before the next audit cycle, still sees the blank, and (c) any
future non-managed portfolio strategy gets none of this safety net.

**No fix needed for this finding itself** — informational, resolves the
open question in Finding 8.

---

## Finding 10 (CONFIRMED, HIGH IMPACT) — position-sizing formula disagrees with production (#2407, already open on your tracker)

**File:** `app/crud.py:3459` (inside `materialize_portfolio_positions`,
stock open branch):

```python
shares = round((weight * capital) / price, 4)
```

Sizes every portfolio-strategy position to **fractional shares, 4 decimal
places**. `app/services/portfolio_sizing.py`'s `quantize_shares()` mirrors
this exact formula, but its own docstring flags the mismatch:

> "⚠️ THIS AGREES WITH THE REPO, NOT WITH PRODUCTION (#2407). Every
> portfolio position in prod is sized in WHOLE shares — 59/59 open rows
> match `round(raw, 0)` and none match `round(raw, 4)`, checked against the
> `scanner_runs.results_json` payload that sized each one."

So your own team already audited this against live prod data and found
100% of real open positions (59/59) are whole-share-sized, while the
`main`-branch code that's supposed to be producing them computes fractional
shares. This means either an older/different sizing path is actually
deployed, or something else overrides the fractional count before it hits
the book — either way, **the source in this repo doesn't currently
describe how live share counts are actually being decided**, which is
exactly the kind of divergence that compounds silently (dollar P&L still
*looks* plausible, just computed against the wrong share count).

**No patch proposed — this needs your decision, not a mechanical fix**:
whichever convention is correct (fractional vs whole), the other side needs
to change — either `crud.py:3459` switches to `round(raw, 0)` (and
`quantize_shares()` follows, per its own stated contract), or someone
identifies and documents whatever code path is actually rounding live fills
to whole shares, since it isn't this one. This also blocks the `#2362`
size-true-up feature (`resolve_size_adjustment()` in `portfolio_sizing.py`)
from shipping safely, since it computes drift against the fractional
target today.

**Not a bug (checked, ruled out):** the shared
`DEFAULT_PORTFOLIO_STRATEGY_CAPITAL = 100_000.0` used by every portfolio
strategy is a deliberate per-strategy NAV-rebasing constant per
`docs/SCANNER_PORTFOLIO_STRATEGIES.md` — not a Capital Isolation Policy
violation. No action needed on that part.

---

## Finding 11 (CONFIRMED) — `compute_top_pairs` missing the exact fallback its sibling function already has, 20 lines away

**File:** `dashboard_stats.py`. `compute_streak()` (~634) does this
correctly, with its own comment referencing a previously-fixed bug:

```python
# #1495 bug 21: prefer realized_pl_dollars when set; otherwise derive
# from shares/price (side-aware) so stock trades without a stored
# realized P&L are not silently treated as $0 and don't break the streak.
if t.realized_pl_dollars is not None:
    pl = float(t.realized_pl_dollars)
else:
    pl = DashboardStatsService.calculate_net_pl_dollars(t)
daily[t.exit_date] += pl
```

`compute_top_pairs()` (~669) does not:

```python
# current
for t in self._closed_trades:
    sym = t.symbol or ''
    sym_key = sym[2:] if sym.startswith('C:') else sym
    pl_map[sym_key] += (t.realized_pl_dollars or 0.0)
    cnt_map[sym_key] += 1
```

Any closed trade with `realized_pl_dollars` still `NULL` (the
pre-reconciliation state every AQR-A/AQR-B/S8/B1 close passes through, per
Findings 1/9) silently contributes **$0.00** to that symbol's Top Pairs
bucket instead of its real P&L.

**Proposed fix** — one-line change, mirrors the sibling exactly:

```python
# proposed
for t in self._closed_trades:
    sym = t.symbol or ''
    sym_key = sym[2:] if sym.startswith('C:') else sym
    pl = t.realized_pl_dollars if t.realized_pl_dollars is not None else DashboardStatsService.calculate_net_pl_dollars(t)
    pl_map[sym_key] += pl
    cnt_map[sym_key] += 1
```

Cheapest fix in this whole list — one function, mirrors an already-fixed
sibling in the same file.

---

## Finding 12 (CONFIRMED) — MAE/MFE chart markers render outside the chart box: this is the "comes out of the box" bug, root-caused

**File:** `spa/modules/tv-price-chart.js`, `_repositionMaeMfeEls()`
(lines 2141-2168):

```js
// current
function _repositionMaeMfeEls() {
    ...
    var y = series.priceToCoordinate(m.price);
    var x = timeScale.timeToCoordinate(m.time);
    if (y == null || !Number.isFinite(Number(y)) || x == null || !Number.isFinite(Number(x))) {
        el.style.display = 'none';
        return;
    }
    el.style.display = '';
    el.style.top  = (Number(y) - 10) + 'px';   // no clamp to [0, containerHeight]
    el.style.left = (Number(x) - 22) + 'px';
}
```

MAE/MFE labels are appended straight to the chart `container` (line 2199),
which has no `overflow:hidden`. `priceToCoordinate()` is unbounded — MAE/MFE
are by definition a trade's most extreme excursion, so whenever the price
scale autoscales to the visible candle window (or the user pans/zooms —
fires on every `subscribeVisibleTimeRangeChange`, line 2239-2240), the
MAE/MFE price can map to a Y pixel far above/below the chart card. The
function only checks `Number.isFinite`, not the actual `[0, containerHeight]`
range — this is a **regression relative to three sibling functions in the
same file**, all of which already carry this exact guard (documented fix
`SD-047`):

- `_repositionAlertHandles()` (line 1020, guard at 1043)
- `_repositionStopHandle()` (line 1271, guard at 1285)
- `_repositionTargetHandle()` (line 1480, guard at 1494)
- `_repositionRRZones()` (line 1706) additionally renders into a dedicated
  `overflow:hidden` clip wrapper (`data-rr-clip`) — whose own comment notes
  the team already knew MAE/MFE markers, drag handles, and event markers
  "legitimately render at negative offsets."

**Proposed fix** — copy the exact guard from the three siblings:

```js
// proposed
function _repositionMaeMfeEls() {
    ...
    var y = series.priceToCoordinate(m.price);
    var x = timeScale.timeToCoordinate(m.time);
    if (y == null || !Number.isFinite(Number(y)) || x == null || !Number.isFinite(Number(x))) {
        el.style.display = 'none';
        return;
    }
    var containerH = container.clientHeight;
    var yNum = Number(y);
    if (containerH > 0 && (yNum < 0 || yNum > containerH)) {
        el.style.display = 'none';
        return;
    }
    el.style.display = '';
    el.style.top  = (yNum - 10) + 'px';
    el.style.left = (Number(x) - 22) + 'px';
}
```

(Verify the exact `container` variable name in scope at your current line
numbers — pattern-matched from the three sibling functions, not
independently re-derived.) This directly matches the user-reported symptom
("chart comes out of the box... seen many times") and reproduces on
ordinary pan/zoom, not an edge case — **recommend prioritizing this one for
a quick visual fix even ahead of the trade-engine items**, since it's the
most visible/reported symptom and the smallest, safest patch in this list.

---

## Finding 13 (CONFIRMED) — `compute_monthly_pnl` sums raw ROI fractions with no ×100, unlike its sibling and its own "%" label

**File:** `dashboard_stats.py`, lines 460-467:

```python
# current
def compute_monthly_pnl(self) -> List:
    monthly_map: Dict[str, float] = {}
    for t in self._closed_trades:
        if t.exit_date:
            m_key = t.exit_date.strftime("%Y-%m")
            monthly_map[m_key] = monthly_map.get(m_key, 0) + self.calculate_roi_decimal(t)
    return [schemas.ChartPoint(x=k, y=v) for k, v in sorted(monthly_map.items())]
```

`compute_pnl_distribution()` (~490) explicitly does
`roi_pct = self.calculate_roi_decimal(t) * 100` before using it —
`compute_monthly_pnl` is the only consumer that skips that step, yet the
frontend renders its output as `'Monthly P&L %'` (`spa/app.js` ~16120-16127)
with no compensating ×100 on the JS side either.

**Impact:** a single leveraged forex/CFD trade in a month can produce a
decimal well above 1.0 (e.g. 300% ROI → `3.0`). Chart.js's default
auto-ranging y-axis stretches to fit that one outlier month, visually
flattening every other month's bar to near-zero height — a second,
independent "chart looks wrong" bug, different mechanism from Finding 12
(unit mismatch inflating data vs. unclamped DOM position), worth fixing
separately.

**Proposed fix** — one-line change, matches the sibling exactly:

```python
# proposed
def compute_monthly_pnl(self) -> List:
    monthly_map: Dict[str, float] = {}
    for t in self._closed_trades:
        if t.exit_date:
            m_key = t.exit_date.strftime("%Y-%m")
            monthly_map[m_key] = monthly_map.get(m_key, 0) + (self.calculate_roi_decimal(t) * 100)
    return [schemas.ChartPoint(x=k, y=v) for k, v in sorted(monthly_map.items())]
```

---

## Priority order (Shardul's read, not gospel — re-rank as you see fit)

1. **Finding 12** — smallest, safest patch, directly matches the reported
   visual bug, ship first.
2. **Finding 1** — root fix for the biggest money-adjacent gap; do this even
   though Finding 9 shows it's currently self-healing for the 4 live
   managed strategies, since the healing has real gaps (Finding 8) and
   won't cover future non-managed strategies.
3. **Finding 10 (#2407)** — needs your decision on fractional vs whole
   shares; flagging as urgent because it's already open on your tracker and
   blocks #2362 safely shipping.
4. **Findings 2/4** — one shared fix, repairs 6+ dashboard fields at once.
5. **Findings 11, 13** — both one-line, low-risk, ship together.
6. **Finding 6** — cosmetic but easy.
7. **Findings 7, 8, 3/5** — decisions/verification, not mechanical patches;
   lowest urgency of the 13 but worth a look when time allows.

## What's NOT covered by this audit (flag if you want it picked up next)

- The `_tag_agg`/`_signal_agg`/`_discipline_agg` SQL query definitions
  themselves (only their Python consumers were checked).
- A live DB spot-check of whether the 59 whole-share positions (Finding 10)
  have drifted since your 2026-08-13 prod copy.
- A runtime/browser repro of Findings 12/13 (this audit is source-only, per
  the standing no-clone/no-edit rule on `signaldeckapi`).
- A full visual check of AQR-B/S8/B1's own Recent Trades lists for the same
  blank-P&L symptom as AQR-A (strong candidates per Finding 1's
  generalization, not individually re-checked here).

---

*Compiled by Shardul's Claude session from a source-only audit of
`zachisit/signaldeckapi` (GitLab read-only API, `main` branch, 2026-08-19).
No changes were made to that repository. This document and the proposed
patches above are for your/your agent's review and verification against the
live schema before applying.*
