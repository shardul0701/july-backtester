"""Task 9 — UnifiedMarketDataProvider.

The single read interface for the backtester. Reads data/market_data/merged/ ONLY.
The engine is agnostic to whether a row came from Norgate, Polygon, or a local
fallback; provenance (source / adjustment_factor / adjustment_method /
data_quality_status) is preserved internally and available via
get_with_provenance().

Drop-in data-provider compatibility: get_price_data(symbol, start, end, config)
returns Open/High/Low/Close/Volume with a tz-naive midnight DatetimeIndex named
'Datetime', matching the engine's fetcher contract.
"""
import os
import re
import glob
import json

import pandas as pd

from .pipeline import paths

_CANON = ["open", "high", "low", "close", "volume"]
_SUFFIX_RE = re.compile(r"-\d{6}$")  # delisted files carry a -YYYYMM suffix
_RENAME = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}


class UnifiedMarketDataProvider:
    def __init__(self, merged_dir=None, metadata_dir=None):
        self.merged_dir = merged_dir or paths.MERGED
        self.metadata_dir = metadata_dir or paths.METADATA
        self._class = None

    # ------------------------------------------------------------- internals ---
    def _resolve(self, symbol):
        """Return the merged parquet path for a requested symbol, or None.

        Tries (1) exact / upper / slash->dash variants, then (2) a date-suffixed
        delisted file (`{SYMBOL}-YYYYMM.parquet`), picking the most recent suffix.
        Step 2 lets PIT members that delisted (e.g. AABA->AABA-201910,
        DAY->DAY-202602) resolve to their real history.
        """
        cands = []
        for c in (symbol, symbol.upper(), symbol.replace("/", "-"),
                  symbol.upper().replace(".", "-"), symbol.upper().replace("-", ".")):
            if c not in cands:
                cands.append(c)
        for cand in cands:
            p = os.path.join(self.merged_dir, f"{cand}.parquet")
            if os.path.exists(p):
                return p
        # delisted fallback: most recent -YYYYMM suffix for this base ticker
        for cand in cands:
            hits = glob.glob(os.path.join(self.merged_dir, f"{cand}-*.parquet"))
            hits = [h for h in hits
                    if _SUFFIX_RE.search(os.path.splitext(os.path.basename(h))[0])]
            if hits:
                return sorted(hits)[-1]
        return None

    def _read(self, symbol):
        p = self._resolve(symbol)
        if p is None:
            return None
        return pd.read_parquet(p)

    @staticmethod
    def _slice(df, start_date, end_date):
        if start_date is not None:
            df = df[df.index >= pd.Timestamp(start_date)]
        if end_date is not None:
            df = df[df.index <= pd.Timestamp(end_date)]
        return df

    # ------------------------------------------------------------- public API ---
    def get_price_data(self, symbol, start_date=None, end_date=None, config=None):
        """Engine-compatible OHLCV (Open/High/Low/Close/Volume, Datetime index)."""
        df = self._read(symbol)
        if df is None or df.empty:
            return None
        df = self._slice(df, start_date, end_date)
        if df.empty:
            return None
        out = df[_CANON].rename(columns=_RENAME).copy()
        out.index = pd.DatetimeIndex(out.index).normalize()
        out.index.name = "Datetime"
        return out

    def load_prices(self, symbols, start_date=None, end_date=None):
        """Return {symbol: OHLCV DataFrame} for the symbols that exist."""
        out = {}
        for s in symbols:
            df = self.get_price_data(s, start_date, end_date)
            if df is not None:
                out[s] = df
        return out

    def load_required_asset(self, symbol, start_date=None, end_date=None):
        """Required commodity/bond/FX/index asset (same store, may be index-named)."""
        return self.get_price_data(symbol, start_date, end_date)

    def get_with_provenance(self, symbol, start_date=None, end_date=None):
        """Full canonical schema including source / adjustment_factor / method / status."""
        df = self._read(symbol)
        if df is None or df.empty:
            return None
        return self._slice(df, start_date, end_date)

    # ----------------------------------------------------- execution-safe API ---
    def get_raw_price_data(self, symbol, start_date=None, end_date=None, config=None):
        """RAW (broker-quote) OHLC, NOT total-return adjusted.

        Canonical prices are forward-adjusted to the 2026-04-22 anchor, so after a
        post-anchor split/dividend they diverge from what a broker (e.g. Alpaca)
        quotes. Use THIS for order submission / fill reconciliation, never
        get_price_data. raw = canonical / adjustment_factor (per row). Volume is
        passed through unchanged (price factor does not reconstruct raw volume).
        Returns Open/High/Low/Close/Volume with a Datetime index, or None.
        """
        df = self._read(symbol)
        if df is None or df.empty:
            return None
        df = self._slice(df, start_date, end_date)
        if df.empty:
            return None
        factor = pd.to_numeric(df.get("adjustment_factor", 1.0), errors="coerce").fillna(1.0)
        factor = factor.replace(0.0, 1.0)
        out = pd.DataFrame(index=df.index)
        for lc, cc in (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")):
            out[cc] = df[lc].astype("float64") / factor
        out["Volume"] = df["volume"].astype("float64")
        out.index = pd.DatetimeIndex(out.index).normalize()
        out.index.name = "Datetime"
        return out

    def get_execution_price(self, symbol, date, field="close"):
        """Scalar RAW price for `symbol` on the last bar on/before `date`.

        Convenience for the Alpaca runner / fill reconciliation. Returns a float
        (broker-comparable) or None if no bar exists on/before the date.
        """
        df = self._read(symbol)
        if df is None or df.empty:
            return None
        df = df[df.index <= pd.Timestamp(date)]
        if df.empty:
            return None
        row = df.iloc[-1]
        factor = row.get("adjustment_factor", 1.0)
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            factor = 1.0
        if not factor:
            factor = 1.0
        return float(row[field]) / factor

    def load_universe(self, date, universe_name):
        """Symbols in a named ticker list that have data on/through `date`.

        universe_name may be a tickers_to_scan/*.json filename or a bucket name.
        """
        date = pd.Timestamp(date)
        symbols = self._universe_symbols(universe_name)
        live = []
        for s in symbols:
            p = self._resolve(s)
            if p is None:
                continue
            # cheap last-date check via the parquet (already small)
            try:
                idx = pd.read_parquet(p, columns=["close"]).index
            except Exception:
                continue
            if idx.min() <= date <= idx.max() or idx.max() >= date:
                live.append(s)
        return sorted(live)

    def _universe_symbols(self, universe_name):
        # named JSON list under tickers_to_scan/
        jpath = os.path.join(paths.ROOT, "tickers_to_scan", universe_name)
        if os.path.exists(jpath):
            with open(jpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else list(data)
        # otherwise treat as a classification bucket
        if self._class is None:
            self._class = pd.read_csv(
                os.path.join(self.metadata_dir, "symbol_classification.csv"),
                keep_default_na=False, na_values=[""])
        sub = self._class[self._class["bucket"] == universe_name]
        return sub["symbol"].astype(str).tolist()

    def available_symbols(self):
        return sorted(f[:-8] for f in os.listdir(self.merged_dir) if f.endswith(".parquet"))

    # --------------------------------------------------------- quality screen ---
    def quality_status(self, symbol):
        """Last-row data_quality_status for a symbol, or None if not found.
        Values: ok / review_no_patch / insufficient_history / identity_review /
        flagged."""
        df = self._read(symbol)
        if df is None or df.empty or "data_quality_status" not in df.columns:
            return None
        return str(df["data_quality_status"].iloc[-1])

    def filter_universe(self, symbols,
                        exclude_statuses=("insufficient_history", "review_no_patch",
                                          "identity_review"),
                        min_bars=0, min_avg_dollar_volume=0.0, lookback=60):
        """Screen a symbol list for backtest fitness. Returns (kept, dropped) where
        dropped is ``{symbol: reason}``.

        - exclude_statuses: drop quarantined data. Defaults exclude new listings
          with too little history (#8), active Norgate names with no Polygon patch
          / stale at the anchor (#6), and identity-flagged fail-closed names (#7).
          Pass () to keep everything.
        - min_bars: drop series shorter than this (long-lookback strategies).
        - min_avg_dollar_volume: drop illiquid names (mean close*volume over the
          last `lookback` bars) — mitigates micro-cap anomalies (#12).
        Only the requested symbols are opened (cheap for a few-hundred-name
        universe; do not call on all 35k)."""
        exclude_statuses = set(exclude_statuses or ())
        kept, dropped = [], {}
        for s in symbols:
            df = self.get_with_provenance(s)
            if df is None or df.empty:
                dropped[s] = "no data"
                continue
            status = str(df["data_quality_status"].iloc[-1]) if "data_quality_status" in df else "ok"
            if status in exclude_statuses:
                dropped[s] = f"status={status}"
                continue
            if min_bars and len(df) < min_bars:
                dropped[s] = f"bars={len(df)}<{min_bars}"
                continue
            if min_avg_dollar_volume and {"close", "volume"} <= set(df.columns):
                tail = df.tail(lookback)
                adv = float((tail["close"] * tail["volume"]).mean())
                if adv < min_avg_dollar_volume:
                    dropped[s] = f"adv=${adv:,.0f}<${min_avg_dollar_volume:,.0f}"
                    continue
            kept.append(s)
        return kept, dropped


# module-level singleton for drop-in use as a data provider
_PROVIDER = None


def _provider():
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = UnifiedMarketDataProvider()
    return _PROVIDER


def get_price_data(symbol, start_date=None, end_date=None, config=None):
    """Module-level fetcher matching services.* signature."""
    return _provider().get_price_data(symbol, start_date, end_date, config)
