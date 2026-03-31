# Backtest & Telegram Metrics Implementation

I have implemented and verified the requested performance metrics (MDD, PF, RR Ratio) across both the backtesting system and the live Telegram reporting interface.

## Changes Made

### 1. Database & Persistence Layer
- **[db.py](file:///Users/lamer/Project/stock/project_hermes/src/data/db.py)**
    - Added `peak_value` and `max_drawdown` columns to the `portfolios` table for persistent MDD tracking in live trading.
    - Updated save/load logic for the new metrics.

### 2. Portfolio Management Layer
- **[portfolio_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py)**
    - Implemented [update_drawdown()](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py#477-499): Automatically updates historical peak value and Maximum Drawdown (MDD) whenever total value is calculated.
    - Added Profit Factor (PF) and **Risk-Reward (RR) Ratio** calculation.
    - Updated markdown reports (`portfolio.md`) to include these new metrics.

### 3. Telegram & Command Handlers
- **[command_handler.py](file:///Users/lamer/Project/stock/project_hermes/src/communication/command_handler.py)**
    - Updated the `/status` command to display MDD, PF, and RR Ratio in the overview.
- **[strategy_report.py](file:///Users/lamer/Project/stock/project_hermes/src/data/strategy_report.py)**
    - Updated the `/report` command to include PF and RR Ratio for each trading strategy and the global portfolio.

### 4. Backtest Engine
- **[backtest_system.py](file:///Users/lamer/Project/stock/project_hermes/src/backtest_system.py)**
    - Integrated with the updated [PortfolioManager](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py#11-763) to display MDD, PF, and **Risk-Reward (RR) Ratio** in the final results.

## Key Metrics Added

| Metric | Calculation | Usage |
|--------|-------------|-------|
| **MDD** | (Peak - Current) / Peak | Risk management and stability assessment. |
| **PF** | Total Gross Profit / Total Gross Loss | Strategy consistency and efficiency. |
| **RR Ratio** | Avg Profit / Avg Loss | Risk management and per-trade quality. |

## Verification Results

A 5-day backtest was executed to verify the new metrics:
- **ROI**: -0.03%
- **Win Rate**: 37.5% 
- **Profit Factor**: 0.96
- **Risk-Reward Ratio**: 1.59
- **MDD**: -0.31%

These indicators are now fully functional and will be displayed in your Telegram `/status` and `/report` commands.
