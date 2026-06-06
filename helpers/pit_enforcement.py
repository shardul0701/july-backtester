"""Daily point-in-time membership ENFORCEMENT (engine-safe, additive).

The PIT universe loaders (``sp500_pit`` / ``nq100_pit`` / ``pit:*``) return the
*union* of every historical member, then the engine runs all of them across the
whole period. A naive cross-sectional strategy can therefore select a name years
before it actually joined the index ("hold today's members back in 2010").

This module fixes that without touching the simulation engine, three ways:

1. ``membership_intervals(value, config)`` -> ``{ticker: [(start, end), ...]}`` the
   list of *contiguous membership spells* for each (price-normalised) ticker. A name
   that left and rejoined the index has two intervals, so the gap between them is
   represented explicitly (no longer silently filled in).

2. ``build_member_mask(index, intervals)`` -> boolean Series, ``True`` only on days
   that fall inside a membership spell. Warm-up bars (before the first join) and gap
   bars (between spells) are ``False``. main.py attaches this as a ``_pit_member``
   column; run_single_simulation forces the signal flat (-1) wherever it is False, so
   the engine NEVER trades a warm-up or gap bar even though those bars are kept in the
   frame for indicator continuity. This is the exact daily gating the span approach
   could not give.

3. ``trim_to_membership(df, span, warmup_days)`` slices a frame to the outer bound
   ``[first_join - warmup, last_leave]`` to bound data size; the ``_pit_member`` mask
   then does the fine-grained per-day gating on top.

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


# ----------------------------------------------------------------- intervals ---
def _sp500_intervals(start: str, end: str, repo: str) -> dict:
    """Per-ticker membership spells from the S&P change-event timeline.

    Returns ``{normalised_ticker: [(spell_start, spell_end), ...]}``. A removal
    closes the open spell at the change date; a later re-add opens a new spell,
    so gaps are represented as separate intervals rather than swallowed.
    """
    import pandas as pd
    from helpers.pit_universe import _load_sp500_yaml

    yaml_dir = Path(repo) / "src" / "sp500_ticker_history"
    if not yaml_dir.is_dir():
        return {}
    end_y = int(end[:4])

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

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    open_spell: dict[str, "pd.Timestamp"] = {}
    intervals: dict[str, list] = {}
    for t in current:
        open_spell[_norm(t)] = start_ts

    while i < len(events) and events[i][0] <= end:
        d, a, r = events[i]
        d_ts = pd.Timestamp(d)
        for t in r:                       # removals close the open spell
            n = _norm(t)
            if n in open_spell:
                intervals.setdefault(n, []).append((open_spell.pop(n), d_ts))
        for t in a:                       # additions open a new spell
            n = _norm(t)
            open_spell.setdefault(n, d_ts)
        i += 1
    for n, s in open_spell.items():       # still a member at period end
        intervals.setdefault(n, []).append((s, end_ts))
    return intervals


def _nq100_intervals(start: str, end: str, parquet_path: str,
                     gap_days: int = 10) -> dict:
    """Per-ticker membership spells from the NQ100 daily snapshot parquet.

    Consecutive member dates are grouped into a spell; a gap larger than
    ``gap_days`` calendar days between member dates starts a new spell, so a
    name that dropped out and came back is two intervals.
    """
    import pandas as pd
    if not os.path.isfile(parquet_path):
        return {}
    df = pd.read_parquet(parquet_path)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[(df["date"] >= start) & (df["date"] <= end)].sort_values("date")

    per: dict[str, list] = {}
    for d, tjson in zip(df["date"], df["tickers_json"]):
        try:
            tickers = json.loads(tjson)
        except Exception:
            continue
        ts = pd.Timestamp(d)
        for t in tickers:
            per.setdefault(_norm(t), []).append(ts)

    tol = pd.Timedelta(days=gap_days)
    out: dict[str, list] = {}
    for t, dates in per.items():
        dates = sorted(set(dates))
        spells = []
        s = prev = dates[0]
        for d in dates[1:]:
            if d - prev > tol:
                spells.append((s, prev))
                s = d
            prev = d
        spells.append((s, prev))
        out[t] = spells
    return out


def membership_intervals(value: str, config: dict | None = None) -> dict:
    """Return ``{normalised_ticker: [(start_ts, end_ts), ...]}`` membership spells
    for a PIT portfolio value, or ``{}`` for a non-PIT value / missing source."""
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
        return _sp500_intervals(start, end, repo) if repo else {}
    parquet = config.get("nq100_pit_path") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "nq100_membership.parquet")
    return _nq100_intervals(start, end, parquet)


def membership_spans(value: str, config: dict | None = None) -> dict:
    """Return ``{normalised_ticker: (first_ts, last_ts)}`` — the OUTER bound of
    each ticker's spells. Used only to bound data size via trim_to_membership;
    fine per-day gating uses build_member_mask on the full interval list."""
    out = {}
    for t, spells in membership_intervals(value, config).items():
        if spells:
            out[t] = (min(s for s, _ in spells), max(e for _, e in spells))
    return out


def build_member_mask(index, intervals_for_symbol):
    """Boolean Series over ``index``: ``True`` only on days inside a membership
    spell. Warm-up bars (before the first join) and gap bars (between spells) are
    ``False`` so the engine never trades them. ``intervals_for_symbol`` is the
    ``[(start, end), ...]`` list from membership_intervals()[symbol]."""
    import pandas as pd
    mask = pd.Series(False, index=index)
    if not intervals_for_symbol:
        return mask
    for s, e in intervals_for_symbol:
        mask |= (index >= pd.Timestamp(s)) & (index <= pd.Timestamp(e))
    return mask


def mask_signal(signal, member_mask):
    """Force the signal flat (-1 = exit/stay-flat) on every bar where the symbol
    is NOT an index member, so warm-up and gap bars are never traded. The engine
    is untouched; this just rewrites the signal it receives. Returns ``signal``
    unchanged if ``member_mask`` is None."""
    if member_mask is None:
        return signal
    m = member_mask.reindex(signal.index).fillna(False).astype(bool)
    return signal.where(m, -1)


def trim_to_membership(df, span, warmup_days: int = 400):
    """Slice ``df`` (DatetimeIndex) to ``[first - warmup_days, last]`` of a
    ``(first, last)`` outer span — bounds data size only. The per-day gating that
    actually keeps warm-up/gap bars un-traded is build_member_mask + mask_signal.
    Returns df unchanged if span is falsy."""
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
