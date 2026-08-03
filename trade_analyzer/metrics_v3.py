# metrics_v3.py — institutional-style metrics for the v3 tearsheet layout.
#
# Pure, stateless functions computed from a daily equity Series (DatetimeIndex),
# the matching daily-return Series, and the trades DataFrame. Nothing here does
# I/O, plotting, or config mutation, so every function is directly unit-testable.
#
# The v3 tearsheet (trade_analyzer/_pdf_v3.py) is the only consumer. Standard
# definitions are used wherever one exists; the two ratios that lack a single
# canonical formula (Serenity, Gain/Pain) are documented inline so the number is
# reproducible even if it differs from a given vendor's proprietary variant.
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clean_equity(equity: pd.Series) -> pd.Series:
    """Return a tz-naive, date-sorted, NaN-free copy of an equity Series."""
    if not isinstance(equity, pd.Series) or equity.empty:
        return pd.Series(dtype=float)
    eq = equity.dropna().copy()
    if eq.empty:
        return eq
    eq.index = pd.to_datetime(eq.index)
    if getattr(eq.index, "tz", None) is not None:
        eq.index = eq.index.tz_localize(None)
    return eq.sort_index()


def _clean_returns(returns: pd.Series) -> pd.Series:
    if not isinstance(returns, pd.Series) or returns.empty:
        return pd.Series(dtype=float)
    return returns.dropna().astype(float)


# ---------------------------------------------------------------------------
# Risk / volatility
# ---------------------------------------------------------------------------

def annualized_volatility(daily_returns: pd.Series, trading_days: int = 252) -> float:
    """Annualized standard deviation of returns (sample std, ddof=1)."""
    r = _clean_returns(daily_returns)
    if len(r) < 2:
        return np.nan
    return float(r.std(ddof=1) * np.sqrt(trading_days))


def ulcer_index(equity: pd.Series) -> float:
    """Ulcer Index = sqrt(mean(drawdown_pct**2)), drawdown measured off the
    running peak as a fraction. Penalizes deep *and* prolonged drawdowns.

    The upstream daily_equity Series (data_handler.calculate_daily_returns)
    is built on a calendar-day pd.date_range + ffill, so weekends carry a
    synthetic flat-forward copy of the prior Friday's value. Those rows are
    never a new peak or a new trough relative to the trading day they were
    ffilled from, so dropping them here doesn't change the drawdown shape at
    surviving (trading-day) rows — it just keeps them out of the mean."""
    eq = _clean_equity(equity)
    if len(eq):
        eq = eq[eq.index.dayofweek < 5]
    if len(eq) < 2:
        return np.nan
    peak = eq.cummax()
    dd = (eq - peak) / peak  # <= 0
    return float(np.sqrt(np.mean(np.square(dd.values))))


def pct_time_in_drawdown(equity: pd.Series) -> float:
    """Fraction of bars where equity sits below its prior peak (0..1).

    Restricted to business days (Mon-Fri) — see ulcer_index docstring for why
    the calendar-day ffilled weekend rows are excluded from the denominator."""
    eq = _clean_equity(equity)
    if len(eq):
        eq = eq[eq.index.dayofweek < 5]
    if len(eq) < 2:
        return np.nan
    peak = eq.cummax()
    return float((eq < peak).mean())


def var_cvar_pct(daily_returns: pd.Series, level: float = 0.05):
    """Historical VaR / CVaR of the daily-return distribution (as fractions).
    Both are returned as negative numbers for a loss-tail level."""
    r = _clean_returns(daily_returns)
    if len(r) < 2:
        return np.nan, np.nan
    var = float(r.quantile(level))
    tail = r[r <= var]
    cvar = float(tail.mean()) if not tail.empty else var
    return var, cvar


# ---------------------------------------------------------------------------
# Return-quality ratios
# ---------------------------------------------------------------------------

def omega_ratio(returns: pd.Series, threshold: float = 0.0) -> float:
    """Omega(threshold) = sum(gains above threshold) / sum(|losses below threshold|).
    At threshold 0 this equals Gain/Pain + 1."""
    r = _clean_returns(returns) - threshold
    if r.empty:
        return np.nan
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    if losses <= 1e-12:
        return np.inf if gains > 0 else np.nan
    return float(gains / losses)


def gain_to_pain(returns: pd.Series) -> float:
    """Gain-to-Pain ratio = sum(returns) / sum(|negative returns|) (Schwager)."""
    r = _clean_returns(returns)
    if r.empty:
        return np.nan
    pain = -r[r < 0].sum()
    if pain <= 1e-12:
        return np.inf if r.sum() > 0 else np.nan
    return float(r.sum() / pain)


