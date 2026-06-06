"""Lightweight config validation helpers — no heavy dependencies.

Importable from both main.py and tests without pulling in orjson, pandas,
or other service-layer imports.
"""

from __future__ import annotations


def validate_forward_test_mode(ft: dict) -> list[str]:
    """Return a list of error strings for an invalid forward_test_mode config.

    Empty list means valid.
    """
    if not ft:
        return []
    errors: list[str] = []
    valid_models = ("isolated", "shared")
    cm = ft.get("capital_model", "isolated")
    if cm not in valid_models:
        errors.append(
            f"  - forward_test_mode.capital_model '{cm}' must be one of: {valid_models}"
        )
    for strat, amt in (ft.get("strategy_capital_allocation") or {}).items():
        if amt < 0:
            errors.append(
                f"  - forward_test_mode.strategy_capital_allocation['{strat}']"
                f" = {amt} must be >= 0"
            )
    return errors
