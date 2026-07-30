"""tests/test_metrics_v3.py — unit tests for the v3 institutional metrics.

Pure numeric checks with hand-computed expectations; no I/O, no network,
no randomness except where a distribution property is asserted against pandas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade_analyzer import metrics_v3 as m


def _equity(vals, start="2020-01-31", freq="ME"):
    idx = pd.date_range(start, periods=len(vals), freq=freq)
    return pd.Series([float(v) for v in vals], index=idx, dtype=float)


class TestReturnRatios:
    def test_omega_equals_gain_pain_plus_one(self):
        r = pd.Series([0.02, -0.01, 0.03, -0.02, 0.01, 0.04])
        assert m.omega_ratio(r, 0.0) == pytest.approx(m.gain_to_pain(r) + 1.0, rel=1e-9)

    def test_gain_to_pain_value(self):
        # sum = 0.10, pain (|neg|) = 0.05  -> 2.0
        r = pd.Series([0.10, -0.05, 0.05])
        assert m.gain_to_pain(r) == pytest.approx(2.0)

    def test_omega_all_positive_is_inf(self):
        assert np.isinf(m.omega_ratio(pd.Series([0.01, 0.02, 0.03])))

    def test_ratios_empty_series_nan(self):
        assert np.isnan(m.omega_ratio(pd.Series([], dtype=float)))
        assert np.isnan(m.gain_to_pain(pd.Series([], dtype=float)))


class TestUlcerAndDrawdown:
    def test_ulcer_zero_for_monotonic(self):
        assert m.ulcer_index(_equity([100, 110, 120, 130])) == pytest.approx(0.0)

    def test_ulcer_known_value(self):
        # equity 100 -> 90: dd = [0, -0.1]; UI = sqrt((0 + 0.01)/2)
        assert m.ulcer_index(_equity([100, 90])) == pytest.approx(np.sqrt(0.01 / 2))

    def test_pct_time_in_drawdown(self):
        # peaks: 100,100,100,105 ; below-peak bars = index 1,2 -> 2/4
        assert m.pct_time_in_drawdown(_equity([100, 90, 95, 105])) == pytest.approx(0.5)

    def test_pct_time_zero_for_monotonic(self):
        assert m.pct_time_in_drawdown(_equity([100, 110, 120])) == pytest.approx(0.0)


class TestRecoveryAndPayoff:
    def test_recovery_factor(self):
        assert m.recovery_factor(20_000, 5_000) == pytest.approx(4.0)

    def test_recovery_factor_zero_dd_positive_is_inf(self):
        assert np.isinf(m.recovery_factor(100, 0))

    def test_recovery_factor_nan_inputs(self):
        assert np.isnan(m.recovery_factor(np.nan, 5000))

    def test_payoff_ratio(self):
        assert m.payoff_ratio(200, -100) == pytest.approx(2.0)

    def test_payoff_ratio_zero_loss_positive_is_inf(self):
        assert np.isinf(m.payoff_ratio(200, 0))


class TestVolatilityAndDistribution:
    def test_annualized_volatility(self):
        r = pd.Series([0.01, -0.01, 0.01, -0.01])
        assert m.annualized_volatility(r, 252) == pytest.approx(r.std(ddof=1) * np.sqrt(252))

    def test_annualized_volatility_short_series_nan(self):
        assert np.isnan(m.annualized_volatility(pd.Series([0.01]), 252))

    def test_excess_kurtosis_matches_pandas(self):
        r = pd.Series(np.random.default_rng(0).normal(0, 1, 1000))
        assert m.excess_kurtosis(r) == pytest.approx(r.kurt())

    def test_skewness_matches_pandas(self):
        r = pd.Series(np.random.default_rng(1).normal(0, 1, 500))
        assert m.skewness(r) == pytest.approx(r.skew())


class TestPeriodReturns:
    def test_itd_equals_total_return(self):
        idx = pd.date_range("2020-01-01", periods=400, freq="D")
        eq = pd.Series(100_000 * (1.001) ** np.arange(400), index=idx)
        pr = m.period_returns(eq)
        assert pr["ITD"] == pytest.approx(eq.iloc[-1] / eq.iloc[0] - 1)

    def test_short_history_long_window_falls_back_to_inception(self):
        idx = pd.date_range("2020-01-01", periods=10, freq="D")
        eq = pd.Series(np.linspace(100, 110, 10), index=idx)
        pr = m.period_returns(eq)
        assert pr["1Y"] == pytest.approx(pr["ITD"])

    def test_empty_equity_all_nan(self):
        pr = m.period_returns(pd.Series([], dtype=float))
        assert all(np.isnan(v) for v in pr.values())


class TestMonthlyAggregation:
    def test_monthly_pnl_sums_to_total_profit(self):
        idx = pd.date_range("2020-01-01", periods=300, freq="D")
        eq = pd.Series(100_000 + np.arange(300) * 10.0, index=idx)
        pnl = m.monthly_pnl(eq, 100_000)
        assert pnl.sum() == pytest.approx(eq.iloc[-1] - 100_000)

    def test_strip_total_equals_sum_of_cells(self):
        idx = pd.date_range("2020-01-01", periods=300, freq="D")
        eq = pd.Series(100_000 + np.arange(300) * 10.0, index=idx)
        labels, vals, total = m.monthly_pnl_strip(eq, 100_000, n_months=8)
        assert total == pytest.approx(sum(vals))
        assert len(labels) == len(vals) <= 8


class TestRollingSeries:
    def test_rolling_empty_when_shorter_than_window(self):
        r = pd.Series(np.random.default_rng(1).normal(0, 0.01, 50))
        assert m.rolling_sharpe(r, window=126).empty
        assert m.rolling_volatility(r, window=126).empty

    def test_rolling_volatility_length(self):
        r = pd.Series(np.random.default_rng(2).normal(0.0005, 0.01, 300))
        rv = m.rolling_volatility(r, window=126)
        assert len(rv) == 300 - 126 + 1


class TestAggregator:
    def _fixture(self):
        idx = pd.date_range("2020-01-01", periods=300, freq="D")
        eq = pd.Series(100_000 + np.arange(300) * 20.0, index=idx)
        ret = eq.pct_change().dropna()
        trades = pd.DataFrame({
            "Profit": [100.0, -50.0, 200.0, -30.0],
            "Win": [True, False, True, False],
            "RMultiple": [1.0, -0.5, 2.0, -0.3],
        })
        core = {
            "total_profit": 6000.0, "duration_years": 300 / 365.25,
            "avg_win": 150.0, "avg_loss": -40.0, "total_trades": 4,
            "win_rate": 0.5, "profit_factor": 2.0, "avg_trade_profit": 55.0,
            "max_consecutive_wins": 1, "max_consecutive_losses": 1,
            "sharpe": 1.2, "sortino": 1.5,
        }
        return eq, ret, trades, core

    def test_expected_keys_present(self):
        eq, ret, trades, core = self._fixture()
        M = m.compute_v3_metrics(eq, ret, trades, core, initial_equity=100_000,
                                 max_dd_dollars=0.0, max_dd_pct=0.0, trading_days=252)
        for k in ("sharpe", "sortino", "calmar", "omega", "gain_to_pain",
                  "serenity", "recovery_factor", "ret_dd", "volatility_ann",
                  "ulcer_index", "skewness", "excess_kurtosis", "payoff_ratio",
                  "r_expectancy", "period_returns"):
            assert k in M, f"missing key {k}"

    def test_payoff_and_r_expectancy_values(self):
        eq, ret, trades, core = self._fixture()
        M = m.compute_v3_metrics(eq, ret, trades, core, initial_equity=100_000,
                                 max_dd_dollars=0.0, max_dd_pct=0.0, trading_days=252)
        assert M["payoff_ratio"] == pytest.approx(150.0 / 40.0)
        assert M["r_expectancy"] == pytest.approx(np.mean([1.0, -0.5, 2.0, -0.3]))
        assert M["best_trade"] == pytest.approx(200.0)
        assert M["worst_trade"] == pytest.approx(-50.0)

    def test_recovery_factor_uses_dollar_drawdown(self):
        eq, ret, trades, core = self._fixture()
        M = m.compute_v3_metrics(eq, ret, trades, core, initial_equity=100_000,
                                 max_dd_dollars=1500.0, max_dd_pct=1.5, trading_days=252)
        assert M["recovery_factor"] == pytest.approx(6000.0 / 1500.0)
        assert M["ret_dd"] == pytest.approx(M["recovery_factor"])