def recovery_factor(net_profit: float, max_drawdown_dollars: float) -> float:
    """Recovery Factor (= Return/DD in $ terms) = net profit / max drawdown $.
    ``max_drawdown_dollars`` is expected as a positive magnitude."""
    if net_profit is None or max_drawdown_dollars is None:
        return np.nan
    if pd.isna(net_profit) or pd.isna(max_drawdown_dollars):
        return np.nan
    mdd = abs(float(max_drawdown_dollars))
    if mdd < 1e-9:
        return np.inf if net_profit > 0 else np.nan
    return float(net_profit) / mdd


def serenity_index(equity: pd.Series, daily_returns: pd.Series) -> float:
    """Serenity Ratio — an Ulcer-penalized return that also charges for tail risk.

    Definition used here (documented so it is reproducible):
        serenity = total_return / (ulcer_index * pitfall)
        pitfall  = |CVaR_5% of daily returns| / std(daily returns)

    The pitfall term (~1.6 for a normal distribution) inflates the denominator
    when the loss tail is fatter than normal, so a strategy with rare large
    losses scores lower even at the same Ulcer Index. This mirrors the intent of
    KeyQuant's Serenity Ratio; the exact vendor constant is proprietary, so the
    absolute number will differ from a Tirmann-style report while ranking
    strategies the same way.
    """
    eq = _clean_equity(equity)
    r = _clean_returns(daily_returns)
    if len(eq) < 2 or len(r) < 2:
        return np.nan
    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0 if eq.iloc[0] > 0 else np.nan
    ui = ulcer_index(eq)
    if pd.isna(total_return) or pd.isna(ui) or ui < 1e-9:
        return np.nan
    std = r.std(ddof=1)
    _, cvar = var_cvar_pct(r, level=0.05)
    if std < 1e-12 or pd.isna(cvar):
        return np.nan
    pitfall = abs(cvar) / std
    if pitfall < 1e-9:
        return np.nan
    return float(total_return / (ui * pitfall))


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------

def skewness(returns: pd.Series) -> float:
    r = _clean_returns(returns)
    return float(r.skew()) if len(r) >= 3 else np.nan


def excess_kurtosis(returns: pd.Series) -> float:
    """pandas .kurt() already returns *excess* kurtosis (normal = 0)."""
    r = _clean_returns(returns)
    return float(r.kurt()) if len(r) >= 4 else np.nan


def payoff_ratio(avg_win: float, avg_loss: float) -> float:
    """Payoff ratio = avg win / |avg loss|."""
    if avg_win is None or avg_loss is None or pd.isna(avg_win) or pd.isna(avg_loss):
        return np.nan
    denom = abs(float(avg_loss))
    if denom < 1e-9:
        return np.inf if avg_win > 0 else np.nan
    return float(avg_win) / denom


# ---------------------------------------------------------------------------
# Periodic aggregation (monthly / yearly)
# ---------------------------------------------------------------------------

def _period_end_equity(equity: pd.Series, freq: str) -> pd.Series:
    """Last equity value within each calendar period ('M' or 'Y')."""
    eq = _clean_equity(equity)
    if eq.empty:
        return eq
    return eq.groupby(eq.index.to_period(freq)).last()


def monthly_pnl(equity: pd.Series, initial_equity: float) -> pd.Series:
    """Per-calendar-month P&L in $, indexed by month Period.

    The first month's P&L is measured from ``initial_equity`` so the series sums
    to total net profit."""
    m = _period_end_equity(equity, "M")
    if m.empty:
        return m
    pnl = m.diff()
    pnl.iloc[0] = m.iloc[0] - float(initial_equity)
    return pnl


def monthly_returns(equity: pd.Series, initial_equity: float) -> pd.Series:
    """Per-calendar-month return as a fraction, indexed by month Period."""
    m = _period_end_equity(equity, "M")
    if m.empty:
        return m
    base = m.shift(1)
    base.iloc[0] = float(initial_equity)
    return (m / base) - 1.0


def yearly_returns(equity: pd.Series, initial_equity: float) -> pd.Series:
    y = _period_end_equity(equity, "Y")
    if y.empty:
        return y
    base = y.shift(1)
    base.iloc[0] = float(initial_equity)
    return (y / base) - 1.0


