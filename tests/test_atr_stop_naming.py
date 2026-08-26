"""
Regression tests for issue #309 — the documented ATR stop config
(`{"type": "atr", "multiplier": 2.0}`, no ``period`` key) crashed every
worker task with ``KeyError: 'period'`` inside ``run_single_simulation``.

The offending line built the strategy display name as::

    f"{name} w/ {stop_config['multiplier']}x ATR({stop_config['period']}) SL"

``stop_config['period']`` is not part of the documented config shape
(``config.py`` and CLAUDE.md only ever specify ``type`` + ``multiplier``);
only the CLI shorthand ``atr:14:3.0`` injects a ``period``. The engine itself
always uses the ``ATR_14`` column, so the label must default the period to 14
rather than assume the key is present.

These tests exercise the extracted, pure ``main._build_strat_name`` helper.
"""

import pytest

from main import _build_strat_name


class TestAtrStopNaming:
    def test_documented_atr_config_without_period_does_not_raise(self):
        """The exact documented config shape must not raise KeyError."""
        stop_config = {"type": "atr", "multiplier": 2.0}
        # Must not raise
        label = _build_strat_name("My Strategy", stop_config)
        assert "My Strategy" in label
        assert "ATR" in label
        assert "SL" in label

    def test_atr_config_without_period_defaults_to_14(self):
        """Engine always uses ATR_14, so the label defaults the period to 14."""
        stop_config = {"type": "atr", "multiplier": 2.0}
        assert _build_strat_name("S", stop_config) == "S w/ 2.0x ATR(14) SL"

    def test_atr_config_with_explicit_period_is_honored(self):
        """The CLI shorthand path supplies a period; it must be used verbatim."""
        stop_config = {"type": "atr", "multiplier": 3.0, "period": 21}
        assert _build_strat_name("S", stop_config) == "S w/ 3.0x ATR(21) SL"

    def test_percentage_config_label(self):
        stop_config = {"type": "percentage", "value": 0.05}
        assert _build_strat_name("S", stop_config) == "S w/ 5% SL"

    def test_none_config_returns_name_unchanged(self):
        assert _build_strat_name("S", {"type": "none"}) == "S"

    def test_unknown_stop_type_returns_name_unchanged(self):
        # points / signal_bar / trailing_atr etc. are not specially labelled
        assert _build_strat_name("S", {"type": "points", "value": 10}) == "S"
