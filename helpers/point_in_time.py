"""Point-in-time index membership helpers.

This module is the small public API used by the main backtester for
survivorship-bias-free portfolio definitions such as ``pit:nq100`` and
``pit:sp500``. It intentionally keeps static JSON portfolios unchanged.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]

INDEX_ALIASES = {
    "nq100": "nq100",
    "nasdaq100": "nq100",
    "nasdaq-100": "nq100",
    "ndx": "nq100",
    "sp500": "sp500",
    "s&p500": "sp500",
    "s&p-500": "sp500",
    "s&p_500": "sp500",
}

INDEX_DIR_NAMES = {
    "nq100": ["nq100", "nasdaq100", "nasdaq_100"],
    "sp500": ["sp500", "sp-500", "s-and-p-500"],
}

INDEX_FILE_PREFIXES = {
    "nq100": ["n100-ticker-changes", "nasdaq100-ticker-changes"],
    "sp500": ["sp500-ticker-changes"],
}

PIT_TICKER_NORMALISATION = {
    # Maps a historical PIT-membership ticker -> the ticker the merged/ price
    # store actually carries (Norgate back-fills the *current* ticker, so a name
    # that was "UTX" in 2015 lives in the store as "RTX"). Every target below was
    # verified to exist in merged/ (exact file or date-suffixed delisted file);
    # see scripts/diagnose_pipeline_issues.py. This lifts PIT->price coverage to
    # ~99% S&P / ~99% NQ100. Names with NO surviving file (bankruptcies such as
    # ENDP/JCP/SIVB/TUP/WIN/MERQE/QRTEA/RHAT and Norgate-snapshot gaps
    # MMC/ATGE/PARA) are intentionally absent — no alias can recover them.
    "PCLN": "BKNG", "HANS": "MNST", "GOOG": "GOOGL",
    # --- S&P 500 renames / mergers (old -> surviving ticker) ---
    "ABC": "COR", "ADS": "BFH", "ANTM": "ELV", "BHGE": "BKR", "BLL": "BALL",
    "CCE": "CCEP", "CDAY": "DAY", "CHK": "EXE", "COG": "CTRA", "CTL": "LUMN",
    "DDR": "SITC", "DISCA": "WBD", "DNR": "DEN", "DWDP": "DD", "ESV": "VAL",
    "FBHS": "FBIN", "FII": "FHI", "FLT": "CPAY", "GPS": "GAP", "HFC": "DINO",
    "HRS": "LHX", "JEC": "J", "KORS": "CPRI", "MYL": "VTRS", "NLOK": "GEN",
    "PKI": "RVTY", "RE": "EG", "SYMC": "GEN", "TMK": "GL", "UTX": "RTX",
    "WFT": "WFRD", "WLTW": "WTW", "WYND": "TNL",
    # --- Nasdaq-100 renames / mergers ---
    "AEOS": "AEO", "IVGN": "LIFE", "JDSU": "VIAV", "KFT": "MDLZ", "UAUA": "UAL",
    "VIP": "VEON", "YHOO": "AABA",
}


def _canonical_index(index: str) -> str:
    key = str(index).strip().lower()
    if key not in INDEX_ALIASES:
        raise ValueError(f"Unknown PIT index '{index}'. Expected one of: nq100, sp500")
    return INDEX_ALIASES[key]


def normalise_pit_ticker(symbol: str, date: str | None = None) -> str:
    """Return the ticker format expected by local price providers.

    ``date`` is accepted for future date-aware mappings; current mappings are
    stable aliases used by the available PIT repositories.
    """
    sym = str(symbol).strip().upper().replace(".", "-")
    return PIT_TICKER_NORMALISATION.get(sym, sym)


def _candidate_roots(index: str, config: dict | None = None) -> list[Path]:
    config = config or {}
    roots: list[Path] = []

    if index == "sp500":
        for key in ("sp500_pit_path", "sp500_data_root"):
            if config.get(key):
                roots.append(Path(config[key]))
        if os.environ.get("SP500_DATA_ROOT"):
            roots.append(Path(os.environ["SP500_DATA_ROOT"]))
    else:
        for key in ("nq100_pit_path", "nq100_data_root"):
            if config.get(key):
                roots.append(Path(config[key]))
        if os.environ.get("NQ100_DATA_ROOT"):
            roots.append(Path(os.environ["NQ100_DATA_ROOT"]))

    pit_base = ROOT / "tickers_to_scan" / "point_in_time"
    for name in INDEX_DIR_NAMES[index]:
        roots.append(pit_base / name)

    if index == "nq100":
        roots.append(ROOT / "NQ-SB" / "nasdaq100_point_in_time_universe_repo")
    roots.append(ROOT)

    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        resolved = root.expanduser()
        if resolved not in seen:
            out.append(resolved)
            seen.add(resolved)
    return out


def _yaml_dirs_for_root(root: Path, index: str) -> Iterable[Path]:
    yield root
    if index == "sp500":
        yield root / "src" / "sp500_ticker_history"
    else:
        yield root / "src" / "nasdaq_100_ticker_history"
        yield root / "src" / "nasdaq100_ticker_history"


def _find_year_yaml(index: str, year: int, config: dict | None = None) -> Path:
    prefixes = INDEX_FILE_PREFIXES[index]
    for root in _candidate_roots(index, config):
        for directory in _yaml_dirs_for_root(root, index):
            if not directory.is_dir():
                continue
            for prefix in prefixes:
                path = directory / f"{prefix}-{year}.yaml"
                if path.exists():
                    return path
    raise FileNotFoundError(
        f"No PIT YAML found for {index} {year}. Configure "
        f"{'SP500_DATA_ROOT' if index == 'sp500' else 'NQ100_DATA_ROOT'} "
        "or place files under tickers_to_scan/point_in_time/."
    )


@lru_cache(maxsize=512)
def _load_year_yaml_cached(path_str: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required for point-in-time universe loading.") from exc

    with open(path_str, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_year_yaml(path: Path) -> dict:
    return _load_year_yaml_cached(str(path.resolve()))


def tickers_as_of(index: str, date: str, config: dict | None = None) -> list[str]:
    """Return the PIT members of ``index`` on ``date``.

    Parameters
    ----------
    index:
        ``"nq100"`` or ``"sp500"``.
    date:
        ISO date string. The membership includes changes effective on that date.
    config:
        Optional backtester config. Recognised path keys:
        ``sp500_pit_path`` and ``nq100_pit_path``.
    """
    idx = _canonical_index(index)
    qdate = str(date)[:10]
    year = int(qdate[:4])
    path = _find_year_yaml(idx, year, config)
    data = _load_year_yaml(path)

    members = {normalise_pit_ticker(t, qdate) for t in (data.get("tickers_on_Jan_1") or [])}
    changes = data.get("changes") or {}
    for change_date in sorted(str(d) for d in changes.keys()):
        if change_date > qdate:
            break
        entry = changes[change_date] or {}
        removed = {normalise_pit_ticker(t, change_date) for t in (entry.get("difference") or [])}
        added = {normalise_pit_ticker(t, change_date) for t in (entry.get("union") or [])}
        members = (members - removed) | added

    return sorted(members)


def resolve_pit_portfolio(value: str, config: dict) -> list[str] | None:
    """Resolve ``pit:<index>`` portfolio values; return ``None`` otherwise."""
    if not isinstance(value, str) or not value.startswith("pit:"):
        return None
    index = value.split(":", 1)[1]
    return tickers_as_of(index, config["start_date"], config)
