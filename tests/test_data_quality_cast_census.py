"""tests/test_data_quality_cast_census.py

Pins the corpus-independent parts of scripts/data_quality_cast_census.py (#389):
the un-cast variant is built from the *current* helpers/data_quality.py, it
differs only by the two casts, and the scoring/reporting arithmetic is right on
frames whose answer is known. The census counts themselves depend on the
corpus and are not tested here.
"""

import importlib.util
import os

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "data_quality_cast_census",
    os.path.join(ROOT, "scripts", "data_quality_cast_census.py"))
census = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(census)

MODULE = census.DEFAULT_MODULE


def _frame(n=2000, zero_vol=0):
    idx = pd.bdate_range("2000-01-03", periods=n)
    px = 100 + np.arange(n) * 0.01
    df = pd.DataFrame({"Open": px, "High": px + 1, "Low": px - 1,
                       "Close": px, "Volume": 1e6}, index=idx)
    if zero_vol:
        df.iloc[:zero_vol, 4] = 0
    return df


class TestVariantConstruction:
    def test_substitutions_still_match_the_committed_module(self):
        # Fails loudly (SystemExit) the day data_quality.py's casts change --
        # which is when this script must be updated or retired.
        committed, un_cast, cap = census.build_variant_sources(MODULE, None)
        assert committed == MODULE.read_text(encoding="utf-8")
        assert cap == census.committed_check6_cap(committed)
        assert census.CAST_CHECK5[0] not in un_cast
        assert census.CAST_CHECK6[0].format(cap=cap) not in un_cast

    def test_variant_differs_only_by_the_two_casts(self):
        committed, un_cast, _ = census.build_variant_sources(MODULE, None)
        diff = [(a, b) for a, b in zip(committed.splitlines(),
                                       un_cast.splitlines()) if a != b]
        assert len(committed.splitlines()) == len(un_cast.splitlines())
        assert len(diff) == 2
        assert all("int(" in a and "int(" not in b for a, b in diff)

    def test_cap_override_rewrites_both_copies(self):
        committed, un_cast, cap = census.build_variant_sources(MODULE, 37)
        assert cap == 37
        assert census.committed_check6_cap(committed) == 37
        assert census.CAST_CHECK6[1].format(cap=37) in un_cast

    def test_missing_target_is_fatal(self):
        with pytest.raises(SystemExit):
            census._sub_once("nothing here", "int(pct)", "pct", "x")

    def test_negative_cap_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            census.main(["--cap", "-5", "--corpus", str(tmp_path)])


class TestScoring:
    @pytest.fixture(autouse=True)
    def _mods(self):
        census._init_worker(str(MODULE), None)
        yield
        census._MODS.clear()

    def test_zero_volume_fraction_is_charged_un_cast(self, tmp_path):
        # 70 / 2000 = 3.5% zero-volume bars: int() charges 3, un-cast 3.5.
        p = tmp_path / "ZV.parquet"
        _frame(zero_vol=70).to_parquet(p)
        row = census._score_one(str(p))
        assert row["score_int"] - row["score_float"] == pytest.approx(0.5)

    def test_clean_series_does_not_move(self, tmp_path):
        p = tmp_path / "CLEAN.parquet"
        _frame().to_parquet(p)
        row = census._score_one(str(p))
        assert row["score_int"] == row["score_float"]

    def test_corrupt_file_is_a_row_not_a_crash(self, tmp_path):
        p = tmp_path / "BAD.parquet"
        p.write_bytes(b"not parquet")
        row = census._score_one(str(p))
        assert set(row) == {"symbol", "error"}


class TestReporting:
    ROWS = [
        {"symbol": "A", "bars": 2000, "score_int": 80.0, "score_float": 79.9},
        {"symbol": "B", "bars": 2000, "score_int": 80.0, "score_float": 80.0},
        {"symbol": "C", "bars": 100, "score_int": 80.4, "score_float": 79.95},
        {"symbol": "D", "bars": 2000, "score_int": 70.0, "score_float": 70.0},
        {"symbol": "E", "error": "OSError: x"},
    ]

    def test_gate_delta_splits_at_gate_from_above(self):
        scored = [r for r in self.ROWS if "error" not in r]
        assert census.gate_delta(scored, 80.0) == (1, 3, 1, 1)

    def test_summarise_counts(self):
        out = census.summarise(self.ROWS, 80.0, 20)
        assert "Series scored        : 4   (1 unreadable)" in out
        assert "demoted below the gate un-cast   : 1" in out
        assert "At 80 the delta splits 1 + 1" in out

    def test_json_rounding_is_writer_only(self):
        r = census._round_row({"symbol": "X", "score_float": 79.99999999})
        assert r["score_float"] == 80.0
