# Unified Market Data — Access Guide (for handoff to Codex / external tools)

One clean source for the backtester. Norgate history (≤ 2026-04-22) + Polygon daily
patch (2026-04-23 →), total-return forward-adjusted (Option A). Audited & APPROVED;
validated bit-identical to the Norgate master on history. See `audit/final_approval_report.md`
and `audit/reverification_report.md`.

## Location

```
C:\Users\shard\Light Water Internship\july-backtester\data\market_data\merged\
```
- **35,310 parquet files · 2.82 GB · one file per symbol**: `{SYMBOL}.parquet` (uppercase).
- Delisted symbols carry a date suffix, e.g. `ONCR-200807.parquet` (survivorship-bias-free set).
- Indices & required assets are in the SAME folder: `SPY QQQ IWM DIA XLF VIX TNX SPX NDX RUT DJI OEX VXN GLD SLV TLT IEF HYG LQD UUP`.

## Recency (as of 2026-06-05)

- Polygon patch window = **2026-04-23 → 2026-06-05** (31 trading days), the "recent" tail.
- **97.1%** of live names (11,513 / 11,853) have a bar on 2026-06-05. Examples:
  SPY 737.55 · QQQ 705.06 · AAPL 307.62 · NVDA 205.34 · GLD 396.24 · TLT 85.71 ·
  VIX 21.51 · TNX 45.36 (10y yield ×10) · SPX 7383.74 · NDX 28957.60.
- Symbols that delisted/halted inside the window end earlier (intended).

## Schema (each parquet)

Index: tz-naive `DatetimeIndex` at midnight. Columns:

| Column | Notes |
|---|---|
| `open high low close volume vwap` | **lowercase**, float64. `close` = total-return adjusted |
| `source` | `norgate` (≤2026-04-22) / `polygon` (after) / `local` |
| `adjustment_factor` | **raw price = `close / adjustment_factor`** |
| `adjustment_method` | `norgate_native / none / split / dividend / split+dividend` |
| `security_type` | granular Polygon type (`CS`/`ETF`/`ETV`/`ADRC`/`UNIT`/`WARRANT`/`PFD`/`FUND`…), or `equity_or_etf` for Norgate-only rows, or `index` for the 8 indices |
| `data_quality_status` | `ok / flagged / review_no_patch / identity_review / insufficient_history` |

⚠️ Raw parquet columns are **lowercase**. The project's strategies expect **Capitalized**
`Open/High/Low/Close/Volume` — rename (snippet below) or use the provider.

## Reading it — zero-dependency (only pandas + pyarrow)

```python
import os, pandas as pd
MERGED = r"C:\Users\shard\Light Water Internship\july-backtester\data\market_data\merged"

def load(symbol, start=None, end=None, capitalized=True, ohlcv_only=True):
    df = pd.read_parquet(os.path.join(MERGED, f"{symbol}.parquet"))
    if start: df = df[df.index >= pd.Timestamp(start)]
    if end:   df = df[df.index <= pd.Timestamp(end)]
    if ohlcv_only: df = df[["open","high","low","close","volume"]]
    if capitalized:
        df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        df.index.name = "Datetime"
    return df

aapl = load("AAPL", "2015-01-01")     # recent strategies: just set a start date
spy, vix = load("SPY"), load("VIX")
```

## Reading it — in-repo provider (drop-in, returns Capitalized OHLCV)

```python
from src.data.unified_market_data_provider import UnifiedMarketDataProvider
p = UnifiedMarketDataProvider()
df   = p.get_price_data("AAPL", "2015-01-01", "2026-06-05")  # engine fetcher contract
prov = p.get_with_provenance("AAPL")                          # + source/factor/method/status
syms = p.available_symbols()                                  # all 35,310
```

## Choosing a universe

