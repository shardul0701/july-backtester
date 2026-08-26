"""
Regression tests for issue #315 — the cache read path discarded any entry whose
first bar lagged the *requested* start by > 30 days.

The cache is keyed by the requested start (it is in the filename), so a fixed
request always maps to the same file. A first bar later than the requested start
means EITHER the symbol listed after `start` (an IPO — re-fetching returns the
identical first bar) OR provider plan-capping — indistinguishable from the cache
alone. The old heuristic discarded both, so every late-listed symbol was
re-fetched from the API on EVERY run (a broad 2004 PIT universe re-fetches all of
its post-2004 listings each time). A genuine plan upgrade is recovered by the 24h
TTL, so the request-lag invalidation is removed.
"""

import numpy as np
import pandas as pd
import pytest

import helpers.caching as caching


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "CACHE_DIR", str(tmp_path))
    return tmp_path


def _df(start_index, n=250):
    idx = pd.date_range(start_index, periods=n, freq="D", tz="UTC")
    close = np.linspace(100.0, 120.0, n)
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6},
        index=idx,
    )


def test_late_listed_symbol_is_not_discarded(tmp_cache):
    # Requested 2004; symbol's data starts 2020 (listed 2020). The cache must be
    # SERVED, not discarded — re-fetching would return the same 2020 first bar.
    df = _df("2020-01-01")
    caching.set_cached_data(df, "ABNB", "2004-01-01", "2024-01-01", "day", 1)
    out = caching.get_cached_data("ABNB", "2004-01-01", "2024-01-01", "day", 1)
    assert out is not None, "late-listed symbol was wrongly discarded (issue #315)"
    assert out.index.min() == pd.Timestamp("2020-01-01", tz="UTC")


def test_data_covering_the_request_is_served(tmp_cache):
    df = _df("2004-01-05")  # within a few days of the request
    caching.set_cached_data(df, "SPY", "2004-01-01", "2024-01-01", "day", 1)
    out = caching.get_cached_data("SPY", "2004-01-01", "2024-01-01", "day", 1)
    assert out is not None
    assert len(out) == len(df)


def test_missing_file_is_a_cache_miss(tmp_cache):
    assert caching.get_cached_data("NONE", "2004-01-01", "2024-01-01", "day", 1) is None


def test_expired_ttl_is_a_cache_miss(tmp_cache, monkeypatch):
    df = _df("2020-01-01")
    caching.set_cached_data(df, "ABNB", "2004-01-01", "2024-01-01", "day", 1)
    # Force the file's mtime to > TTL ago.
    import os
    from datetime import datetime, timedelta
    fp = os.path.join(str(tmp_cache), "ABNB_2004-01-01_2024-01-01_day_1.parquet")
    old = (datetime.now() - timedelta(hours=caching.CACHE_TTL_HOURS + 1)).timestamp()
    os.utime(fp, (old, old))
    assert caching.get_cached_data("ABNB", "2004-01-01", "2024-01-01", "day", 1) is None
