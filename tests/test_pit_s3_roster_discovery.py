from pathlib import Path

import pandas as pd
import pytest

from helpers import pit_enforcement
from helpers import point_in_time


def _write_year(root: Path, index: str, year: int, tickers: list[str]):
    sub = "sp500_ticker_history" if index == "sp500" else "nasdaq_100_ticker_history"
    prefix = "sp500-ticker-changes" if index == "sp500" else "n100-ticker-changes"
    directory = root / "src" / sub
    directory.mkdir(parents=True, exist_ok=True)
    quoted = "\n".join(f"  - '{ticker}'" for ticker in tickers)
    (directory / f"{prefix}-{year}.yaml").write_text(
        f"tickers_on_Jan_1:\n{quoted}\nchanges: {{}}\n", encoding="utf-8"
    )


def test_year_loader_discovers_s3_roster_beside_parquet_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("SP500_DATA_ROOT", raising=False)
    price_dir = tmp_path / "parquet_data" / "data"
    roster = tmp_path / "parquet_data" / "pit" / "sp500"
    _write_year(roster, "sp500", 2026, ["SKYD"])
    path = point_in_time._find_year_yaml(
        "sp500", 2026, {"parquet_data_dir": str(price_dir)}
    )
    assert path.is_file()
    assert roster in path.parents


def test_daily_enforcement_discovers_s3_roster_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NQ100_DATA_ROOT", raising=False)
    price_dir = tmp_path / "parquet_data" / "data"
    roster = tmp_path / "parquet_data" / "pit" / "nq100"
    _write_year(roster, "nq100", 2004, ["AAPL"])
    intervals = pit_enforcement.membership_intervals(
        "pit:nq100",
        {"start_date": "2004-01-02", "end_date": "2004-01-30", "parquet_data_dir": str(price_dir)},
    )
    assert "AAPL" in intervals


@pytest.mark.parametrize("index", ["sp500", "nq100"])
@pytest.mark.parametrize("missing_year", [2005, 2006])
def test_daily_enforcement_skips_partial_mirror_and_preserves_removal(
    tmp_path, monkeypatch, caplog, index, missing_year,
):
    monkeypatch.delenv("SP500_DATA_ROOT", raising=False)
    monkeypatch.delenv("NQ100_DATA_ROOT", raising=False)
    partial = tmp_path / "pit" / index
    complete = tmp_path / "complete" / index
    for year in range(2004, 2007):
        _write_year(complete, index, year, ["XOM"])
        if year != missing_year:
            _write_year(partial, index, year, ["XOM"])
    sub = "sp500_ticker_history" if index == "sp500" else "nasdaq_100_ticker_history"
    prefix = "sp500-ticker-changes" if index == "sp500" else "n100-ticker-changes"
    (complete / "src" / sub / f"{prefix}-2005.yaml").write_text(
        "tickers_on_Jan_1: ['XOM']\nchanges:\n"
        "  '2005-06-01':\n    union: []\n    difference: ['XOM']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        point_in_time, "_candidate_roots", lambda *_: [partial, complete],
    )

    intervals = pit_enforcement.membership_intervals(
        f"pit:{index}", {"start_date": "2004-01-02", "end_date": "2006-12-29"},
    )

    assert intervals["XOM"] == [(pd.Timestamp("2004-01-02"), pd.Timestamp("2005-06-01"))]
    assert f"missing year {missing_year}" in caplog.text
    assert str(partial) in caplog.text
    assert "Re-sync" in caplog.text
    forced = pit_enforcement.build_forced_exit_mask(
        pd.DatetimeIndex(["2005-05-31", "2005-06-01"]),
        intervals["XOM"], "2006-12-29",
    )
    assert forced.iloc[-1]


def test_auto_discovery_requires_only_years_through_run_end(tmp_path, monkeypatch):
    monkeypatch.delenv("SP500_DATA_ROOT", raising=False)
    root = tmp_path / "pit" / "sp500"
    for year in (2004, 2005):
        _write_year(root, "sp500", year, ["XOM"])
    monkeypatch.setattr(point_in_time, "_candidate_roots", lambda *_: [root])

    intervals = pit_enforcement.membership_intervals(
        "pit:sp500", {"start_date": "2005-01-03", "end_date": "2005-12-30"},
    )

    assert intervals["XOM"] == [(pd.Timestamp("2005-01-03"), pd.Timestamp("2005-12-30"))]


def test_explicit_incomplete_roster_warns_about_missing_year(tmp_path, caplog):
    _write_year(tmp_path, "sp500", 2004, ["XOM"])

    pit_enforcement.membership_intervals(
        "pit:sp500",
        {"start_date": "2004-01-02", "end_date": "2005-12-30", "sp500_pit_path": str(tmp_path)},
    )

    assert "PIT membership history is incomplete" in caplog.text
    assert "sp500-ticker-changes-2005.yaml" in caplog.text
    assert "Re-sync" in caplog.text


def test_daily_enforcement_skips_empty_s3_mirror(tmp_path, monkeypatch):
    monkeypatch.delenv("SP500_DATA_ROOT", raising=False)
    empty_mirror = tmp_path / "pit" / "sp500"
    empty_mirror.mkdir(parents=True)
    valid_repo = tmp_path / "valid_sp500"
    _write_year(valid_repo, "sp500", 2004, ["AAPL"])
    monkeypatch.setattr(
        point_in_time, "_candidate_roots",
        lambda index, config: [empty_mirror, valid_repo],
    )

    intervals = pit_enforcement.membership_intervals(
        "pit:sp500",
        {"start_date": "2004-01-02", "end_date": "2004-01-30"},
    )

    assert "AAPL" in intervals