def monthly_pnl_strip(equity: pd.Series, initial_equity: float, n_months: int = 8):
    """Return (labels, values, total) for the trailing ``n_months`` of monthly
    P&L, where total is the sum of the displayed months (matches the reference
    layout's TOTAL cell)."""
    pnl = monthly_pnl(equity, initial_equity)
    if pnl.empty:
        return [], [], 0.0
    tail = pnl.tail(n_months)
    labels = [p.strftime("%b %y") for p in tail.index.to_timestamp()]
    values = [float(v) for v in tail.values]
    return labels, values, float(np.nansum(values))


# ---------------------------------------------------------------------------
# Trailing period returns (1M / 3M / 6M / YTD / 1Y / ITD)
# ---------------------------------------------------------------------------

def period_returns(equity: pd.Series) -> dict:
    """Trailing return over standard windows, each as a fraction.

    A window whose start predates the first equity bar returns NaN (N/A) —
    the window genuinely doesn't fit inside the available history, so a short
    backtest must not silently report its since-inception return as if it
    were a real 3M/6M/YTD/1Y figure. ITD is exempt: it is defined as the
    full-history return, so it always equals inception-to-date."""
    eq = _clean_equity(equity)
    out = {k: np.nan for k in ("1M", "3M", "6M", "YTD", "1Y", "ITD")}
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return out
    end_val = eq.iloc[-1]
    end_date = eq.index[-1]

    def _ret_since(start_date) -> float:
        if start_date < eq.index[0]:
            return np.nan
        prior = eq.loc[:start_date]
        base = prior.iloc[-1] if not prior.empty else eq.iloc[0]
        return float(end_val / base - 1.0) if base > 0 else np.nan

    out["1M"] = _ret_since(end_date - pd.DateOffset(months=1))
    out["3M"] = _ret_since(end_date - pd.DateOffset(months=3))
    out["6M"] = _ret_since(end_date - pd.DateOffset(months=6))
    out["YTD"] = _ret_since(pd.Timestamp(year=end_date.year, month=1, day=1))
    out["1Y"] = _ret_since(end_date - pd.DateOffset(years=1))
    out["ITD"] = float(end_val / eq.iloc[0] - 1.0)
    return out


# ---------------------------------------------------------------------------
# Rolling series (for the page-2 charts)
# ---------------------------------------------------------------------------

def rolling_sharpe(daily_returns: pd.Series, window: int = 126,
                   risk_free_rate: float = 0.0, trading_days: int = 252) -> pd.Series:
    """Annualized rolling Sharpe over ``window`` bars of excess returns."""
    r = _clean_returns(daily_returns)
    if len(r) < window:
        return pd.Series(dtype=float)
    rf_daily = (1 + risk_free_rate) ** (1 / trading_days) - 1
    excess = r - rf_daily
    mean = excess.rolling(window).mean()
    std = excess.rolling(window).std(ddof=1)
    sharpe = (mean / std) * np.sqrt(trading_days)
    return sharpe.replace([np.inf, -np.inf], np.nan).dropna()


def rolling_volatility(daily_returns: pd.Series, window: int = 126,
                       trading_days: int = 252) -> pd.Series:
    """Annualized rolling volatility over ``window`` bars."""
    r = _clean_returns(daily_returns)
    if len(r) < window:
        return pd.Series(dtype=float)
    vol = r.rolling(window).std(ddof=1) * np.sqrt(trading_days)
    return vol.dropna()


# ---------------------------------------------------------------------------
# Transaction fees (best-effort from the trade log)
# ---------------------------------------------------------------------------

_FEE_COLUMNS = ("Commission", "Commissions", "Fees", "Total_Fees",
                "TransactionCost", "Transaction_Cost", "Cost")


