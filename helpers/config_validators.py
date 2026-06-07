"""Backward-compatibility shim — do not add logic here.

validate_forward_test_mode was consolidated into helpers/config_validator.py
(singular) which is the canonical home for all config validation helpers.
This file re-exports it so any existing importers continue to work unmodified.
"""
from helpers.config_validator import validate_forward_test_mode  # noqa: F401
