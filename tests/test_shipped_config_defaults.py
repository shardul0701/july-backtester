"""Shipped config.py safety defaults."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_shipped_price_adjustment_defaults_to_total_return() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    if "config" in sys.modules:
        del sys.modules["config"]
    config = importlib.import_module("config")
    assert config.CONFIG["price_adjustment"] == "total_return"
