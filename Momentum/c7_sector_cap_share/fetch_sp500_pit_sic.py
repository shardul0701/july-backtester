"""One-off: fetch Polygon sic_code/sic_description for the full SP500 PIT ticker
universe (2008-2026) and cache to scripts/sp500_pit_sic.json.

Read-only reference-data pull (Polygon v3/reference/tickers/{ticker}), not part
of the main pipeline. Current-classification only (no PIT sector history) --
used as a coarse sector-diversification filter for the C7 sleeve, not for
signal generation.

KNOWN DEFECT -- do not trust the nulls in the output. This call passes no `date`
parameter, so Polygon 404s every delisted ticker, and `fetch_one` maps both that
404 and any exception to `sic_code: null` -- identical to "no classification
exists". Result: the nulls are concentrated on exactly the delisted names, so
sp500_pit_sic.json alone is a survivor-only sector map (50.2% of the nq100 PIT
union came back Unknown). `backfill_delisted_sic.py` repairs this by re-querying
with an as-of date the ticker was listed; `sector_map.py` overlays the result.
Also note `todo` skips any ticker already present in the cache, so re-running
this script will NOT retry a bad null.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault(
    "SP500_DATA_ROOT",
    r"c:\Users\shard\Light Water Internship\market-data\SP500-Survivorship-bias-data-2004-2026",
)

from helpers.aws_utils import get_secret
from helpers.pit_universe import get_sp500_tickers_in_period

OUT_PATH = os.path.join(ROOT, "scripts", "sp500_pit_sic.json")
KEY = os.environ.get("POLYGON_API_KEY") or get_secret("POLYGON_API_KEY")


def fetch_one(ticker):
    try:
        r = requests.get(
            f"https://api.polygon.io/v3/reference/tickers/{ticker}",
            params={"apiKey": KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return ticker, None, None
        res = r.json().get("results", {}) or {}
        return ticker, res.get("sic_code"), res.get("sic_description")
    except Exception:
        return ticker, None, None


def main():
    tickers = sorted(get_sp500_tickers_in_period("2008-01-01", "2026-04-30", os.environ["SP500_DATA_ROOT"]))
    print(f"{len(tickers)} tickers to classify")

    existing = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH) as fh:
            existing = json.load(fh)
    todo = [t for t in tickers if t not in existing]
    print(f"{len(todo)} not yet cached")

    results = dict(existing)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_one, t): t for t in todo}
        done = 0
        for fut in as_completed(futs):
            ticker, sic_code, sic_desc = fut.result()
            results[ticker] = {"sic_code": sic_code, "sic_description": sic_desc}
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)}")

    with open(OUT_PATH, "w") as fh:
        json.dump(results, fh, indent=1, sort_keys=True)
    print(f"Wrote {OUT_PATH} ({len(results)} tickers)")


if __name__ == "__main__":
    main()
