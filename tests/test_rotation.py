"""tests/test_rotation.py

Tests for the cross-sectional rotation mechanism (issue #294).

Covers:
- registry: kind="rotation" registration + register_rotation sugar + backward
  compatibility of the classic signal path;
- portfolio construction (top-N selection, equal weighting);
- rebalance / rank-drop / trim mechanics;
- config-key validation (rotation, max_position_pct recognised);
- CRITICAL: scale-invariance regression — 100k vs 1M produce identical %
  returns and proportional share counts (the #293 lesson).

Pure, deterministic, no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from helpers import rotation
from helpers.registry import (
    register_strategy,
    register_rotation,
    REGISTRY,
    SIGNAL,
    ROTATION,
)


# ---------------------------------------------------------------------------
# Deterministic synthetic universe
# ---------------------------------------------------------------------------
def _make_df(prices, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="D")
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {"Open": p, "High": p * 1.01, "Low": p * 0.99, "Close": p, "Volume": 1e6},
        index=idx,
    )


def _linear_universe(n_days=120):
    """Four symbols with strictly ordered, monotonic uptrends so the momentum
    ranking is deterministic: STRONG > MID > WEAK > FLAT every day."""
    base = np.arange(n_days, dtype=float)
    return {
        "STRONG": _make_df(100 + base * 2.0),
        "MID": _make_df(100 + base * 1.0),
        "WEAK": _make_df(100 + base * 0.3),
        "FLAT": _make_df(100 + base * 0.0 + 100.0),  # constant 200
    }


def _momentum_rank(lookback=20):
    def rank(data, rebalance_date, **kwargs):
        scores = {}
        for sym, df in data.items():
            w = df.loc[:rebalance_date]
            if len(w) <= lookback:
                continue
            past = w["Close"].iloc[-lookback - 1]
            now = w["Close"].iloc[-1]
            if past > 0:
                scores[sym] = float(now / past - 1.0)
        return scores
    return rank


def _base_config(initial_capital=100_000.0, **rotation_overrides):
    rot = {
        "enabled": True,
        "top_n": 2,
        "rebalance_days": 21,
        "weighting": "equal",
        "sell_buffer_rank": 0,
        "drift_trim_pct": 0.0,
    }
    rot.update(rotation_overrides)
    return {
        "initial_capital": initial_capital,
        "allocation_per_trade": 0.10,
        "max_position_pct": 1.0,
        "commission_per_share": 0.0,
        "slippage_pct": 0.0,
        "rotation": rot,
        # instruments resolves an equity instrument from these defaults
        "instruments": {"default_asset_class": "equity"},
    }


# ---------------------------------------------------------------------------
# Registry: kind + backward compat
# ---------------------------------------------------------------------------
class TestRegistryKind:
    def test_register_rotation_sets_kind(self):
        name = "test-rot-plugin-xyz"

        @register_rotation(name=name, params={"lookback": 10})
        def _r(data, rebalance_date, **kwargs):
            return list(data.keys())

        entry = REGISTRY[name]
        assert entry["kind"] == ROTATION
        assert entry["logic"] is _r
        assert entry["params"] == {"lookback": 10}
        assert entry["regime_gate"] is None

    def test_register_strategy_defaults_to_signal_kind(self):
        name = "test-sig-plugin-xyz"

        @register_strategy(name=name)
        def _s(df, **kwargs):
            return df

        entry = REGISTRY[name]
        assert entry["kind"] == SIGNAL
        # backward-compatible shape: logic / dependencies / params all present
        assert set(["logic", "dependencies", "params"]).issubset(entry.keys())

    def test_get_active_strategies_excludes_rotation(self, monkeypatch):
        from helpers import registry

        name_rot = "test-active-rot"
        name_sig = "test-active-sig"

        @register_rotation(name=name_rot)
        def _r(data, rebalance_date, **kwargs):
            return []

        @register_strategy(name=name_sig)
        def _s(df, **kwargs):
            return df

        # get_active_strategies imports CONFIG lazily; provide a minimal one.
        import config as _cfg
        monkeypatch.setitem(_cfg.CONFIG, "strategies", "all")

        active = registry.get_active_strategies(directory="does_not_exist_dir")
        assert name_sig in active
        assert name_rot not in active

    def test_get_rotation_strategies_returns_only_rotation(self):
        from helpers import registry

        name_rot = "test-getrot-rot"

        @register_rotation(name=name_rot)
        def _r(data, rebalance_date, **kwargs):
            return []

        rots = registry.get_rotation_strategies(directory="does_not_exist_dir")
        assert name_rot in rots
        assert all(e["kind"] == ROTATION for e in rots.values())


# ---------------------------------------------------------------------------
# Rebalance calendar + ranking normalisation
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_build_rebalance_dates_stride_and_last(self):
        data = _linear_universe(n_days=100)
        dates = rotation.build_rebalance_dates(data, rebalance_days=21)
        all_dates = sorted(set(data["STRONG"].index))
        assert dates[0] == all_dates[0]
        assert dates[-1] == all_dates[-1]  # last date always included
        # stride of 21 across 100 days -> indices 0,21,42,63,84 (+last)
        assert dates[1] == all_dates[21]

    def test_normalise_ranking_dict_sorts_desc(self):
        out = rotation._normalise_ranking({"A": 0.1, "B": 0.9, "C": 0.5})
        assert out == ["B", "C", "A"]

    def test_normalise_ranking_list_passthrough(self):
        assert rotation._normalise_ranking(["X", "Y"]) == ["X", "Y"]

    def test_normalise_ranking_none_empty(self):
        assert rotation._normalise_ranking(None) == []


# ---------------------------------------------------------------------------
# Portfolio construction
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_top_n_selection_holds_strongest(self):
        data = _linear_universe()
        cfg = _base_config(top_n=2)
        res = rotation.run_rotation(data, _momentum_rank(), cfg)
        assert res is not None
        held = {t["Symbol"] for t in res["trade_log"]}
        # top-2 momentum are always STRONG and MID; WEAK/FLAT never selected
        assert held == {"STRONG", "MID"}

    def test_equal_weight_two_positions_half_each(self):
        data = _linear_universe()
        cfg = _base_config(top_n=2, rebalance_days=200)  # single rebalance window
        res = rotation.run_rotation(data, _momentum_rank(), cfg)
        assert res is not None
        # Two equal-weight positions => each ~50% of initial equity at entry.
        # Reconstruct entry notional from the trade log.
        entries = {t["Symbol"]: t["EntryPrice"] * t["Shares"] for t in res["trade_log"]}
        assert set(entries) == {"STRONG", "MID"}
        for notional in entries.values():
            assert notional == pytest.approx(50_000.0, rel=0.02)

    def test_result_shape_matches_pipeline(self):
        data = _linear_universe()
        res = rotation.run_rotation(data, _momentum_rank(), _base_config())
        for key in ("trade_log", "portfolio_timeline", "trade_pnl_list",
                    "initial_capital", "pnl_percent", "Trades",
                    "max_drawdown", "sharpe_ratio", "win_rate"):
            assert key in res
        assert isinstance(res["portfolio_timeline"], pd.Series)
        # trade log entries carry the engine-shaped keys
        t0 = res["trade_log"][0]
        for key in ("Symbol", "EntryDate", "ExitDate", "EntryPrice", "ExitPrice",
                    "Shares", "Profit", "ProfitPct", "HoldDuration", "ExitReason",
                    "InitialRisk", "RMultiple", "is_win"):
            assert key in t0

    def test_empty_data_returns_none(self):
        assert rotation.run_rotation({}, _momentum_rank(), _base_config()) is None


# ---------------------------------------------------------------------------
# Rebalance / rank-drop / regime gate mechanics
# ---------------------------------------------------------------------------
class TestMechanics:
    def test_rank_drop_triggers_sell(self):
        # Build a universe where the leader flips halfway so a held name drops out.
        n = 80
        base = np.arange(n, dtype=float)
        up_then_flat = np.concatenate([100 + base[:40] * 3.0, np.full(40, 100 + 39 * 3.0)])
        flat_then_up = np.concatenate([np.full(40, 100.0), 100 + base[:40] * 5.0])
        data = {
            "EARLY": _make_df(up_then_flat),
            "LATE": _make_df(flat_then_up),
            "NOISE": _make_df(100 + base * 0.1),
        }
        cfg = _base_config(top_n=1, rebalance_days=10, sell_buffer_rank=0)
        res = rotation.run_rotation(data, _momentum_rank(lookback=10), cfg)
        assert res is not None
        reasons = {t["ExitReason"] for t in res["trade_log"]}
        # A held leader that loses its rank is sold with a "Rank Drop" reason.
        assert "Rank Drop" in reasons
        symbols = {t["Symbol"] for t in res["trade_log"]}
        assert "EARLY" in symbols and "LATE" in symbols

    def test_regime_gate_off_liquidates(self):
        data = _linear_universe()

        def gate(_data, date):
            # Risk-off after day 40 -> everything sold, nothing re-bought.
            return pd.Timestamp(date) < pd.Timestamp("2020-02-10")

        cfg = _base_config(top_n=2, rebalance_days=10)
        res = rotation.run_rotation(data, _momentum_rank(), cfg, regime_gate=gate)
        assert res is not None
        reasons = [t["ExitReason"] for t in res["trade_log"]]
        assert "Regime Off" in reasons
        # After liquidation the book is flat -> final equity is pure cash, so the
        # last portion of the timeline is constant.
        tl = res["portfolio_timeline"]
        assert tl.iloc[-1] == pytest.approx(tl.iloc[-5], rel=1e-9)

    def test_sell_buffer_reduces_churn(self):
        # With a generous buffer a name kept just outside top_n is not churned.
        data = _linear_universe()
        cfg_no_buffer = _base_config(top_n=1, rebalance_days=10, sell_buffer_rank=0)
        cfg_buffer = _base_config(top_n=1, rebalance_days=10, sell_buffer_rank=3)
        res_nb = rotation.run_rotation(data, _momentum_rank(), cfg_no_buffer)
        res_b = rotation.run_rotation(data, _momentum_rank(), cfg_buffer)
        # Buffer never increases the trade count.
        assert res_b["Trades"] <= res_nb["Trades"] + 0  # deterministic universe: equal or fewer


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
class TestConfigValidation:
    def test_rotation_keys_recognised(self):
        from helpers.config_validator import validate_config, KNOWN_KEYS
        assert "rotation" in KNOWN_KEYS
        assert "max_position_pct" in KNOWN_KEYS
        cfg = {"rotation": {"enabled": True}, "max_position_pct": 0.25}
        warnings = validate_config(cfg)
        assert not any("rotation" in w or "max_position_pct" in w for w in warnings)

    def test_config_py_has_rotation_disabled_by_default(self):
        from config import CONFIG
        assert CONFIG["rotation"]["enabled"] is False
        assert CONFIG["max_position_pct"] == 1.0


# ---------------------------------------------------------------------------
# CRITICAL: scale invariance (issue #293)
# ---------------------------------------------------------------------------
class TestScaleInvariance:
    def test_returns_identical_across_capital_scales(self):
        data = _linear_universe()
        rank = _momentum_rank()

        res_100k = rotation.run_rotation(data, rank, _base_config(100_000.0, top_n=2))
        res_1m = rotation.run_rotation(data, rank, _base_config(1_000_000.0, top_n=2))

        # Same percentage return regardless of capital scale.
        assert res_100k["pnl_percent"] == pytest.approx(res_1m["pnl_percent"], rel=1e-9)
        # Same max drawdown / sharpe.
        assert res_100k["max_drawdown"] == pytest.approx(res_1m["max_drawdown"], rel=1e-9)

        # Same number of trades, and share counts scale by exactly 10x.
        assert res_100k["Trades"] == res_1m["Trades"]
        tl_100 = sorted(res_100k["trade_log"], key=lambda t: (t["Symbol"], t["EntryDate"]))
        tl_1m = sorted(res_1m["trade_log"], key=lambda t: (t["Symbol"], t["EntryDate"]))
        for a, b in zip(tl_100, tl_1m):
            assert a["Symbol"] == b["Symbol"]
            assert b["Shares"] == pytest.approx(a["Shares"] * 10.0, rel=1e-9)
            assert b["Profit"] == pytest.approx(a["Profit"] * 10.0, rel=1e-9)

    def test_equity_curve_scales_proportionally(self):
        data = _linear_universe()
        rank = _momentum_rank()
        res_100k = rotation.run_rotation(data, rank, _base_config(100_000.0, top_n=2))
        res_1m = rotation.run_rotation(data, rank, _base_config(1_000_000.0, top_n=2))
        a = res_100k["portfolio_timeline"]
        b = res_1m["portfolio_timeline"]
        assert list(a.index) == list(b.index)
        # Every equity point scales by exactly 10x.
        ratio = (b / a).dropna()
        assert np.allclose(ratio.values, 10.0, rtol=1e-9)
