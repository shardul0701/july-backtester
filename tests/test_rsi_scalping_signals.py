"""
Regression tests for issue #311 — ``rsi_scalping_logic`` produced phantom long
entries.

The old implementation built a stateful series (1=long, -1=short, 0=flat), then
converted it to events with ``signals.diff()`` + ``.replace(-2,-1).replace(2,1)``
+ ``ffill``. A short round-trip ``0 -> -1 -> 0`` produces diffs ``[-1, +1]``; the
``+1`` on the short-COVER bar survived the replaces, was read by the engine as
**enter long**, and was forward-filled — so every overbought-fade episode
silently opened a long the strategy never intended. The short entries were also
mis-encoded as ``-1`` (engine cover) rather than ``-2`` (engine short entry), so
they were inert.

Fixed: emit engine-convention event signals directly from the state machine —
``1`` long entry, ``-1`` long exit / short cover, ``-2`` short entry, ``0``
otherwise.

RSI is injected via a monkeypatched ``calculate_rsi`` so the crossover events are
deterministic and independent of price-to-RSI arithmetic.
"""

import numpy as np
import pandas as pd
import pytest

import helpers.indicators as ind


# RSI path (per bar) engineered to fire exactly one of each event:
#   bar2  buy_entry  (prev 15 < 20, cur 25 >= 20)          -> long entry   (+1)
#   bar4  buy_exit   (prev 45 < 50, cur 55 >= 50)          -> long exit    (-1)
#   bar6  sell_entry (prev 85 > 80, cur 75 <= 80)          -> short entry  (-2)
#   bar8  sell_exit  (prev 55 > 50, cur 45 <= 50)          -> short cover  (-1)
_RSI = [25.0, 15.0, 25.0, 45.0, 55.0, 85.0, 75.0, 55.0, 45.0, 45.0]


@pytest.fixture
def patched_rsi(monkeypatch):
    def _fake_calc(df, length=14):
        df = df.copy()
        df[f"RSI_{length}"] = np.array(_RSI, dtype=float)
        return df
    monkeypatch.setattr(ind, "calculate_rsi", _fake_calc)


def _price_df():
    idx = pd.date_range("2024-01-02 09:30", periods=len(_RSI), freq="1min")
    close = np.linspace(100.0, 101.0, len(_RSI))
    return pd.DataFrame(
        {"Open": close, "High": close * 1.001, "Low": close * 0.999,
         "Close": close, "Volume": 1000.0},
        index=idx,
    )


class TestRsiScalpingSignals:
    def test_no_phantom_long_after_short_cover(self, patched_rsi):
        out = ind.rsi_scalping_logic(_price_df())
        sig = out["Signal"].tolist()
        # The one and only long lives at bar 2 (entry) .. bar 4 (exit).
        # After the short cover at bar 8 there must be NO +1 (the old bug
        # forward-filled a phantom long from bar 8 onward).
        assert sig[8] == -1, "short cover must be -1, not a phantom long"
        assert sig[9] == 0, "no phantom long should persist after the cover"
        assert 1 not in sig[5:], f"phantom long present after bar 4: {sig}"

    def test_long_entry_and_exit_events(self, patched_rsi):
        sig = ind.rsi_scalping_logic(_price_df())["Signal"].tolist()
        assert sig[2] == 1     # long entry
        assert sig[4] == -1    # long exit

    def test_short_entry_uses_minus_two(self, patched_rsi):
        sig = ind.rsi_scalping_logic(_price_df())["Signal"].tolist()
        assert sig[6] == -2    # engine short-entry convention (was inert -1 before)

    def test_full_event_sequence(self, patched_rsi):
        sig = ind.rsi_scalping_logic(_price_df())["Signal"].tolist()
        assert sig == [0, 0, 1, 0, -1, 0, -2, 0, -1, 0]

    def test_signal_column_length_preserved(self, patched_rsi):
        df = _price_df()
        out = ind.rsi_scalping_logic(df)
        assert len(out["Signal"]) == len(df)
        assert "Signal" in out.columns
