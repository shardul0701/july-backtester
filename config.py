"""
Platform-default configuration for july-backtester.

Edit the values below before running.  Every key is documented; do NOT rename
or remove keys — the engine validates against a known allowlist in
helpers/config_validator.py and will warn on typos.

Research-specific presets (e.g. config_weekly_rsi.py) live next to this file.
Copy the preset over config.py temporarily when you want to reproduce a
specific experiment, and restore this file when you are done.
"""

CONFIG = {
    # ============================================================
    # SECTION 1: DATA PROVIDER
    # ============================================================
    # "polygon" | "norgate" | "yahoo" | "csv"
    # polygon_api_secret_name: name of the env-var / .env key that holds the key.
    "data_provider": "polygon",
    "polygon_api_secret_name": "POLYGON_API_KEY",
    # Only used when data_provider = "csv":
    "csv_data_dir": "csv_data",
    # Only used when data_provider = "norgate" and the merged dataset is present:
    # "merged_data_root": os.environ.get("MERGED_DATA_ROOT", ""),

    # ============================================================
    # SECTION 2: BACKTEST PERIOD & CAPITAL
    # ============================================================
    "start_date": "2004-01-01",
    "end_date": "2026-12-31",  # update this or set to today's date before running
    "initial_capital": 100000.0,

    # ============================================================
    # SECTION 3: TIMEFRAME
    # ============================================================
    # "D" = daily  |  "W" = weekly  |  "M" = monthly
    # "H" = hourly  |  "MIN" = minute-level
    "timeframe": "D",
    "timeframe_multiplier": 1,

    # ============================================================
    # SECTION 4: PRICE ADJUSTMENT & BENCHMARKS
    # ============================================================
    # comparison_tickers drives which symbols are downloaded alongside your
    # portfolio.  role="benchmark" → buy-and-hold return column in summary.
    # role="dependency" → available to strategies (spy_df, vix_df).
    # role="both" → benchmark + dependency.
    "price_adjustment": "none",
    "benchmark_symbol": "SPY",
    "comparison_tickers": [
        {"symbol": "SPY",   "role": "both"},
        {"symbol": "QQQ",   "role": "benchmark"},
        {"symbol": "I:VIX", "role": "dependency"},
        {"symbol": "I:TNX", "role": "dependency"},
    ],

    # ============================================================
    # SECTION 5: FILE OUTPUT
    # ============================================================
    "save_individual_trades": True,

    # ============================================================
    # SECTION 6: SUMMARY FILTERING
    # ============================================================
    # Strategies below these thresholds are omitted from the printed table.
    # Set all to -9999 / 1.0 during initial exploration to see everything.
    "mc_score_min_to_show_in_summary": 0,
    "min_pandl_to_show_in_summary": 0,
    "max_acceptable_drawdown": 0.30,
    "min_performance_vs_spy": -9999.0,
    "min_performance_vs_qqq": -9999.0,
    "save_only_filtered_trades": False,
    "show_qqq_losers": True,

    # ============================================================
    # SECTION 7: PORTFOLIO
    # ============================================================
    # A single ticker:        {"My Run": ["AAPL"]}
    # Pre-built JSON list:    {"Nasdaq 100": "nasdaq_100.json"}
    # PIT universe:           {"NQ100 PIT": "pit:nq100"}  or  "pit:sp500"
    # Norgate watchlist:      {"My WL": "norgate:WatchlistName"}
    "min_bars_required": 200,
    "portfolios": {
        "My Symbols": ["AAPL"],
    },

    # ============================================================
    # SECTION 8: ALLOCATION & EXECUTION
    # ============================================================
    "allocation_per_trade": 0.10,   # fraction of equity per position
    "max_pct_adv": 0.05,            # max order size as % of 20-day avg daily volume
    "execution_time": "open",       # "open" = next-day open fill
    "roc_thresholds": [0.0, 0.5],

    # ============================================================
    # SECTION 9: STOP LOSS
    # ============================================================
    # {"type": "none"} | {"type": "percentage", "value": 0.05}
    # | {"type": "atr", "multiplier": 2.0}
    "stop_loss_configs": [
        {"type": "none"},
    ],

    # ============================================================
    # SECTION 10: MONTE CARLO
    # ============================================================
    "min_trades_for_mc": 50,
    "num_mc_simulations": 1000,

    # ============================================================
    # SECTION 11: WALK-FORWARD ANALYSIS
    # ============================================================
    "wfa_split_ratio": 0.80,        # 0.80 = 80% IS / 20% OOS; None = disabled
    "wfa_folds": None,              # None = rolling WFA disabled; int >=2 = folds
    "wfa_min_fold_trades": 5,

    # ============================================================
    # SECTION 12: TRADING COSTS
    # ============================================================
    "slippage_pct": 0.0005,         # 5 bps flat bid/ask spread per trade
    "commission_per_share": 0.002,  # $0.002 per share
    "risk_free_rate": 0.05,         # annual, used in Sharpe (US T-bill proxy)

    # ============================================================
    # SECTION 13: STRESS TESTING
    # ============================================================
    "noise_injection_pct": 0.0,     # 0.0 = disabled; 0.01 = ±1% price noise

    # ============================================================
    # SECTION 14: STRATEGY SELECTION
    # ============================================================
    # "all" = run every registered plugin.
    # Or a list of exact names: ["RSI Mean Reversion (14/30)", "MACD Crossover"]
    "strategies": "all",

    # ============================================================
    # SECTION 15: SENSITIVITY SWEEP
    # ============================================================
    "sensitivity_sweep_enabled": False,
    "sensitivity_sweep_pct": 0.20,
    "sensitivity_sweep_steps": 2,
    "sensitivity_sweep_min_val": 2,

    # ============================================================
    # SECTION 16: ROLLING SHARPE
    # ============================================================
    "rolling_sharpe_window": 126,   # trading days; 0 or None = disable

    # ============================================================
    # SECTION 17: SHORT SELLING BORROW COST
    # ============================================================
    "htb_rate_annual": 0.0,         # 0.0 = disabled (long-only default); set to e.g. 0.02 for short strategies

    # ============================================================
    # SECTION 18: MONTE CARLO SAMPLING
    # ============================================================
    "mc_sampling": "iid",           # "iid" = independent; "block" = block-bootstrap
    "mc_block_size": None,          # None = auto floor(sqrt(N))

    # ============================================================
    # SECTION 19: VOLUME-BASED MARKET IMPACT
    # ============================================================
    "volume_impact_coeff": 0.0,     # 0.0 = disabled; 0.1 = mild institutional

    # ============================================================
    # SECTION 20: MISC OUTPUT
    # ============================================================
    "export_ml_features": False,
    "verbose_output": False,
    "exclude_open_positions": False,
    "upload_to_s3": False,
    "s3_reports_bucket": "",

    # ============================================================
    # SECTION 21: POINT-IN-TIME (PIT) MEMBERSHIP ENFORCEMENT
    # ============================================================
    # Only relevant for PIT portfolios ("pit:nq100" / "pit:sp500").
    # When pit_enforce_daily=True each symbol is gated to its index-membership
    # spells: warm-up bars (kept for indicator continuity) and gap bars (while it
    # was out of the index) stay in the frame but are NEVER traded.
    # _pit_force_exit marks the last available member bar when no timely post-leave
    # bar exists, so the simulator can close at Close instead of waiting.
    "pit_enforce_daily": True,
    "pit_warmup_days": 400,              # calendar days of pre-join data for indicators
    "pit_exit_buffer_days": 10,          # grace-period bars after index removal
    "pit_coverage_tolerance_days": 7,
    "merged_quality_filter_enabled": True,
    "merged_exclude_statuses": [
        "insufficient_history", "review_no_patch", "identity_review", "flagged",
    ],
    "merged_min_avg_dollar_volume": 0.0,  # opt-in liquidity floor (0 = off)

    # ============================================================
    # SECTION 22: FORWARD TESTING — CAPITAL ISOLATION MODEL
    # ============================================================
    # "isolated" (default): each strategy gets a fixed dollar slice so backtest
    # P&L maps 1:1 to forward P&L.  "shared" is planned but not yet implemented.
    "forward_test_mode": {
        "capital_model": "isolated",
        "strategy_capital_allocation": {},  # {} = initial_capital / N per strategy
    },

    # ============================================================
    # SECTION 23: ALPACA PAPER TRADING
    # ============================================================
    # API keys are read from env vars / .env (never hardcoded here).
    # Run:  python scripts/alpaca_paper_runner.py --run-id <run_id> [--dry-run]
    "alpaca": {
        "api_key_env": "APCA_API_KEY_ID",
        "secret_key_env": "APCA_API_SECRET_KEY",
        "base_url": "https://paper-api.alpaca.markets",
        "order_timeout_seconds": 300,
    },
}
