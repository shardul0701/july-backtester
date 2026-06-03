from pathlib import Path

import pytest

from helpers.point_in_time import resolve_pit_portfolio, tickers_as_of


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_sp500_tickers_as_of_start_date_with_normalisation(tmp_path):
    root = tmp_path / "sp500_repo"
    _write_yaml(
        root / "src" / "sp500_ticker_history" / "sp500-ticker-changes-2020.yaml",
        """
year: 2020
tickers_on_Jan_1:
  - AAA
  - GOOG
  - PCLN
  - BRK.B
changes:
  '2020-02-01':
    difference:
      - AAA
    union:
      - CCC
  '2020-07-01':
    difference:
      - CCC
    union:
      - DDD
""",
    )

    config = {"sp500_pit_path": str(root)}

    jan_members = tickers_as_of("sp500", "2020-01-15", config)
    assert "BKNG" in jan_members
    assert "GOOGL" in jan_members
    assert "BRK-B" in jan_members
    assert "PCLN" not in jan_members

    june_members = tickers_as_of("s&p500", "2020-06-30", config)
    assert "AAA" not in june_members
    assert "CCC" in june_members
    assert "DDD" not in june_members


def test_nq100_tickers_as_of_uses_n100_yaml_name(tmp_path):
    root = tmp_path / "nq_repo"
    _write_yaml(
        root / "src" / "nasdaq_100_ticker_history" / "n100-ticker-changes-2021.yaml",
        """
year: 2021
tickers_on_Jan_1:
  - AAPL
  - MSFT
changes:
  '2021-03-10':
    difference:
      - MSFT
    union:
      - NVDA
""",
    )

    config = {"nq100_pit_path": str(root)}

    assert tickers_as_of("nq100", "2021-03-09", config) == ["AAPL", "MSFT"]
    assert tickers_as_of("nasdaq-100", "2021-03-10", config) == ["AAPL", "NVDA"]


def test_resolve_pit_portfolio_reads_config_start_date(tmp_path):
    root = tmp_path / "sp500_repo"
    _write_yaml(
        root / "src" / "sp500_ticker_history" / "sp500-ticker-changes-2022.yaml",
        """
year: 2022
tickers_on_Jan_1:
  - AAPL
changes: {}
""",
    )

    config = {"start_date": "2022-04-01", "sp500_pit_path": str(root)}

    assert resolve_pit_portfolio("pit:sp500", config) == ["AAPL"]
    assert resolve_pit_portfolio("sp500_pit", config) is None


def test_missing_pit_yaml_has_friendly_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="No PIT YAML found"):
        tickers_as_of("sp500", "1900-01-01", {"sp500_pit_path": str(tmp_path)})
