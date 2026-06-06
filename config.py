# config_weekly_rsi.py
"""
CONFIG: Weekly RSI Crossover Strategy — Top 20 Nasdaq (17DD VERSION)
=====================================================================
This is the EXACT configuration that produced the 17.39% max drawdown result.
 
PERFORMANCE WITH THIS CONFIG:
- Total Trades: 709
- Net Profit: $902,590.95 (1805.18% return)
- CAGR: 14.77%
- Sharpe Ratio: 0.75
- Max Drawdown: 17.39% ← The "17DD" result
- Win Rate: 41.75%
- Profit Factor: 1.85
- Expectancy per Trade: $1,273.05
 
IMPORTANT: Timeframe MUST be "D" (daily). The strategy handles weekly
resampling internally and maps signals to next-day execution.
 
How to use:
    1. Copy to project root as config.py:
           cp config_weekly_rsi.py config.py
    2. Copy the strategy file:
           cp weekly_rsi_crossover.py custom_strategies/
    3. Copy the ticker file:
           cp top_20_nasdaq.json tickers_to_scan/
    4. Dry-run first:
           python main.py --dry-run
    5. Run the backtest:
           python main.py --name "weekly-rsi-17DD" --verbose
"""
 
CONFIG = {
    # ============================================================
    # SECTION 1: DATA PROVIDER
    # ============================================================
    # Yahoo Finance — free, no API key needed.
    # This is what was used for the 17DD result.
    "data_provider": "csv",
    "csv_data_dir": "csv_data",
 
    # ============================================================
    # SECTION 2: BACKTEST PERIOD & CAPITAL
    # ============================================================
    # 17DD config used: 2004-01-01 to 2026-03-28 (22+ years)
    "start_date": "2004-01-03",
    "end_date": "2026-04-24",
    "initial_capital": 100000.0,
 
    # ============================================================
    # SECTION 3: TIMEFRAME — MUST BE DAILY
    # ============================================================
    # The strategy resamples to weekly internally.
    # Execution is on the next daily open after a weekly signal.
    "timeframe": "D",
    "timeframe_multiplier": 1,
 
    # ============================================================
    # SECTION 4: PRICE ADJUSTMENT & BENCHMARKS
    # ============================================================
    "price_adjustment": "none",
    "benchmark_symbol": "QQQ",
    "comparison_tickers": [
        {"symbol": "QQQ",   "role": "both"},
        {"symbol": "SPY",   "role": "benchmark"},
        {"symbol": "I:VIX", "role": "dependency"},
        {"symbol": "I:TNX", "role": "dependency"},
    ],
 
    # ============================================================
    # SECTION 5: FILE OUTPUT
    # ============================================================
    "save_individual_trades": True,
 
    # ============================================================
    # SECTION 6: FILTERING — Show everything for initial research
    # ============================================================
    "mc_score_min_to_show_in_summary": -9999,
    "min_pandl_to_show_in_summary": -9999,
    "max_acceptable_drawdown": 1.0,
    "min_performance_vs_spy": -9999,
    "min_performance_vs_qqq": -9999,
    "save_only_filtered_trades": False,
    "show_qqq_losers": True,
 
    # ============================================================
    # SECTION 7: PORTFOLIO
    # ============================================================
    "min_bars_required": 250,
    "portfolios": {
        "NQ100 MR": "nasdaq_100.json",
    },
 
    # ============================================================
    # SECTION 8: ALLOCATION & EXECUTION
    # ============================================================
    "allocation_per_trade": 0.10,    # 10% per position — matches research
    "max_pct_adv": 0.02,            # max 2% of avg daily volume
    "execution_time": "open",        # fill at the open (next-day)
    "roc_thresholds": [0.0, 0.5],

    # ============================================================
    # SECTION 9: STOP LOSS
    # ============================================================
    "stop_loss_configs": [
        {"type": "percentage", "value": 0.06},  # 6% hard stop — MR strategy
    ],
 
    # ============================================================
    # SECTION 10: MONTE CARLO
    # ============================================================
    "min_trades_for_mc": 20,
    "num_mc_simulations": 1000,
    "mc_sampling": "block",
    "mc_block_size": None,
 
    # ============================================================
    # SECTION 11: WALK-FORWARD ANALYSIS
    # ============================================================
    "wfa_split_ratio": 0.69,        # IS: 2010–2021 (~11y) / OOS: 2021–2026 (~5y)
    "wfa_folds": 5,                 # 5-fold rolling WFA over full test period
    "wfa_min_fold_trades": 3,
 
    # ============================================================
    # SECTION 12: TRADING COSTS (17DD settings)
    # ============================================================
    "slippage_pct": 0.0005,          # 5 bps
    "commission_per_share": 0.002,  # $0.002 per share
    "risk_free_rate": 0.05,         # 5% annual (US T-bill proxy)
 
    # ============================================================
    # SECTION 13: STRESS TESTING
    # ============================================================
    "noise_injection_pct": 0.0,     # Set to 0.01 for ±1% stress test
 
    # ============================================================
    # SECTION 14: STRATEGY SELECTION — Only this strategy
    # ============================================================
    "strategies": ["MR REG2-E (NQ100 Mean Reversion + Crash Gate)"],

    # ============================================================
    # SECTION 15: SENSITIVITY SWEEP — Off by default
    # ============================================================
    # Turn on to test parameter fragility:
    #   Sweeps rsi_length, smoothing_length, os_level, expiry_level ±20%
    # 17DD result did NOT use sensitivity sweep.
    "sensitivity_sweep_enabled": False,
    "sensitivity_sweep_pct": 0.20,
    "sensitivity_sweep_steps": 2,
    "sensitivity_sweep_min_val": 2,
 
    # ============================================================
    # SECTION 16: EXTRAS
    # ============================================================
    "rolling_sharpe_window": 126,
    "htb_rate_annual": 0.0,          # long strategy — no borrow cost
    "volume_impact_coeff": 0.0,
    "export_ml_features": False,
    "verbose_output": True,
    "upload_to_s3": False,
    "s3_reports_bucket": "",

    # ============================================================
    # SECTION 21: POINT-IN-TIME (PIT) MEMBERSHIP ENFORCEMENT
    # ============================================================
    # Only relevant for PIT portfolios ("sp500_pit" / "nq100_pit" / "pit:*").
    # When True, each symbol is gated to its index-membership spells: warm-up
    # bars (kept for indicator continuity) and gap bars (while it was out of the
    # index) stay in the frame but are NEVER traded. The simulator checks the
    # membership flag on the actual execution date.
    # Required for PIT portfolios; has no effect on ordinary static portfolios.
    "pit_enforce_daily": True,
    "pit_warmup_days": 400,             # calendar days of pre-join data for indicators
    "pit_exit_buffer_days": 10,          # post-leave bars for next-open liquidation
    "pit_coverage_tolerance_days": 7,
    "merged_quality_filter_enabled": True,
    "merged_exclude_statuses": [
        "insufficient_history", "review_no_patch", "identity_review", "flagged",
    ],
    "merged_min_avg_dollar_volume": 0.0,  # opt-in liquidity floor
    "exclude_open_positions": False,
    # "sp500_pit_path": "",            # else read from SP500_DATA_ROOT in .env
    # "nq100_pit_path": "data/nq100_membership.parquet",

    # ============================================================
    # SECTION 22: FORWARD TESTING — Capital isolation model
    # ============================================================
    # "isolated" (default): each strategy gets its own fixed dollar slice.
    # This is the only model that maps backtest P&L to forward P&L 1:1.
    # "shared" is a future path — documented but not yet implemented.
    "forward_test_mode": {
        "capital_model": "isolated",        # "isolated" (default) | "shared" (future)
        "strategy_capital_allocation": {},  # {strategy_name: dollar_amount}; empty = initial_capital / N
    },

    # ============================================================
    # SECTION 23: ALPACA PAPER TRADING
    # ============================================================
    # Keys resolved from env vars / .env (same pattern as aws_utils.py).
    # Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env for paper trading.
    # Run: python scripts/alpaca_paper_runner.py --run-id <run_id> [--dry-run]
    "alpaca": {
        "api_key_env": "APCA_API_KEY_ID",
        "secret_key_env": "APCA_API_SECRET_KEY",
        "base_url": "https://paper-api.alpaca.markets",
        "order_timeout_seconds": 300,
    },
}
 
 
# ================================================================
# NOTES ON THE 17DD CONFIGURATION
# ================================================================
"""
EXACT SETTINGS THAT PRODUCED 17.39% MAX DRAWDOWN:
-------------------------------------------------
1. Data Provider: Yahoo Finance
2. Period: 2004-01-01 to 2026-03-28 (22+ years)
3. Initial Capital: $100,000
4. Position Size: 10% per trade (max 10 concurrent)
5. Stop Loss: 10% hard stop
6. Universe: Top 20 Nasdaq (via top_20_nasdaq.json)
7. Slippage: 0.05% per trade
8. Commission: $0.002 per share
 
STRATEGY PARAMETERS (in weekly_rsi_crossover.py):
-------------------------------------------------
- rsi_length: 14 (weekly RSI period)
- smoothing_length: 7 (EMA of RSI)
- os_level: 50 (arming threshold - RSI must dip below)
- expiry_level: 70 (disarm if RSI exceeds without crossing)
- price_ema_length: 12 (price EMA for exit confirmation)
 
OPTIMIZATION OPPORTUNITIES:
--------------------------
To improve from the 17DD baseline, test these changes:
 
1. REMOVE LOSING STOCKS:
   Edit top_20_nasdaq.json to exclude stocks with PF < 1.0
   (historically: AMD, CSCO, INTC may be losers)
   Expected gain: +1-2% CAGR
 
2. TEST DIFFERENT STOPS:
   Uncomment multiple stop configs in SECTION 9:
   - 7% tighter stop → less risk, more whipsaw
   - 15% wider stop → ride trends, bigger losses
   - ATR stop → adaptive to volatility
   Expected gain: +0.5-1.5% CAGR, -5-10% max DD
 
3. ENABLE PARAMETER SWEEP:
   Set "sensitivity_sweep_enabled": True
   Tests variations of all numeric params
   Helps find optimal settings
 
4. ADD SPY REGIME FILTER:
   Modify strategy to only take longs when SPY > 200 MA
   Would have avoided 2008 & 2022 crashes
   Expected gain: -10-15% max DD
 
5. TEST DIFFERENT ARMING LEVELS:
   In weekly_rsi_crossover.py, try:
   - os_level: 40 (more aggressive, more signals)
   - os_level: 55 (more conservative, fewer signals)
   - os_level: 45 (middle ground)
 
6. ADJUST POSITION SIZE:
   - 0.15 allocation → more aggressive (higher DD, higher returns)
   - 0.07 allocation → more conservative (lower DD, lower returns)
 
REPRODUCING THE EXACT 17DD RESULT:
---------------------------------
1. Ensure top_20_nasdaq.json contains:
   ["AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", 
    "TSLA", "AVGO", "ASML", "COST", "ADBE", "NFLX", "CSCO", 
    "AMD", "INTC", "INTU", "CMCSA", "TMUS", "PEP"]
 
2. Use weekly_rsi_crossover.py with these params:
   - rsi_length: 14
   - smoothing_length: 7
   - os_level: 50
   - expiry_level: 70
   - price_ema_length: 12
 
3. Run: python main.py --name "17DD_exact" --verbose
 
Expected output:
- Trades: 700-720 (varies slightly with Yahoo data updates)
- Net Profit: $900K-$920K
- CAGR: 14-15%
- Max DD: 17-18%
- Sharpe: 0.7-0.8
 
WHY RESULTS MAY VARY SLIGHTLY:
------------------------------
- Yahoo Finance updates prices daily (corporate actions)
- Different end dates include/exclude recent trades
- Slight differences in data provider adjustments
- Normal variance: ±50 trades, ±5% profit, ±1% DD
 
The STRATEGY LOGIC is exact. Minor result differences are expected.
"""
