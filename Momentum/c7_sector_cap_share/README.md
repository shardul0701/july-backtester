# C7 Sector-Diversification Cap — Handoff Bundle

Everything needed to reproduce the S8 sector-cap result on your own machine. The
change is **opt-in and default-off** — with `C7_SECTOR_CAP_MAX` unset, the engine
behaves *exactly* as the promoted PR #11 build (bit-for-bit; the uncapped branch is
the original `nlargest(C7_N_POSITIONS)` path).

---

## TL;DR — what the change does

C7 (the momentum sleeve inside B1/B2/B1_35 → SSD/S8) ranks the full S&P 500 by
trailing 126-day return and takes the top 7, **with zero sector awareness**. The cap
adds a greedy post-filter: walk down the same ranked list, but skip a name once its
sector bucket already holds `C7_SECTOR_CAP_MAX` picks. Ranking, universe, prices,
rebalance cadence, QQQ regime gate — all unchanged.

- **Only C7 is touched.** MR sleeve is byte-identical between capped and uncapped runs.
- **Sector labels are current Polygon SIC** (coarse but stable). 235 delisted/unclassified
  tickers → `Unknown` → **cap-exempt** (each is its own singleton bucket). This can only
  *weaken* the cap historically, never inflate it — the benefit is conservative.

---

## Files in this bundle

| File | Status | Purpose |
|---|---|---|
| `rebuild_blended_pit.py` | **modified** (3 additive hunks — see below) | The backtest engine. Diff against your copy, or apply the 3 hunks. |
| `sector_map.py` | **new** | `_bucket_for_sic()` + `load_ticker_sector_map()` → `{ticker: bucket}`. Reads the JSON below. No network. |
| `sp500_pit_sic.json` | **new (committed data)** | 867 SP500-PIT tickers → `{sic_code, sic_description}`. Ship this so you don't need a Polygon key. |
| `fetch_sp500_pit_sic.py` | **new (provenance only)** | One-off that generated the JSON from Polygon `v3/reference/tickers`. You do NOT need to run it — the JSON is included. |

`sector_map.py` + `sp500_pit_sic.json` live alongside the engine in `scripts/`.

---

## The 3 hunks applied to `rebuild_blended_pit.py`

All additive; nothing existing was altered.

**Hunk 1 — make `sector_map.py` importable (after the existing ROOT insert, ~line 22):**
```python
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # <-- ADDED: for sector_map
```

**Hunk 2 — read the env flag + load the map once (after `OUT_BASE.mkdir(...)`, ~line 38):**
```python
# Opt-in C7 sector-diversification cap (default 0 = disabled, original
# unconstrained top-N behavior). Set e.g. C7_SECTOR_CAP_MAX=2 to test capping
# the number of C7 positions drawn from the same broad sector bucket.
C7_SECTOR_CAP_MAX = int(os.environ.get("C7_SECTOR_CAP_MAX", "0"))
_SECTOR_MAP = None
if C7_SECTOR_CAP_MAX > 0:
    from sector_map import load_ticker_sector_map
    _SECTOR_MAP = load_ticker_sector_map()
```

**Hunk 3 — the selection block in `simulate_b1_b2_pit()` (~line 422). The old single line**
```python
                for rank, s in enumerate(avail.nlargest(C7_N_POSITIONS).index):
```
**becomes:**
```python
                if C7_SECTOR_CAP_MAX > 0:
                    # Greedy walk down the ranked list, skipping a name once its
                    # sector bucket already holds C7_SECTOR_CAP_MAX positions.
                    # "Unknown" bucket (unclassified/delisted tickers) is exempt
                    # from the cap -- each unknown name counts as its own
                    # singleton bucket rather than being lumped together.
                    ranked = avail.sort_values(ascending=False)
                    picked, sector_ct = [], {}
                    for s in ranked.index:
                        sec = _SECTOR_MAP.get(s, "Unknown")
                        key = sec if sec != "Unknown" else f"__unk_{s}"
                        if sector_ct.get(key, 0) >= C7_SECTOR_CAP_MAX:
                            continue
                        picked.append(s)
                        sector_ct[key] = sector_ct.get(key, 0) + 1
                        if len(picked) == C7_N_POSITIONS:
                            break
                    selected = picked
                else:
                    selected = list(avail.nlargest(C7_N_POSITIONS).index)
                for rank, s in enumerate(selected):
```

