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
        # Explicit weekday dates (Thu/Fri) -- the shared _equity() default
        # (freq="ME" from 2020-01-31) lands its 2nd point on Sat 2020-02-29,
        # which the business-day filter in ulcer_index() would now drop.
        assert m.ulcer_index(_equity([100, 90], start="2020-01-30", freq="D")) == pytest.approx(np.sqrt(0.01 / 2))

    def test_pct_time_in_drawdown(self):
        # Default fixture dates (freq="ME" from 2020-01-31) are
        # Fri 1/31, Sat 2/29, Tue 3/31, Thu 4/30 -- the Sat row (value 90)
        # is now excluded as a weekend by pct_time_in_drawdown(), leaving
        # eq = [100, 95, 105]; peaks = [100, 100, 105]; below-peak bar =
        # index 1 (95 < 100) -> 1/3.
        assert m.pct_time_in_drawdown(_equity([100, 90, 95, 105])) == pytest.approx(1 / 3)

    def test_pct_time_zero_for_monotonic(self):
        assert m.pct_time_in_drawdown(_equity([100, 110, 120])) == pytest.approx(0.0)

    def test_weekend_ffill_rows_excluded(self):
        # Simulate the upstream calendar-day ffill from
        # data_handler.calculate_daily_returns(): a trading-day series
        # Fri/Mon/Tue/Wed with the Sat/Sun gap flat-forwarded from Friday's
        # close should score identically to the trading-day-only series,
        # since the ffilled weekend rows are never a new peak or trough.
        trading_days = pd.to_datetime(["2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"])
        trading_only = pd.Series([100.0, 90.0, 95.0, 105.0], index=trading_days)

        calendar_days = pd.to_datetime(
            ["2020-01-03", "2020-01-04", "2020-01-05", "2020-01-06", "2020-01-07", "2020-01-08"]
        )
        with_weekend_ffill = pd.Series([100.0, 100.0, 100.0, 90.0, 95.0, 105.0], index=calendar_days)

        assert m.pct_time_in_drawdown(with_weekend_ffill) == pytest.approx(m.pct_time_in_drawdown(trading_only))
        assert m.pct_time_in_drawdown(with_weekend_ffill) == pytest.approx(0.5)
        assert m.ulcer_index(with_weekend_ffill) == pytest.approx(m.ulcer_index(trading_only))


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


class TestSerenityIndex:
    """Covers the annualized-numerator fix (issue: Serenity scaled with
    backtest length because it used cumulative total_return instead of an
    annualized return in the numerator)."""

    def _fixture(self):
        eq = pd.Series([100.0, 90.0, 105.0, 95.0, 115.0],
                        index=pd.bdate_range("2020-01-01", periods=5))
        r = eq.pct_change().dropna()
        return eq, r

    def test_uses_passed_in_annualized_return_not_cumulative_total_return(self):
        eq, r = self._fixture()
        ann_ret = 0.25  # deliberately distinct from the cumulative total return
        ui = m.ulcer_index(eq)
        std = r.std(ddof=1)
        _, cvar = m.var_cvar_pct(r, level=0.05)
        pitfall = abs(cvar) / std
        expected = ann_ret / (ui * pitfall)

        assert m.serenity_index(eq, r, ann_ret) == pytest.approx(expected)

        # Sanity: the pre-fix (cumulative) formula would have given a
        # different number, confirming the numerator actually changed.
        total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
        assert total_return != pytest.approx(ann_ret)
        old_style = total_return / (ui * pitfall)
        assert m.serenity_index(eq, r, ann_ret) != pytest.approx(old_style)

    def test_nan_when_annualized_return_is_nan(self):
        eq, r = self._fixture()
        assert np.isnan(m.serenity_index(eq, r, np.nan))

    def test_nan_for_short_series(self):
        eq = pd.Series([100.0], index=pd.bdate_range("2020-01-01", periods=1))
        r = pd.Series([], dtype=float)
        assert np.isnan(m.serenity_index(eq, r, 0.10))


