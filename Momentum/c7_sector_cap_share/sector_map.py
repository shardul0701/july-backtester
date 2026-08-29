"""Coarse SIC-major-group -> broad sector bucket mapper.

Built from Polygon `sic_code` (current classification only, not point-in-time)
cached in scripts/sp500_pit_sic.json by fetch_sp500_pit_sic.py. Used to test a
sector-diversification cap on the C7 sub-sleeve (see rebuild_blended_pit.py,
C7_SECTOR_CAP_MAX env var).

Known limitation: SIC codes are coarser/older than GICS and occasionally
mis-bucket a company relative to how it actually trades today (e.g. GLW/Corning
carries a legacy "nonferrous wire drawing" SIC code from its cable business,
so it lands in Materials here rather than Technology). This is disclosed, not
silently patched -- the mapping is applied uniformly to all 867 tickers with
no per-ticker overrides.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIC_PATH = os.path.join(_HERE, "sp500_pit_sic.json")
# Delisted names that the base file records as null purely because
# fetch_sp500_pit_sic.py queried Polygon without a `date` (see
# backfill_delisted_sic.py). Overlaid on top of the base file; never overrides
# a ticker the base file already classified.
_BACKFILL_PATH = os.path.join(_HERE, "delisted_sic_backfill.json")


def _bucket_for_sic(sic_code: str | None) -> str:
    if not sic_code:
        return "Unknown"
    code = int(sic_code)
    major = code // 100  # 2-digit major group

    if major in (1, 2) and code < 999:
        return "Agriculture"
    if 1000 <= code <= 1499:
        return "Energy" if 1300 <= code <= 1399 else "Materials"  # oil&gas vs metal mining
    if 1500 <= code <= 1799:
        return "Industrials"  # construction
    if 2000 <= code <= 2199:
        return "Consumer Staples"  # food, tobacco
    if 2200 <= code <= 2399:
        return "Consumer Discretionary"  # textiles, apparel
    if 2600 <= code <= 2699:
        return "Materials"  # paper
    if 2700 <= code <= 2799:
        return "Communication Services"  # publishing/printing
    if 2800 <= code <= 2836:
        return "Health Care"  # chemicals->pharma overlap, bias to healthcare for 2830s
    if 2840 <= code <= 2899:
        return "Materials"  # industrial/specialty chemicals
    if 2900 <= code <= 2999:
        return "Energy"  # petroleum refining
    if 3000 <= code <= 3299:
        return "Materials"  # rubber, plastics, glass, stone, concrete
    if 3300 <= code <= 3399:
        return "Materials"  # primary metals (incl. GLW's legacy 3357)
    if 3400 <= code <= 3499:
        return "Industrials"  # fabricated metal products
    if 3500 <= code <= 3599:
        return "Technology"  # industrial/commercial machinery + computer equipment (3571/3572/3577)
    if 3600 <= code <= 3699:
        return "Technology"  # electronic & other electrical equipment (3674 semis etc.)
    if 3700 <= code <= 3799:
        return "Industrials"  # transportation equipment (autos, aerospace, ships)
    if 3800 <= code <= 3899:
        return "Health Care" if 3840 <= code <= 3851 else "Industrials"  # medical instruments vs misc mfg
    if 3900 <= code <= 3999:
        return "Consumer Discretionary"
    if 4000 <= code <= 4799:
        return "Industrials"  # transportation services (rail/air/trucking)
    if 4800 <= code <= 4899:
        return "Communication Services"  # telecom
    if 4900 <= code <= 4999:
        return "Utilities"
    if 5000 <= code <= 5199:
        return "Industrials"  # wholesale trade
    if 5200 <= code <= 5999:
        return "Consumer Discretionary"  # retail
    if 6000 <= code <= 6399:
        return "Financials"  # banks, credit, insurance carriers
    if 6400 <= code <= 6499:
        return "Financials"  # insurance agents
    if 6500 <= code <= 6599:
        return "Real Estate"
    if 6700 <= code <= 6799:
        return "Financials"  # holding/investment offices
    if 7000 <= code <= 7369:
        return "Consumer Discretionary"  # hotels, personal/business services
    if 7370 <= code <= 7379:
        return "Technology"  # computer services / software / data processing
    if 7380 <= code <= 7999:
        return "Consumer Discretionary"
    if 8000 <= code <= 8099:
        return "Health Care"
    if 8100 <= code <= 8999:
        return "Industrials"  # legal, education, misc services
    return "Other"


_cache: dict[str, str] | None = None


def load_ticker_sector_map() -> dict[str, str]:
    """ticker -> broad sector bucket, base file plus the delisted backfill."""
    global _cache
    if _cache is not None:
        return _cache
    with open(_SIC_PATH) as fh:
        raw = json.load(fh)
    m = {t: _bucket_for_sic(v.get("sic_code")) for t, v in raw.items()}
    if os.path.exists(_BACKFILL_PATH):
        with open(_BACKFILL_PATH) as fh:
            extra = json.load(fh)
        for t, v in extra.items():
            if m.get(t, "Unknown") == "Unknown":
                m[t] = _bucket_for_sic(v.get("sic_code"))
    _cache = m
    return _cache


if __name__ == "__main__":
    m = load_ticker_sector_map()
    from collections import Counter
    print(Counter(m.values()))
    for t in ["AMAT", "DELL", "GLW", "INTC", "MU", "STX", "WDC"]:
        print(t, m.get(t, "Unknown"))
