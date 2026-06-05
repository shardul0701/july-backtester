"""Tests for forward_test_mode config validation (issue #162).

Tests the validate_forward_test_mode() function imported directly from main.
This avoids the subprocess wrapper approach which is fragile across Python
interpreter versions (the wrapper fails if orjson is missing in Python 3.9).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Load config_validators directly to avoid triggering helpers/__init__.py
# (which pulls in pandas_ta and numpy — unavailable in the CI Python 3.9 env).
_spec = importlib.util.spec_from_file_location(
    "config_validators", ROOT / "helpers" / "config_validators.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
validate_forward_test_mode = _mod.validate_forward_test_mode


class TestForwardTestConfigValidation:
    def test_isolated_model_returns_no_errors(self):
        errors = validate_forward_test_mode({
            "capital_model": "isolated",
            "strategy_capital_allocation": {},
        })
        assert errors == []

    def test_shared_model_returns_no_errors(self):
        errors = validate_forward_test_mode({
            "capital_model": "shared",
            "strategy_capital_allocation": {},
        })
        assert errors == []

    def test_invalid_capital_model_returns_error(self):
        errors = validate_forward_test_mode({"capital_model": "magic"})
        assert len(errors) == 1

    def test_invalid_capital_model_mentions_key(self):
        errors = validate_forward_test_mode({"capital_model": "magic"})
        assert "capital_model" in errors[0]

    def test_invalid_capital_model_mentions_value(self):
        errors = validate_forward_test_mode({"capital_model": "magic"})
        assert "magic" in errors[0]

    def test_negative_allocation_returns_error(self):
        errors = validate_forward_test_mode({
            "capital_model": "isolated",
            "strategy_capital_allocation": {"RSI Strategy": -5000},
        })
        assert len(errors) == 1

    def test_negative_allocation_mentions_strategy_name(self):
        errors = validate_forward_test_mode({
            "capital_model": "isolated",
            "strategy_capital_allocation": {"RSI Strategy": -5000},
        })
        assert "RSI Strategy" in errors[0]

    def test_zero_allocation_is_valid(self):
        errors = validate_forward_test_mode({
            "capital_model": "isolated",
            "strategy_capital_allocation": {"RSI Strategy": 0},
        })
        assert errors == []

    def test_multiple_negative_allocations_produce_multiple_errors(self):
        errors = validate_forward_test_mode({
            "capital_model": "isolated",
            "strategy_capital_allocation": {
                "Strategy A": -1000,
                "Strategy B": -2000,
            },
        })
        assert len(errors) == 2

    def test_empty_dict_returns_no_errors(self):
        assert validate_forward_test_mode({}) == []

    def test_missing_capital_model_defaults_to_isolated(self):
        errors = validate_forward_test_mode({"strategy_capital_allocation": {}})
        assert errors == []

    def test_combined_bad_model_and_negative_allocation(self):
        errors = validate_forward_test_mode({
            "capital_model": "magic",
            "strategy_capital_allocation": {"RSI": -100},
        })
        assert len(errors) == 2
