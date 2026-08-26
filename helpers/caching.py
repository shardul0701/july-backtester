# helpers/caching.py (Corrected for safe filenames)

import os
import pandas as pd
from datetime import datetime, timedelta
import logging
import re # Import the regular expressions module

logger = logging.getLogger(__name__)

# --- CONFIGURABLE SETTINGS ---
CACHE_DIR = "data_cache"
CACHE_TTL_HOURS = 24 

os.makedirs(CACHE_DIR, exist_ok=True)

def _sanitize_filename(symbol: str) -> str:
    """Replaces characters invalid for filenames with an underscore."""
    # This regex will replace any character that is NOT a letter, digit, hyphen, or underscore.
    return re.sub(r'[^a-zA-Z0-9_-]', '_', symbol)

def get_cached_data(symbol: str, start: str, end: str, timeframe: str, multiplier: int) -> pd.DataFrame | None:
    """Checks for and loads a DataFrame from a local Parquet cache."""
    # Sanitize the symbol for use in a filename
    safe_symbol = _sanitize_filename(symbol)

    end_date_str = datetime.now().strftime('%Y-%m-%d') if end == datetime.now().strftime('%Y-%m-%d') else end
    
    # Use the sanitized symbol to create the filename
    filename = f"{safe_symbol}_{start}_{end_date_str}_{timeframe}_{multiplier}.parquet"
    filepath = os.path.join(CACHE_DIR, filename)

    if os.path.exists(filepath):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
        if datetime.now() - file_mod_time < timedelta(hours=CACHE_TTL_HOURS):
            logger.debug(f"  -> Cache HIT for '{symbol}'. Loading from '{filepath}'.")
            try:
                df = pd.read_parquet(filepath)
                # The cache is keyed by the REQUESTED start (it is in the
                # filename), so a fixed request always maps to this one file. If
                # the cached data begins later than the requested start it is
                # EITHER a symbol that listed after `start` (an IPO — re-fetching
                # returns the identical first bar) OR provider plan-capping. We
                # cannot distinguish these from the cache alone, and the previous
                # heuristic (discard when the start lags the request by > 30 days)
                # mis-fired on EVERY late-listed symbol, forcing a full API
                # re-fetch of it on every run (issue #315). Do NOT invalidate on
                # request-lag — a genuine plan upgrade is recovered by the 24h TTL
                # (or by clearing the cache dir). Surface a large lag as an INFO
                # so plan-capping stays visible without thrashing the cache.
                try:
                    requested_start = pd.Timestamp(start).tz_localize("UTC")
                    cache_start = df.index.min()
                    if getattr(cache_start, "tzinfo", None) is None:
                        cache_start = cache_start.tz_localize("UTC")
                    lag_days = (cache_start - requested_start).days
                    if lag_days > 30:
                        logger.info(
                            f"  -> Cache for '{symbol}' starts {cache_start.date()}, "
                            f"{lag_days} days after requested {start} — expected for a "
                            f"symbol listed after {start}. If you upgraded data plans, the "
                            f"24h TTL will refresh it (or clear '{CACHE_DIR}/')."
                        )
                except Exception:
                    pass  # visibility only — never fail a cache read on this
                return df
            except Exception as e:
                logger.warning(f"Could not read cache file '{filepath}'. Will re-fetch. Error: {e}")
                return None
    
    logger.debug(f"  -> Cache MISS for '{symbol}'.")
    return None

def set_cached_data(df: pd.DataFrame, symbol: str, start: str, end: str, timeframe: str, multiplier: int):
    """Saves a DataFrame to the local Parquet cache."""
    # Sanitize the symbol for use in a filename
    safe_symbol = _sanitize_filename(symbol)

    end_date_str = datetime.now().strftime('%Y-%m-%d') if end == datetime.now().strftime('%Y-%m-%d') else end
    
    # Use the sanitized symbol to create the filename
    filename = f"{safe_symbol}_{start}_{end_date_str}_{timeframe}_{multiplier}.parquet"
    filepath = os.path.join(CACHE_DIR, filename)
    try:
        df.to_parquet(filepath)
        logger.debug(f"  -> Saved '{symbol}' to cache at '{filepath}'.")
    except Exception as e:
        logger.error(f"Failed to write to cache file '{filepath}'. Error: {e}")