`metadata/symbol_classification.csv` = every symbol + `bucket` + `polygon_ticker` + `security_type`.
`bucket == "common_to_both"` (11,853) is the **current** liquid set — fine for a *current* scan,
but **survivorship-biased if applied over history** (see next section). Pre-built lists live in
`tickers_to_scan/*.json` (`nasdaq_100.json`, `sp-500.json`, `dow-jones-industrial-average.json`, …)
— these too are **current snapshots only**.

```python
import pandas as pd, json
cls = pd.read_csv(r"...\data\market_data\metadata\symbol_classification.csv", keep_default_na=False, na_values=[""])
universe = cls[cls.bucket=="common_to_both"].symbol.tolist()
nas100   = json.load(open(r"...\tickers_to_scan\nasdaq_100.json"))
```

## Survivorship bias — TWO separate axes (read before backtesting indices)

This dataset removes **price** survivorship bias (22,402 delisted names are kept). It does
**not** by itself remove **index-membership** survivorship bias. They are different problems:

| Axis | Handled by | If you ignore it |
|---|---|---|
| Price (delisted names have data) | the merged set itself ✅ | backtest only ever sees winners that survived |
| Index membership (who was *in* NQ/SP on date X) | the PIT layer below ⬇ | you hold *today's* members back in 2010 |

⚠ The static `nasdaq_100.json` (101) / `sp-500.json` (503) are **current membership only**.
Using them over history is membership-biased — they omit **475** names that were S&P 500
members at some point 2004→now.

### Point-in-time (survivorship-bias-free) membership — real & wired into `main.py`

Set the portfolio *value* (in `config.py → portfolios`) to one of:

| Value | Meaning | Source it needs |
|---|---|---|
| `"sp500_pit"` | UNION of all S&P 500 members in `[start,end]` | `SP500-Survivorship-bias-data-2004-2026/` → `SP500_DATA_ROOT` in `.env` |
| `"nq100_pit"` | UNION of all Nasdaq-100 members in `[start,end]` | `data/nq100_membership.parquet` (bundled in repo) |
| `"pit:sp500"` / `"pit:nq100"` | members **as of** `start_date` (single snapshot) | same as above |

- S&P union 2004→2026 = **978** names; real changes through **2026-01-14** (not frozen).
- NQ100 union 2004→2026 = **287** names; daily snapshots through **2026-04-30**.
- **Price coverage of those members in `merged/`: 95.8% S&P, 96.2% NQ100.** The ~4% gap is
  mostly ticker renames where the data **is present under the new ticker** (UTX→RTX, ANTM→ELV,
  ABC→COR, KORS→CPRI, MYL→VTRS, …) plus a few names absent from the Norgate snapshot (e.g. MMC).
  Add an alias map to reach ~99%.

```python
# survivorship-bias-free S&P 500 universe + its prices from merged/
from helpers.pit_universe import get_sp500_tickers_in_period
SP500_REPO = r"C:\Users\shard\Light Water Internship\SP500-Survivorship-bias-data-2004-2026"
universe = get_sp500_tickers_in_period("2004-01-01", "2026-06-05", SP500_REPO)   # 978 names
frames = {t: load(t) for t in universe
          if os.path.exists(os.path.join(MERGED, f"{t}.parquet"))}              # ~937 have prices
```

> **Remote Codex:** membership cannot be reconstructed from `merged/` alone. Ship the merged
> subset **plus** the `SP500-Survivorship-bias-data-2004-2026/` repo and
> `data/nq100_membership.parquet`, or use the static current lists and accept the bias.

## Adjustment gotcha

Prices are forward-adjusted to the 2026-04-22 anchor, so a post-anchor split name (e.g. CVNA 5:1
on 05-08) shows a *scaled* canonical price vs a broker quote — correct for return continuity. The
actual traded price is always `close / adjustment_factor`. Returns/signals are unaffected by a
constant scale, so backtests are apples-to-apples.

## Packaging for a remote Codex

Same machine → point at the path above (no copy). Remote → subset to a universe + indices
(e.g. nasdaq_100 + sp-500 + SPY/QQQ/VIX/TNX ≈ a few hundred MB) and zip that rather than the full 2.82 GB.
