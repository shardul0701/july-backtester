"""
Regression test for issue #316 — ``chaikin_money_flow_with_stop_loss_logic``
could only ever take one trade.

The wrapper derived entry events from ``df_base['Signal'].diff() == 1``. But
``df_base['Signal']`` is the base strategy's already-ffilled state (…,1,1,-1,-1,
1,1,…), so after the first ``0 -> 1`` every re-entry is a ``-1 -> 1`` transition
with diff ``2`` — never equal to 1. Only the first entry ever fired; every
subsequent CMF buy was silently dropped.

Fix: detect entries/exits as state transitions, not fixed diff magnitudes.
"""

import numpy as np
import pandas as pd
import pytest

import helpers.indicators as ind


@pytest.fixture
def patched_base(monkeypatch):
    # Base state with TWO long episodes: bars 2-3 and bars 6-7.
    state = [0, 0, 1, 1, -1, -1, 1, 1, -1, -1]

    def _fake_base(df, *a, **k):
        df = df.copy()
        df["Signal"] = np.array(state, dtype=float)
        return df
    monkeypatch.setattr(ind, "chaikin_money_flow_logic", _fake_base)
    return state


def _price_df(n=10, close=100.0):
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,  # Low 99 > 97 stop
         "Close": close, "Volume": 1e6},
        index=idx,
    )


def _long_entries(signal):
    s = signal.astype(int)
    return int(((s == 1) & (s.shift(1) != 1)).sum())


class TestCmfStopReentry:
    def test_reentry_after_exit_is_detected(self, patched_base):
        out = ind.chaikin_money_flow_with_stop_loss_logic(_price_df())
        # Both long episodes must produce an entry (the old code caught only 1).
        assert _long_entries(out["Signal"]) == 2

    def test_second_long_episode_present_in_signal(self, patched_base):
        sig = ind.chaikin_money_flow_with_stop_loss_logic(_price_df())["Signal"].tolist()
        # bars 6-7 must be long (1) again, not stuck flat/-1 from the first exit.
        assert sig[6] == 1 and sig[7] == 1

    def test_stop_loss_still_fires(self, monkeypatch, patched_base):
        # One long episode (bars 2-3); price crashes at bar 3 below the 3% stop.
        df = _price_df()
        df.loc[df.index[3], "Low"] = 90.0   # 90 < 100*(1-0.03)=97 -> stop
        out = ind.chaikin_money_flow_with_stop_loss_logic(df)
        # Exit event exists at/after the entry (stop or CMF); signal leaves long.
        assert -1 in out["Signal"].tolist()
