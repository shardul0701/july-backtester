"""
Build ticker-level target weights for S8 Balanced Momentum Defensive Core.

This is an Alpaca/live-prep adapter. It does not place orders. It converts the
portfolio-level S8 idea into a single netted target-weight book:

    Risk-on  : 50% NQ100 mean reversion + 50% momentum rotation
    Risk-off : conservative core approximation:
               42% NQ100 mean reversion + 18% momentum rotation
               + 15% IAU + 15% TLT + 10% UUP

S8 selector:
    If QQQ prior close > prior 20-day MA, use risk-on.
    Otherwise use the conservative core.

All indicators use data available at the as-of close. Orders are intended for
the next session/open. Duplicate tickers across sleeves are netted into one
target weight per symbol.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "live_s8_alpaca"
TRADING_DAYS = 252

INITIAL_CAPITAL = 100_000.0

MR_RSI_PERIOD = 14
MR_RSI_THRESHOLD = 25.0
MR_PRICE_MIN = 10.0
MR_PROFIT_TARGET = 0.12
MR_STOP_LOSS = 0.06
MR_MAX_HOLD_BARS = 15
MR_MA_PERIOD = 20            # MA20-recovery exit (matches rebuild_blended_pit MR_MA_PERIOD)
MR_ALLOC_PER_POSITION = 0.10
MR_MAX_POSITIONS = 5

MOM_LOOKBACK = 126
MOM_TOP_N = 7
MOM_QQQ_MA = 200
MOM_REBAL_DAYS = 5

S8_QQQ_MA = 20


@dataclass
class Position:
    symbol: str
    entry_date: pd.Timestamp
    entry_bar: int
    entry_price: float
    shares: float


def read_json_list(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected JSON list: {path}")
    return [str(x).strip().upper() for x in raw if str(x).strip()]


def load_price_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    date_col = next((c for c in df.columns if c.lower() in {"date", "datetime"}), None)
    if date_col is None:
        raise ValueError(f"No date column in {path}")
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()
    rename = {c: c.title() for c in df.columns if c.lower() in {"open", "high", "low", "close", "volume"}}
    df = df.rename(columns=rename)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    missing = {"Open", "High", "Low", "Close"} - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")
    return df[["Open", "High", "Low", "Close"] + (["Volume"] if "Volume" in df.columns else [])]


def load_symbol(symbol: str) -> pd.DataFrame | None:
    candidates = [
        ROOT / "csv_data" / f"{symbol}.csv",
        ROOT / "data" / "polygon_daily" / f"{symbol.replace(':', '_')}.csv",
        ROOT / "data" / "polygon_daily" / f"I_{symbol}.csv",
        ROOT / "data" / "etf_prices" / f"{symbol}.csv",
        ROOT / "data" / "raw" / f"{symbol}.csv",
    ]
    loaded: list[pd.DataFrame] = []
    for path in candidates:
        if path.exists():
            try:
                df = load_price_csv(path)
                if not df.empty:
                    loaded.append(df)
            except Exception:
                continue
    if loaded:
        merged = loaded[0].copy().sort_index()
        for extra in loaded[1:]:
            extra = extra.copy().sort_index()
            new_rows = extra[extra.index > merged.index.max()].copy()
            if new_rows.empty:
                continue
            overlap = merged.index.intersection(extra.index)
            if len(overlap) >= 5:
                ratios = merged.loc[overlap, "Close"] / extra.loc[overlap, "Close"].replace(0, np.nan)
                scale = ratios.tail(20).median()
                if pd.notna(scale) and scale > 0:
                    price_cols = [c for c in ["Open", "High", "Low", "Close"] if c in new_rows.columns]
                    new_rows[price_cols] = new_rows[price_cols] * float(scale)
            merged = pd.concat([merged, new_rows]).sort_index()
            merged = merged[~merged.index.duplicated(keep="first")]
        return merged
    return None


def load_panel(symbols: list[str], start: str | None = None) -> tuple[dict[str, pd.DataFrame], list[str]]:
    data: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    start_ts = pd.Timestamp(start) if start else None
    for symbol in symbols:
        df = load_symbol(symbol)
        if df is None or df.empty:
            missing.append(symbol)
            continue
        if start_ts is not None:
            df = df[df.index >= start_ts]
        if len(df) < 80:
            missing.append(symbol)
            continue
        data[symbol] = df
    return data, missing


def rsi(close: pd.Series, period: int = MR_RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def close_matrix(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({sym: df["Close"] for sym, df in data.items()}).sort_index()


def open_matrix(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({sym: df["Open"] for sym, df in data.items()}).sort_index()


def align_dates(*frames: pd.DataFrame) -> pd.DatetimeIndex:
    idx = frames[0].index
    for frame in frames[1:]:
        idx = idx.intersection(frame.index)
    return idx.sort_values()


def simulate_mr_weight_book(
    data: dict[str, pd.DataFrame],
    vix: pd.DataFrame | None,
    qqq: pd.DataFrame,
    start_date: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    opens = open_matrix(data)
    closes = close_matrix(data)
    dates = closes.index[closes.index >= pd.Timestamp(start_date)]

    rsi_mat = closes.apply(rsi)
    ret_10d = closes.pct_change(10)
    ma20_mat = closes.rolling(MR_MA_PERIOD, min_periods=MR_MA_PERIOD).mean()

    qqq_close = qqq["Close"].reindex(closes.index).ffill()
    qqq_ma200 = qqq_close.rolling(200).mean()
    qqq_below = qqq_close < qqq_ma200

    if vix is not None and not vix.empty:
        vix_close = vix["Close"].reindex(closes.index).ffill()
        vix_prev = vix_close.shift(1)
        vix_spike = (vix_close - vix_prev) / vix_prev.replace(0, np.nan) > 0.30
        vix_gate = vix_close > 40
        vix_rising = vix_close.diff() > 0
    else:
        vix_spike = pd.Series(False, index=closes.index)
        vix_gate = pd.Series(False, index=closes.index)
        vix_rising = pd.Series(False, index=closes.index)

    crash_gate = qqq_below & vix_rising

    cash = INITIAL_CAPITAL
    positions: dict[str, Position] = {}
    rows: list[dict] = []
    state_rows: list[dict] = []

    date_to_i = {d: i for i, d in enumerate(dates)}

    for date in dates:
        i = date_to_i[date]

        for sym in list(positions):
            pos = positions[sym]
            if sym not in opens.columns or sym not in closes.columns:
                cash += 0.0
                del positions[sym]
                continue
            today_open = opens.at[date, sym] if date in opens.index else np.nan
            today_close = closes.at[date, sym] if date in closes.index else np.nan
            if pd.isna(today_open) or pd.isna(today_close):
                continue

            hold_bars = i - pos.entry_bar
            exit_px = None
            exit_reason = None
            if today_open <= pos.entry_price * (1 - MR_STOP_LOSS):
                exit_px = today_open
                exit_reason = "gap_stop"
            elif today_close <= pos.entry_price * (1 - MR_STOP_LOSS):
                exit_px = pos.entry_price * (1 - MR_STOP_LOSS)
                exit_reason = "stop"
            elif today_close >= pos.entry_price * (1 + MR_PROFIT_TARGET):
                exit_px = pos.entry_price * (1 + MR_PROFIT_TARGET)
                exit_reason = "target"
            elif (
                hold_bars >= 2
                and date in ma20_mat.index
                and sym in ma20_mat.columns
                and pd.notna(ma20_mat.at[date, sym])
                and today_close > ma20_mat.at[date, sym]
            ):
                # MA20-recovery exit: mean reversion complete (matches backtest)
                exit_px = today_close
                exit_reason = "ma20_exit"
            elif hold_bars >= MR_MAX_HOLD_BARS:
                exit_px = today_close
                exit_reason = "max_hold"

            if exit_px is not None:
                cash += pos.shares * exit_px
                state_rows.append(
                    {
                        "date": date.date().isoformat(),
                        "component": "MR",
                        "event": "exit",
                        "symbol": sym,
                        "reason": exit_reason,
                        "price": exit_px,
                    }
                )
                del positions[sym]

        mtm = 0.0
        position_values: dict[str, float] = {}
        for sym, pos in positions.items():
            px = closes.at[date, sym] if sym in closes.columns and date in closes.index else np.nan
            if not pd.isna(px):
                value = pos.shares * px
                position_values[sym] = value
                mtm += value
        equity = cash + mtm

        for sym, value in position_values.items():
            rows.append(
                {
                    "date": date.date().isoformat(),
                    "component": "MR",
                    "symbol": sym,
                    "target_weight": value / equity if equity > 0 else 0.0,
                    "note": "existing_mr_position",
                }
            )

        if i == 0:
            continue
        prev_date = dates[i - 1]
        if bool(vix_gate.get(prev_date, False)) or bool(vix_spike.get(prev_date, False)) or bool(crash_gate.get(prev_date, False)):
            continue

        slots = MR_MAX_POSITIONS - len(positions)
        if slots <= 0 or date not in rsi_mat.index or date not in ret_10d.index:
            continue

        eligible = (
            (rsi_mat.loc[date] < MR_RSI_THRESHOLD)
            & (closes.loc[date] >= MR_PRICE_MIN)
            & rsi_mat.loc[date].notna()
            & ret_10d.loc[date].notna()
        )
        for sym in positions:
            if sym in eligible.index:
                eligible[sym] = False

        candidates = ret_10d.loc[date][eligible].sort_values().head(slots)
        for sym in candidates.index:
            rows.append(
                {
                    "date": date.date().isoformat(),
                    "component": "MR",
                    "symbol": sym,
                    "target_weight": MR_ALLOC_PER_POSITION,
                    "note": "new_mr_entry_for_next_open",
                }
            )
            state_rows.append(
                {
                    "date": date.date().isoformat(),
                    "component": "MR",
                    "event": "entry_signal",
                    "symbol": sym,
                    "reason": "rsi_oversold_ranked_by_10d_return",
                    "price": np.nan,
                }
            )

        # For historical state continuity, book the entries at the next bar open.
        if i + 1 < len(dates):
            next_date = dates[i + 1]
            for sym in candidates.index:
                if sym in positions or sym not in opens.columns:
                    continue
                entry_px = opens.at[next_date, sym] if next_date in opens.index else np.nan
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                alloc = min(equity * MR_ALLOC_PER_POSITION, cash)
                if alloc <= 0:
                    continue
                shares = alloc / entry_px
                cash -= shares * entry_px
                positions[sym] = Position(sym, next_date, i + 1, float(entry_px), float(shares))

    return pd.DataFrame(rows), pd.DataFrame(state_rows)


def simulate_momentum_weight_book(
    data: dict[str, pd.DataFrame],
    qqq: pd.DataFrame,
    gld: pd.DataFrame | None,
    start_date: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    closes = close_matrix(data)
    dates = closes.index[closes.index >= pd.Timestamp(start_date)]
    qqq_close = qqq["Close"].reindex(closes.index).ffill()
    qqq_ma200 = qqq_close.rolling(MOM_QQQ_MA).mean()
    ret_126d = closes.pct_change(MOM_LOOKBACK, fill_method=None)

    # Corporate-action filter: exclude any symbol that had a single-day upward move
    # >30% within the trailing 126d window. Only upward jumps (mergers, spin-off
    # exchanges) inflate the 126d return and produce false momentum signals — large
    # downside moves are real price events that don't corrupt the ranking.
    _daily_ret = closes.pct_change(1, fill_method=None)
    _corp_action_flag = (_daily_ret > 0.50).rolling(MOM_LOOKBACK).max() > 0

    # Spinoff / short-history filter: require at least MOM_LOOKBACK + 252 bars of
    # data (≈ 1 year before the lookback start). Prevents newly listed spinoffs from
    # entering C7 with artificially high 126d returns computed from low IPO pricing
    # (e.g. SNDK +865% after spin-off from WDC).
    _bar_count = closes.notna().cumsum()

    holdings: dict[str, float] = {}
    rows: list[dict] = []
    state_rows: list[dict] = []
    rebal_counter = 0

    for i, date in enumerate(dates):
        if rebal_counter % MOM_REBAL_DAYS == 0 and i > 0:
            prev_date = dates[i - 1]
            q_px = qqq_close.get(prev_date, np.nan)
            q_ma = qqq_ma200.get(prev_date, np.nan)
            risk_on = (not pd.isna(q_px)) and (not pd.isna(q_ma)) and q_px > q_ma
            if risk_on:
                returns = ret_126d.loc[date].dropna()
                if date in _corp_action_flag.index:
                    clean = ~_corp_action_flag.loc[date].reindex(returns.index, fill_value=False)
                    returns = returns[clean]
                if date in _bar_count.index:
                    old_enough = _bar_count.loc[date] >= (MOM_LOOKBACK + 252)
                    returns = returns[old_enough.reindex(returns.index, fill_value=False)]
                top = returns.nlargest(MOM_TOP_N)
                holdings = {sym: 1.0 / len(top) for sym in top.index} if len(top) else {}
                state = "top_momentum"
            else:
                holdings = {"GLD": 1.0} if gld is not None else {}
                state = "gld_bear_mode"
            state_rows.append(
                {
                    "date": date.date().isoformat(),
                    "component": "Momentum",
                    "event": "rebalance",
                    "symbol": ",".join(holdings),
                    "reason": state,
                    "price": np.nan,
                }
            )
        rebal_counter += 1

        for sym, weight in holdings.items():
            rows.append(
                {
                    "date": date.date().isoformat(),
                    "component": "Momentum",
                    "symbol": sym,
                    "target_weight": weight,
                    "note": "momentum_current_holding",
                }
            )

    return pd.DataFrame(rows), pd.DataFrame(state_rows)


def weights_on_date(book: pd.DataFrame, date: str, component: str) -> dict[str, float]:
    sub = book[(book["date"] == date) & (book["component"] == component)]
    if sub.empty:
        return {}
    return sub.groupby("symbol")["target_weight"].sum().to_dict()


def scaled(weights: dict[str, float], multiplier: float, sleeve: str) -> list[dict]:
    return [
        {
            "sleeve": sleeve,
            "symbol": sym,
            "sleeve_weight": multiplier,
            "component_weight": weight,
            "target_weight": weight * multiplier,
        }
        for sym, weight in weights.items()
        if abs(weight * multiplier) > 1e-8
    ]


def fixed_etf_weights(weights: dict[str, float], sleeve: str) -> list[dict]:
    return [
        {
            "sleeve": sleeve,
            "symbol": sym,
            "sleeve_weight": weight,
            "component_weight": 1.0,
            "target_weight": weight,
        }
        for sym, weight in weights.items()
        if abs(weight) > 1e-8
    ]


def build_s8_targets(
    mr_book: pd.DataFrame,
    mom_book: pd.DataFrame,
    qqq: pd.DataFrame,
    as_of: str | None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    available_dates = sorted(set(mr_book["date"]).union(set(mom_book["date"])))
    if not available_dates:
        raise ValueError("No component target dates generated.")
    if as_of is None:
        as_of = available_dates[-1]
    if as_of not in available_dates:
        prior = [d for d in available_dates if d <= as_of]
        if not prior:
            raise ValueError(f"No target date on or before {as_of}")
        as_of = prior[-1]

    q_close = qqq["Close"].copy()
    q_ma = q_close.rolling(S8_QQQ_MA).mean()
    ts = pd.Timestamp(as_of)
    q_px = q_close[q_close.index <= ts].iloc[-1]
    q_ma_value = q_ma[q_ma.index <= ts].iloc[-1]
    risk_on = bool((not pd.isna(q_px)) and (not pd.isna(q_ma_value)) and q_px > q_ma_value)

    mr = weights_on_date(mr_book, as_of, "MR")
    mom = weights_on_date(mom_book, as_of, "Momentum")

    rows: list[dict] = []
    if risk_on:
        state = "risk_on"
        rows.extend(scaled(mr, 0.50, "risk_on_mr_50"))
        rows.extend(scaled(mom, 0.50, "risk_on_momentum_50"))
    else:
        state = "risk_off_conservative_core"
        rows.extend(scaled(mr, 0.42, "conservative_mr_42"))
        rows.extend(scaled(mom, 0.18, "conservative_momentum_18"))
        rows.extend(fixed_etf_weights({"IAU": 0.15, "TLT": 0.15, "UUP": 0.10}, "conservative_defensive_etf_40"))

    sleeve_df = pd.DataFrame(rows)
    if sleeve_df.empty:
        net = pd.DataFrame(columns=["symbol", "target_weight"])
        duplicate_count = 0
    else:
        duplicate_count = int((sleeve_df.groupby("symbol")["sleeve"].nunique() > 1).sum())
        net = (
            sleeve_df.groupby("symbol", as_index=False)["target_weight"]
            .sum()
            .sort_values("target_weight", ascending=False)
        )
    net.insert(0, "date", as_of)
    net.insert(1, "strategy_id", "S8_ORDER_LEVEL_V1")
    net.insert(2, "selector_state", state)

    metadata = {
        "as_of": as_of,
        "selector_state": state,
        "qqq_close": float(q_px),
        "qqq_ma20": float(q_ma_value) if not pd.isna(q_ma_value) else None,
        "gross_target_weight": float(net["target_weight"].abs().sum()) if not net.empty else 0.0,
        "net_target_weight": float(net["target_weight"].sum()) if not net.empty else 0.0,
        "position_count": int(len(net)),
        "pre_net_duplicate_symbol_count": duplicate_count,
        "duplicate_policy": "net duplicate tickers across sleeves by summing target weights",
    }
    if not sleeve_df.empty:
        sleeve_df.insert(0, "date", as_of)
        sleeve_df.insert(1, "strategy_id", "S8_ORDER_LEVEL_V1")
        sleeve_df.insert(2, "selector_state", state)
    return net, sleeve_df, metadata


def load_current_positions(path: Path | None, account_equity: float) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame(columns=["symbol", "current_weight"])
    df = pd.read_csv(path)
    symbol_col = next((c for c in df.columns if c.lower() in {"symbol", "ticker"}), None)
    if symbol_col is None:
        raise ValueError("Current positions file needs a symbol/ticker column.")
    if "current_weight" in df.columns:
        out = df[[symbol_col, "current_weight"]].copy()
    else:
        value_col = next((c for c in df.columns if c.lower() in {"market_value", "value", "position_value"}), None)
        if value_col is None:
            raise ValueError("Positions file needs current_weight or market_value/value.")
        out = df[[symbol_col, value_col]].copy()
        out["current_weight"] = pd.to_numeric(out[value_col], errors="coerce").fillna(0.0) / account_equity
        out = out[[symbol_col, "current_weight"]]
    out = out.rename(columns={symbol_col: "symbol"})
    out["symbol"] = out["symbol"].astype(str).str.upper()
    return out.groupby("symbol", as_index=False)["current_weight"].sum()


def build_order_preview(targets: pd.DataFrame, current: pd.DataFrame, account_equity: float) -> pd.DataFrame:
    cur = current.copy()
    tgt = targets[["symbol", "target_weight"]].copy()
    merged = pd.merge(tgt, cur, on="symbol", how="outer")
    merged["target_weight"] = pd.to_numeric(merged["target_weight"], errors="coerce").fillna(0.0)
    merged["current_weight"] = pd.to_numeric(merged["current_weight"], errors="coerce").fillna(0.0)
    merged["delta_weight"] = merged["target_weight"] - merged["current_weight"]
    merged["target_dollars"] = merged["target_weight"] * account_equity
    merged["current_dollars"] = merged["current_weight"] * account_equity
    merged["delta_dollars"] = merged["delta_weight"] * account_equity
    merged["action"] = np.where(
        merged["delta_weight"] > 1e-5,
        "BUY",
        np.where(merged["delta_weight"] < -1e-5, "SELL", "HOLD"),
    )
    return merged.sort_values("delta_dollars", key=lambda s: s.abs(), ascending=False)


def write_readme(path: Path, metadata: dict, missing_mr: list[str], missing_mom: list[str]) -> None:
    lines = [
        "# S8 Alpaca Target-Weight Adapter",
        "",
        "This folder contains the first ticker-level conversion of selected strategy S8.",
        "",
        "## Strategy",
        "",
        "- Selector: QQQ prior close above its 20-day moving average = risk-on.",
        "- Risk-on: 50% mean reversion sleeve + 50% momentum sleeve.",
        "- Risk-off: 42% mean reversion + 18% momentum + 15% IAU + 15% TLT + 10% UUP.",
        "- Duplicate tickers across sleeves are netted into one final target weight.",
        "- This script does not place Alpaca orders; it creates target weights and an order-delta preview.",
        "",
        "## Latest Run",
        "",
        f"- As-of date: `{metadata['as_of']}`",
        f"- Selector state: `{metadata['selector_state']}`",
        f"- QQQ close: `{metadata['qqq_close']:.4f}`",
        f"- QQQ MA20: `{metadata['qqq_ma20']:.4f}`" if metadata["qqq_ma20"] is not None else "- QQQ MA20: `NA`",
        f"- Gross target weight: `{metadata['gross_target_weight']:.4f}`",
        f"- Net target weight: `{metadata['net_target_weight']:.4f}`",
        f"- Position count: `{metadata['position_count']}`",
        "",
        "## Files",
        "",
        "- `S8_latest_target_weights.csv`: final net ticker target weights.",
        "- `S8_sleeve_target_breakdown.csv`: pre-net sleeve/component target weights.",
        "- `S8_order_preview.csv`: BUY/SELL/HOLD deltas versus supplied/current-empty portfolio.",
        "- `S8_component_events.csv`: MR and momentum signal/rebalance events.",
        "- `S8_run_metadata.json`: run metadata and caveats.",
        "",
        "## Caveats",
        "",
        "- This is order-level v1. It is designed for paper/shadow forward testing first.",
        "- It uses an explicit conservative-core approximation because the frozen handover B1 curve was not an order-level strategy file.",
        "- Before live money, reconcile this target book against the exact July engine fills and Alpaca fractional/share constraints.",
    ]
    if missing_mr or missing_mom:
        lines += [
            "",
            "## Missing Local Symbols",
            "",
            f"- MR universe missing/skipped: {len(missing_mr)}",
            f"- Momentum universe missing/skipped: {len(missing_mom)}",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build S8 Alpaca-ready target weights.")
    parser.add_argument("--mr-universe", default="tickers_to_scan/nasdaq_100.json")
    parser.add_argument("--momentum-universe", default="tickers_to_scan/sp-500.json")
    parser.add_argument("--start-date", default="2016-09-01")
    parser.add_argument("--as-of", help="YYYY-MM-DD. Defaults to latest common generated target date.")
    parser.add_argument("--account-equity", type=float, default=100_000.0)
    parser.add_argument("--current-positions", help="Optional CSV with symbol/current_weight or market_value.")
    parser.add_argument("--output-dir", default=str(OUT_DIR))
    return parser.parse_args()


# =============================================================================
# S8 = QQQ/MA20 switch: bull -> SSD book, bear -> B1_35 book
# (matches rebuild_blended_pit.simulate_s8_pit; replaces the old 50/50 model)
# =============================================================================

B1_35_C7_ALLOC = 0.35                 # S8 bear-mode C7 alloc (C7_ALLOC_35)
B1_35_DEF_SYMS = ["IAU", "TLT", "UUP"]
VT12_TARGET = 12.0                    # VoltTarget-12% applied to B1_35 (apply_vt)
VT12_LOOKBACK = 20


def _c7_weights_at(mom_book: pd.DataFrame, as_of: str, alloc: float) -> dict[str, float]:
    """C7 sleeve on as_of scaled by `alloc`. GLD/bear -> cash (empty)."""
    sub = mom_book[(mom_book["date"] == as_of) & (mom_book["component"] == "Momentum")]
    holds = sub.groupby("symbol")["target_weight"].sum().to_dict()
    holds = {s: w for s, w in holds.items() if s != "GLD"}
    return {s: w * alloc for s, w in holds.items()}


def _nq_mr_weights_at(mr_nq_book: pd.DataFrame, as_of: str) -> dict[str, float]:
    if mr_nq_book.empty:
        return {}
    sub = mr_nq_book[(mr_nq_book["date"] == as_of) & (mr_nq_book["component"] == "MR")]
    if sub.empty:
        return {}
    return sub.groupby("symbol")["target_weight"].sum().to_dict()


def _vt12_scale(raw_weights: dict[str, float], closes: pd.DataFrame) -> float:
    """Portfolio VT-12% scalar = min(1, 12 / annualised 20d vol of the book).

    Vol is reconstructed from the CURRENT raw weights applied to trailing-20d
    returns — the same approximation the deployed daily_pipeline._portfolio_vt12_scale
    uses, and consistent with the backtest's portfolio-level apply_vt.
    """
    if not raw_weights:
        return 1.0
    rets = closes.pct_change(fill_method=None)
    cols = [s for s in raw_weights if s in rets.columns]
    if not cols:
        return 1.0
    port = sum(raw_weights[s] * rets[s].fillna(0.0) for s in cols)
    tail = port.tail(VT12_LOOKBACK)
    if len(tail) < VT12_LOOKBACK:
        return 1.0
    vol = float(tail.std() * (252 ** 0.5) * 100)
    if not np.isfinite(vol) or vol <= 0:
        return 1.0
    return min(1.0, VT12_TARGET / vol)


def build_b1_35_book(
    mr_nq_book: pd.DataFrame, mom_book: pd.DataFrame, as_of: str, closes: pd.DataFrame
) -> tuple[dict[str, float], float]:
    """B1_35 = C7@35% + NQ100-MR + remaining-cash IAU/TLT/UUP, then x VT12 scale.

    Mirrors simulate_b1_b2_pit(c7_alloc=0.35, defensive=[IAU,TLT,UUP]) + apply_vt(12%).
    """
    raw: dict[str, float] = {}
    for s, w in _c7_weights_at(mom_book, as_of, B1_35_C7_ALLOC).items():
        raw[s] = raw.get(s, 0.0) + w
    for s, w in _nq_mr_weights_at(mr_nq_book, as_of).items():
        raw[s] = raw.get(s, 0.0) + w
    remaining = max(0.0, 1.0 - sum(raw.values()))
    if remaining > 1e-6:
        for s in B1_35_DEF_SYMS:
            raw[s] = raw.get(s, 0.0) + remaining / len(B1_35_DEF_SYMS)
    scale = _vt12_scale(raw, closes)
    return {s: w * scale for s, w in raw.items()}, scale


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    nq_syms = read_json_list(ROOT / args.mr_universe)        # NQ100: MR sleeve (B2/B1_35)
    sp_syms = read_json_list(ROOT / args.momentum_universe)  # SP500: C7 momentum + B5 SP500-MR
    nq_data, missing_nq = load_panel(nq_syms)
    sp_data, missing_sp = load_panel(sp_syms)

    qqq = load_symbol("QQQ")
    if qqq is None:
        raise SystemExit("QQQ data not found.")
    vix = load_symbol("VIX")
    gld = load_symbol("GLD")

    # Component books (shared sims — same assembly as build_ssd)
    mr_nq_book, _ = simulate_mr_weight_book(nq_data, vix, qqq, args.start_date)   # NQ100 MR
    mr_sp_book, _ = simulate_mr_weight_book(sp_data, vix, qqq, args.start_date)   # SP500 MR (B5)
    mom_book, _ = simulate_momentum_weight_book(sp_data, qqq, gld, args.start_date)  # C7

    # Deferred import: build_ssd imports build_s8 at module load — import here to avoid the cycle.
    from build_ssd_alpaca_targets import build_ssd_book, b5_equity_curve

    sp_closes = close_matrix(sp_data)
    b5_eq = b5_equity_curve(mr_sp_book, sp_closes, qqq, gld, args.start_date)
    ssd_book_df, ssd_meta = build_ssd_book(
        mr_sp_book, mr_nq_book, mom_book, b5_eq, qqq, gld, args.as_of
    )
    as_of = ssd_meta["as_of"]

    # B1_35 bear book — needs a combined closes panel (C7 SP500 + NQ MR + ETFs) for VT12.
    etf_closes = {}
    for s in B1_35_DEF_SYMS:
        d = load_symbol(s)
        if d is not None and "Close" in d.columns:
            etf_closes[s] = d["Close"]
    nq_closes = close_matrix(nq_data)
    combined = pd.concat([sp_closes, nq_closes, pd.DataFrame(etf_closes)], axis=1)
    combined = combined.loc[:, ~combined.columns.duplicated()].sort_index()
    b1_35_dict, vt_scale = build_b1_35_book(mr_nq_book, mom_book, as_of, combined)

    # S8 switch (matches simulate_s8_pit): bull = QQQ as-of close > MA20 -> SSD, else B1_35.
    qclose = qqq["Close"]
    qma20 = qclose.rolling(S8_QQQ_MA).mean()
    ts = pd.Timestamp(as_of)
    pri = qclose.index[qclose.index <= ts]
    bull = bool(len(pri) > 0 and pd.notna(qma20.get(pri[-1]))
                and qclose.get(pri[-1]) > qma20.get(pri[-1]))
    state = "bull_ssd" if bull else "bear_b1_35"

    if bull:
        net = ssd_book_df[["symbol", "target_weight"]].copy()
    else:
        net = pd.DataFrame(
            [{"symbol": s, "target_weight": round(w, 6)}
             for s, w in sorted(b1_35_dict.items(), key=lambda kv: -kv[1]) if abs(w) > 1e-6]
        )
    if net.empty:
        net = pd.DataFrame(columns=["symbol", "target_weight"])
    net = net[net["target_weight"].abs() > 1e-6].sort_values("target_weight", ascending=False)
    net.insert(0, "date", as_of)
    net.insert(1, "strategy_id", "S8_DMVC35_ORDER_LEVEL_V2")
    net.insert(2, "selector_state", state)

    current = load_current_positions(
        Path(args.current_positions) if args.current_positions else None, args.account_equity)
    order_preview = build_order_preview(net, current, args.account_equity)

    net.to_csv(out_dir / "S8_latest_target_weights.csv", index=False)
    order_preview.to_csv(out_dir / "S8_order_preview.csv", index=False)
    metadata = {
        "as_of": as_of,
        "selector_state": state,
        "regime": "bull" if bull else "bear",
        "ssd_mode": ssd_meta.get("mode"),
        "b1_35_vt_scale": round(vt_scale, 4),
        "gross_target_weight": round(float(net["target_weight"].sum()), 4) if not net.empty else 0.0,
        "position_count": int(len(net)),
        "missing_nq": len(missing_nq),
        "missing_sp": len(missing_sp),
        "account_equity_for_preview": args.account_equity,
        "model": "S8 = QQQ/MA20 switch(SSD bull, B1_35 bear) — matches simulate_s8_pit",
    }
    (out_dir / "S8_run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"Wrote {out_dir / 'S8_latest_target_weights.csv'}")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