---

## Exact reproduction commands

Same window/costs as the promoted PR #11 build. Set `SP500_DATA_ROOT` to your copy of
`SP500-Survivorship-bias-data-2004-2026`; merged Norgate prices are read from the repo
default `data/market_data/merged`.

```bash
export SP500_DATA_ROOT="<...>/SP500-Survivorship-bias-data-2004-2026"
export BLEND_TEST_START="2008-01-01"
export BLEND_FETCH_START="2007-06-01"
export BACKTEST_END_DATE="2026-04-30"

# Baseline (uncapped — reproduces PR #11):
C7_SECTOR_CAP_MAX=0 REBUILD_OUT_DIR="c7_cap0" python scripts/rebuild_blended_pit.py

# Capped (max 2 per sector):
C7_SECTOR_CAP_MAX=2 REBUILD_OUT_DIR="c7_cap2" python scripts/rebuild_blended_pit.py
```

Per-strategy metrics land in `output/<REBUILD_OUT_DIR>/metrics_comparison_all5.csv`.

---

## Expected numbers (2008–2026, PIT, costs modeled: 5bps slip + $0.002/sh)

**S8_DMVC35 (the PR #11 strategy):**

| Cap | CAGR | MaxDD | Calmar | Sharpe(Rf=4%) |
|---|---|---|---|---|
| 0 — uncapped | +12.39% | −14.97% | 0.828 | 0.713 |
| 1 | +11.49% | −13.58% | 0.846 | 0.649 |
| **2** | +12.31% | −14.14% | **0.871** | 0.708 |
| 3 | +12.32% | −14.93% | 0.825 | 0.708 |

Smooth single-humped response, peak on the pre-existing `THEME_CAP_MAX=2` default; no
value reverses S8 into harm. Sharpe ~flat while Calmar rises → the gain is drawdown/tail
reduction, not new alpha.

**All 4 C7-derived strategies, cap 0 → 2:**

| Strategy | CAGR | MaxDD | Calmar |
|---|---|---|---|
| S8_DMVC35 | +12.39% → +12.31% | −14.97% → −14.14% | 0.828 → 0.871 |
| B1_VG12 (VT12) | +11.23% → +10.93% | −16.03% → −14.94% | 0.701 → 0.731 |
| B2_VG12 (VT12) | +11.15% → +10.88% | −16.08% → −15.69% | 0.693 → 0.693 |
| SSD_B2_DG20 | +12.62% → +12.59% | −18.20% → −18.00% | 0.693 → 0.699 |
| MR_VG12 (control) | +5.32% → +5.32% | −9.36% → −9.36% | 0.568 → 0.568 |

MR unchanged confirms the cap is isolated to C7.

---

## Live illustration — July 2, 2026 (single day)

C7 book that morning was 6/7 semis-storage. Rebuilt with the cap:

| | C7 holdings | C7 sleeve 1-day | Full S8 book 1-day |
|---|---|---|---|
| Uncapped (actual) | AMAT, DELL, GLW, INTC, MU, STX, WDC | −8.1% | **−2.37%** |
| Capped 2/sector | MU, INTC, GLW, MRNA, KLAC, FIX, IBKR | −4.5% | **−0.96%** |

Kept the two strongest names (MU, INTC); the 4 redundant semis/storage slots roll to the
next-best names in other sectors (MRNA +10% that day, KLAC, FIX, IBKR). ~60% less loss.

---

## Caveats (full disclosure)

1. **Blend-level cap sensitivity:** for S8, cap∈{1,2} both beat baseline and cap=3 is
   neutral. For B1/B2 individually, cap=2 is the sweet spot and cap=1/3 are slightly worse
   (~0.03–0.05 Calmar). cap=2 is the pre-registered research default, not tuned here.
2. **SIC is coarse:** a few names bucket oddly (KLAC/COHR → Industrials via an optical-
   instruments SIC; GLW → Materials). Doesn't change the conclusion — the goal is only to
   avoid stacking crash-correlated names, and even the coarse map spread July 2 across 5
   buckets.
3. **Backtest only:** the cap currently lives in `rebuild_blended_pit.py`. Porting it to the
   live `build_s8_alpaca_targets.py` is a separate, pending step.