def total_fees(trades_df: pd.DataFrame) -> float:
    """Sum of any recognised per-trade fee/commission column; 0.0 when none of
    them are present (Profit is already net of costs in this engine)."""
    if not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return 0.0
    for col in _FEE_COLUMNS:
        if col in trades_df.columns and pd.api.types.is_numeric_dtype(trades_df[col]):
            return float(trades_df[col].abs().sum())
    return 0.0


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def compute_v3_metrics(
    daily_equity: pd.Series,
    daily_returns: pd.Series,
    trades_df: pd.DataFrame,
    core_metrics: dict,
    initial_equity: float,
    max_dd_dollars: float,
    max_dd_pct: float,
    trading_days: int = 252,
    risk_free_rate: float = 0.0,
) -> dict:
    """Compute every extra scalar the v3 tearsheet needs.

    ``core_metrics`` is the dict from calculations.calculate_core_metrics
    (augmented with 'sharpe'/'sortino'/'duration_years' by the caller).
    ``max_dd_dollars`` / ``max_dd_pct`` come from calculate_equity_drawdown
    (percent on a 0..100 scale)."""
    eq = _clean_equity(daily_equity)
    r = _clean_returns(daily_returns)

    total_profit = core_metrics.get("total_profit", np.nan)
    duration_years = core_metrics.get("duration_years", np.nan)
    avg_win = core_metrics.get("avg_win", np.nan)
    avg_loss = core_metrics.get("avg_loss", np.nan)

    final_equity = float(initial_equity) + (total_profit if pd.notna(total_profit) else 0.0)
    from . import calculations
    cagr = (calculations.calculate_cagr(initial_equity, final_equity, duration_years)
            if pd.notna(duration_years) and duration_years > 0 else np.nan)
    calmar = calculations.calculate_calmar(cagr, max_dd_pct)

    mret = monthly_returns(eq, initial_equity)
    yret = yearly_returns(eq, initial_equity)
    m_pnl = monthly_pnl(eq, initial_equity)

    # R expectancy (mean RMultiple)
    r_expectancy = np.nan
    if isinstance(trades_df, pd.DataFrame) and "RMultiple" in trades_df.columns:
        rvals = pd.to_numeric(trades_df["RMultiple"], errors="coerce").dropna()
        if len(rvals) >= 1:
            r_expectancy = float(rvals.mean())

    best_trade = worst_trade = np.nan
    if isinstance(trades_df, pd.DataFrame) and "Profit" in trades_df.columns:
        profit = pd.to_numeric(trades_df["Profit"], errors="coerce").dropna()
        if not profit.empty:
            best_trade = float(profit.max())
            worst_trade = float(profit.min())

    var95, cvar95 = var_cvar_pct(r, level=0.05)
    fees = total_fees(trades_df)

    return {
        # overall performance
        "total_profit": total_profit,
        "gross_profit": total_profit + fees if pd.notna(total_profit) else np.nan,
        "transaction_fees": fees,
        "sharpe": core_metrics.get("sharpe", np.nan),
        "sortino": core_metrics.get("sortino", np.nan),
        "calmar": calmar,
        "omega": omega_ratio(mret if not mret.empty else r, 0.0),
        "gain_to_pain": gain_to_pain(mret if not mret.empty else r),
        "serenity": serenity_index(eq, r),
        "recovery_factor": recovery_factor(total_profit, max_dd_dollars),
        "ret_dd": recovery_factor(total_profit, max_dd_dollars),
        "cagr": cagr,
        # periodic returns
        "avg_monthly_pnl": float(m_pnl.mean()) if not m_pnl.empty else np.nan,
        "best_month": float(mret.max()) if not mret.empty else np.nan,
        "worst_month": float(mret.min()) if not mret.empty else np.nan,
        "best_day": float(r.max()) if not r.empty else np.nan,
        "worst_day": float(r.min()) if not r.empty else np.nan,
        "positive_months": float((mret > 0).mean()) if not mret.empty else np.nan,
        "best_year": float(yret.max()) if not yret.empty else np.nan,
        "worst_year": float(yret.min()) if not yret.empty else np.nan,
        "positive_years": float((yret > 0).mean()) if not yret.empty else np.nan,
        # risk
        "max_dd_pct": max_dd_pct,
        "max_dd_dollars": max_dd_dollars,
        "volatility_ann": annualized_volatility(r, trading_days),
        "var_95": var95,
        "cvar_95": cvar95,
        "ulcer_index": ulcer_index(eq),
        "pct_time_in_dd": pct_time_in_drawdown(eq),
        # distribution
        "skewness": skewness(mret if not mret.empty else r),
        "excess_kurtosis": excess_kurtosis(mret if not mret.empty else r),
        # trades
        "total_trades": core_metrics.get("total_trades", np.nan),
        "win_rate": core_metrics.get("win_rate", np.nan),
        "profit_factor": core_metrics.get("profit_factor", np.nan),
        "expectancy_usd": core_metrics.get("avg_trade_profit", np.nan),
        "r_expectancy": r_expectancy,
        "payoff_ratio": payoff_ratio(avg_win, avg_loss),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_consec_wins": core_metrics.get("max_consecutive_wins", np.nan),
        "max_consec_losses": core_metrics.get("max_consecutive_losses", np.nan),
        # trailing windows
        "period_returns": period_returns(eq),
    }