class TestSerenityDurationInvariance:
    """Regression test for the reviewer's exact bug shape: the same
    risk-adjusted profile (identical per-cycle return pattern, hence
    identical Ulcer Index / tail-risk 'pitfall' and identical CAGR) measured
    over a short vs. a 5x-longer backtest must now produce comparable
    Serenity scores — not one that balloons with duration."""

    @staticmethod
    def _cyclic_equity(pattern, cycles, start="2020-01-01"):
        rets = np.tile(np.array(pattern, dtype=float), cycles)
        idx = pd.bdate_range(start, periods=len(rets) + 1)
        growth = np.concatenate([[1.0], np.cumprod(1 + rets)])
        return pd.Series(100_000.0 * growth, index=idx)

    def _serenity_for(self, eq):
        from trade_analyzer import calculations
        r = eq.pct_change().dropna()
        duration_years = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = calculations.calculate_cagr(eq.iloc[0], eq.iloc[-1], duration_years)
        return m.serenity_index(eq, r, cagr)

    def test_fixed_annualization_keeps_serenity_comparable_across_durations(self):
        pattern = [0.02, -0.01, 0.015, -0.02, 0.03]  # one repeating weekly cycle
        short_eq = self._cyclic_equity(pattern, cycles=4)    # ~1 month
        long_eq = self._cyclic_equity(pattern, cycles=20)    # ~5x the duration

        short_serenity = self._serenity_for(short_eq)
        long_serenity = self._serenity_for(long_eq)

        assert not np.isnan(short_serenity)
        assert not np.isnan(long_serenity)
        assert short_serenity == pytest.approx(long_serenity, rel=0.05)

    def test_old_cumulative_formula_would_have_diverged_with_duration(self):
        """Documents the bug being fixed: substituting raw cumulative
        total_return for the numerator (the pre-fix formula) makes Serenity
        balloon with backtest length even though the risk profile and
        per-cycle return are identical -- exactly the reviewer's finding."""
        pattern = [0.02, -0.01, 0.015, -0.02, 0.03]
        short_eq = self._cyclic_equity(pattern, cycles=4)
        long_eq = self._cyclic_equity(pattern, cycles=20)

        def _old_style_serenity(eq):
            r = eq.pct_change().dropna()
            total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
            ui = m.ulcer_index(eq)
            std = r.std(ddof=1)
            _, cvar = m.var_cvar_pct(r, level=0.05)
            pitfall = abs(cvar) / std
            return total_return / (ui * pitfall)

        short_old = _old_style_serenity(short_eq)
        long_old = _old_style_serenity(long_eq)

        assert long_old / short_old > 2.0


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


class TestVarCvar:
    """Covers the reported bug (VaR 95% prints 0.00% for a low-exposure
    strategy) and the active_only fix."""

    def _mostly_flat_returns(self):
        # 100 days: 3 real losses, 92 flat (no position), 5 small gains --
        # a low-exposure strategy shape like the reviewer's 87.7%-flat case.
        return pd.Series([-0.05, -0.03, -0.02] + [0.0] * 92 + [0.01] * 5)

    def test_full_series_quantile_diluted_to_zero_by_flat_days(self):
        # Documents the pre-fix behavior: with active_only left at its
        # default (False), the flat days dominate the tail and the 5th
        # percentile lands exactly on 0.0, masking real losing days.
        r = self._mostly_flat_returns()
        var_full, _ = m.var_cvar_pct(r, level=0.05)
        assert var_full == pytest.approx(0.0)

    def test_active_only_excludes_flat_days_and_surfaces_real_tail_risk(self):
        r = self._mostly_flat_returns()
        active = r[r != 0]
        expected_var = float(active.quantile(0.05))
        expected_cvar = float(active[active <= expected_var].mean())

        var_active, cvar_active = m.var_cvar_pct(r, level=0.05, active_only=True)

        assert var_active == pytest.approx(expected_var)
        assert cvar_active == pytest.approx(expected_cvar)
        assert var_active < 0.0  # no longer masked to 0.00%

    def test_active_only_is_noop_when_no_flat_days(self):
        r = pd.Series([0.01, -0.02, 0.015, -0.03, 0.02, -0.01])
        assert m.var_cvar_pct(r, level=0.05, active_only=True) == \
            m.var_cvar_pct(r, level=0.05, active_only=False)

    def test_active_only_too_few_nonzero_points_is_nan(self):
        r = pd.Series([0.0, 0.0, 0.0, -0.01])
        var, cvar = m.var_cvar_pct(r, level=0.05, active_only=True)
        assert np.isnan(var)
        assert np.isnan(cvar)


