"""Daily point-in-time membership ENFORCEMENT (engine-safe, additive).

The PIT universe loaders (``sp500_pit`` / ``nq100_pit`` / ``pit:*``) return the
*union* of every historical member, then the engine runs all of them across the
whole period. A naive cross-sectional strategy can therefore select a name years
before it actually joined the index ("hold today's members back in 2010").

This module fixes that without touching the simulation engine, two ways:

1. ``membership_spans(value, config)`` -> ``{ticker: (first, last)}`` the first and
   last date each (price-normalised) ticker was an index member within the period.

2. ``trim_to_membership(df, span, warmup_days)`` slices a symbol's price frame to
   ``[first_member_date - warmup, last_member_date]`` so the engine never sees it
   outside its membership era. main.py applies this in the data-prep loop when
   ``pit_enforce_daily`` is set. The warmup buffer preserves indicator continuity
   so a name can be traded on the day it joins.

   This is a *span* approximation: a ticker with two separate membership spells
   has the gap between them included. For exact per-day gating, strategy authors
   can consult ``daily_membership_mask(value, config)`` -> ``{date: frozenset}``.

Tickers are normalised to the price-store ticker (UTX->RTX, ...) via
helpers.point_in_time.normalise_pit_ticker so spans key on the same symbol the
provider serves.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _norm(t):
    try:
        from helpers.point_in_time import normalise_pit_ticker
        return normalise_pit_ticker(t)
    except Exception:
        return str(t).strip().upper()


def _kind(value: str) -> str | None:
    v = str(value).lower()
    if v in ("sp500_pit", "pit:sp500", "pit:s&p500"):
        return "sp500"
    if v in ("nq100_pit", "pit:nq100", "pit:nasdaq100", "pit:ndx"):
        return "nq100"
    return None


# --------------------------------------------------------------------- spans ---
def _sp500_spans(start: str, end: str, repo: str) -> dict:
    import pandas as pd
    from helpers.pit_universe import _load_sp500_yaml

    yaml_dir = Path(repo) / "src" / "sp500_ticker_history"
    if not yaml_dir.is_dir():
        return {}
    start_y, end_y = int(start[:4]), int(end[:4])

    # flat chronological change timeline + a seed membership at `start`
    events = []          # (date_str, added_set, removed_set)
    seed = set()
    for year in range(2004, end_y + 1):
        path = yaml_dir / f"sp500-ticker-changes-{year}.yaml"
        if not path.exists():
            continue
        data = _load_sp500_yaml(path)
        if not data:
            continue
        if year == 2004:
            seed = {str(t) for t in (data.get("tickers_on_Jan_1") or [])}
        for d in sorted((data.get("changes") or {}).keys()):
            e = data["changes"][d]
            events.append((str(d),
                           {str(t) for t in (e.get("union") or [])},
                           {str(t) for t in (e.get("difference") or [])}))
    events.sort(key=lambda x: x[0])

    # advance seed to `start`
    current = set(seed)
    i = 0
    while i < len(events) and events[i][0] < start:
        _, a, r = events[i]
        current = (current - r) | a
        i += 1

    spans: dict[str, list] = {}

    def touch(t, d):
        n = _norm(t)
        ts = pd.Timestamp(d)
        if n not in spans:
            spans[n] = [ts, ts]
        else:
            if ts < spans[n][0]:
                spans[n][0] = ts
            if ts > spans[n][1]:
                spans[n][1] = ts

    for t in current:
        touch(t, start)
    while i < len(events) and events[i][0] <= end:
        d, a, r = events[i]
        for t in current:
            touch(t, d)
        current = (current - r) | a
        for t in a:
            touch(t, d)
        i += 1
    for t in current:
        touch(t, end)
    return {k: (v[0], v[1]) for k, v in spans.items()}


def _nq100_spans(start: str, end: str, parquet_path: str) -> dict:
    import pandas as pd
    if not os.path.isfile(parquet_path):
        return {}
    df = pd.read_parquet(parquet_path)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    spans: dict[str, list] = {}
    for d, tjson in zip(df["date"], df["tickers_json"]):
        try:
            tickers = json.loads(tjson)
        except Exception:
            continue
        ts = pd.Timestamp(d)
        for t in tickers:
            n = _norm(t)
            if n not in spans:
                spans[n] = [ts, ts]
            else:
                if ts < spans[n][0]:
                    spans[n][0] = ts
                if ts > spans[n][1]:
                    spans[n][1] = ts
    return {k: (v[0], v[1]) for k, v in spans.items()}


def membership_spans(value: str, config: dict | None = None) -> dict:
    """Return ``{normalised_ticker: (first_ts, last_ts)}`` for a PIT portfolio
    value, or ``{}`` for a non-PIT value / missing source."""
    config = config or {}
    kind = _kind(value)
    if kind is None:
        return {}
    start = config.get("start_date")
    end = config.get("end_date")
    if not start or not end:
        return {}
    if kind == "sp500":
        repo = config.get("sp500_pit_path") or os.environ.get("SP500_DATA_ROOT", "")
        return _sp500_spans(start, end, repo) if repo else {}
    parquet = config.get("nq100_pit_path") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "nq100_membership.parquet")
    return _nq100_spans(start, end, parquet)


def trim_to_membership(df, span, warmup_days: int = 400):
    """Slice ``df`` (DatetimeIndex) to ``[first - warmup_days, last]`` of a
    ``(first, last)`` span. Returns df unchanged if span is falsy."""
    if df is None or span is None:
        return df
    import pandas as pd
    first, last = span
    lo = pd.Timestamp(first) - pd.Timedelta(days=warmup_days)
    hi = pd.Timestamp(last)
    return df[(df.index >= lo) & (df.index <= hi)]


def daily_membership_mask(value: str, config: dict | None = None) -> dict:
    """Exact per-day membership: ``{date_str: frozenset(normalised_tickers)}``.
    For strategy authors who want true daily gating instead of the span trim."""
    config = config or {}
    kind = _kind(value)
    if kind is None:
        return {}
    start, end = config.get("start_date"), config.get("end_date")
    if not (start and end):
        return {}
    import pandas as pd
    if kind == "nq100":
        parquet = config.get("nq100_pit_path") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "nq100_membership.parquet")
        if not os.path.isfile(parquet):
            return {}
        df = pd.read_parquet(parquet)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        out = {}
        for d, tjson in zip(df["date"], df["tickers_json"]):
            try:
                out[d] = frozenset(_norm(t) for t in json.loads(tjson))
            except Exception:
                pass
        return out
    # sp500: snapshot on each calendar date the data covers
    repo = config.get("sp500_pit_path") or os.environ.get("SP500_DATA_ROOT", "")
    if not repo:
        return {}
    from helpers.pit_universe import build_sp500_pit_snapshots
    dates = [d.strftime("%Y-%m-%d")
             for d in pd.bdate_range(start, end)]
    snaps = build_sp500_pit_snapshots(dates, repo)
    return {d: frozenset(_norm(t) for t in members) for d, members in snaps.items()}
