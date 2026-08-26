"""
Regression test for issue #319 — generate_final_summary defaulted
``max_acceptable_drawdown`` to ``-9999.0`` while the other three summary
functions default it to ``1.0``.

``max_drawdown`` is a positive magnitude, so the filter
``max_drawdown <= max_dd_filter`` with ``max_dd_filter = -9999`` is
unsatisfiable — every strategy is filtered out and the single-asset final
summary prints "No strategies met the final criteria" with no hint why, whenever
the key is absent from the config.
"""

import pytest

import helpers.summary as summary


@pytest.fixture
def permissive_config(monkeypatch):
    # Isolate the DD filter: make every OTHER filter permissive, and REMOVE
    # max_acceptable_drawdown so the code path under test uses its default.
    monkeypatch.setitem(summary.CONFIG, "mc_score_min_to_show_in_summary", -999)
    monkeypatch.setitem(summary.CONFIG, "min_pandl_to_show_in_summary", -999.0)
    monkeypatch.setitem(summary.CONFIG, "min_calmar_to_show_in_summary", -999.0)
    monkeypatch.setitem(summary.CONFIG, "min_trades_for_mc", 1)
    monkeypatch.setitem(summary.CONFIG, "verbose_output", False)
    monkeypatch.delitem(summary.CONFIG, "max_acceptable_drawdown", raising=False)


def _result(max_dd=0.2):
    return {
        "Strategy": "MyStrat", "Trades": 100, "max_drawdown": max_dd,
        "mc_score": 0, "pnl_percent": 0.5, "calmar_ratio": 1.0,
    }


def test_default_dd_filter_does_not_reject_a_normal_strategy(permissive_config, capsys):
    # A strategy with a normal 20% max drawdown must survive the default DD
    # filter (default 1.0 = 100% DD allowed), not be silently dropped.
    summary.generate_final_summary([_result(max_dd=0.2)], benchmark_returns={})
    out = capsys.readouterr().out
    assert "No strategies met the final criteria" not in out, \
        "default max_acceptable_drawdown filtered out a normal strategy (#319)"
    assert "MyStrat" in out


def test_explicit_dd_filter_still_rejects_over_threshold(permissive_config, monkeypatch, capsys):
    # The filter still works when configured: a 50% DD strategy is rejected by a
    # 30% cap.
    monkeypatch.setitem(summary.CONFIG, "max_acceptable_drawdown", 0.30)
    summary.generate_final_summary([_result(max_dd=0.5)], benchmark_returns={})
    out = capsys.readouterr().out
    assert "No strategies met the final criteria" in out