class TestPeriodReturns:
    def test_itd_equals_total_return(self):
        idx = pd.date_range("2020-01-01", periods=400, freq="D")
        eq = pd.Series(100_000 * (1.001) ** np.arange(400), index=idx)
        pr = m.period_returns(eq)
        assert pr["ITD"] == pytest.approx(eq.iloc[-1] / eq.iloc[0] - 1)

    def test_short_history_long_window_is_nan_not_inception(self):
        # A 10-day series starting mid-year has no genuine 3M/6M/YTD/1Y window
        # inside it (their start dates all predate the first equity bar) —
        # those must report N/A rather than silently reusing the inception
        # value. ITD is exempt: it is defined as the full-history return.
        idx = pd.date_range("2020-06-15", periods=10, freq="D")
        eq = pd.Series(np.linspace(100, 110, 10), index=idx)
        pr = m.period_returns(eq)
        assert np.isnan(pr["1M"])
        assert np.isnan(pr["3M"])
        assert np.isnan(pr["6M"])
        assert np.isnan(pr["YTD"])
        assert np.isnan(pr["1Y"])
        assert pr["ITD"] == pytest.approx(eq.iloc[-1] / eq.iloc[0] - 1)

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


class TestTotalFees:
    """Blocker #3 (PR #249, reviewer shardul0701): total_fees() must sum
    every recognised fee column instead of returning on the first match,
    and must never treat a "Cost" column (position notional, not a fee) as
    a fee."""

    def test_multiple_distinct_fee_columns_are_summed(self):
        # Reviewer's repro shape: Commission ($249) and Fees ($1,245) are two
        # genuinely separate fee line items -- both must count.
        trades = pd.DataFrame({
            "Commission": [100.0, 149.0],
            "Fees": [620.0, 625.0],
        })
        assert m.total_fees(trades) == pytest.approx(249.0 + 1245.0)

    def test_cost_column_is_ignored_entirely(self):
        # "Cost" is position cost basis / notional, not a fee -- a lone
        # Cost column must contribute $0, not be auto-detected as fees.
        trades = pd.DataFrame({
            "Cost": [3_000_000.0, 3_225_000.0],
        })
        assert m.total_fees(trades) == pytest.approx(0.0)

    def test_cost_column_alongside_real_fees_not_double_counted(self):
        # A Cost (notional) column sitting next to a genuine Commission
        # column must not leak into the fee total.
        trades = pd.DataFrame({
            "Commission": [100.0, 149.0],
            "Cost": [3_000_000.0, 3_225_000.0],
        })
        assert m.total_fees(trades) == pytest.approx(249.0)

    def test_single_fee_column_no_regression(self):
        trades = pd.DataFrame({"Commission": [10.0, 20.0, 30.0]})
        assert m.total_fees(trades) == pytest.approx(60.0)

    def test_singular_and_plural_commission_columns_both_summed(self):
        # Header-naming inconsistency across data sources (Commission vs
        # Commissions) is still two matched columns per the literal
        # sum-all-matches fix -- no deduplication heuristic is applied.
        trades = pd.DataFrame({
            "Commission": [10.0, 20.0],
            "Commissions": [5.0, 5.0],
        })
        assert m.total_fees(trades) == pytest.approx(40.0)

    def test_empty_or_non_dataframe_returns_zero(self):
        assert m.total_fees(pd.DataFrame()) == 0.0
        assert m.total_fees(None) == 0.0

    def test_no_recognised_columns_returns_zero(self):
        trades = pd.DataFrame({"Profit": [10.0, -5.0]})
        assert m.total_fees(trades) == pytest.approx(0.0)
