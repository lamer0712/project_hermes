# Walkthrough: Portfolio Logging Improvements

This document outlines the specific changes implemented to address the missing metrics in trading logs, which will help us debug poor ROI and fine-tune strategy parameters in the future.

## 1. Changes Made

### A. Strategy Metric Logging Enhancements

We discovered that breakout, pullback, and mean reversion strategies were producing logs without their respective indicator values (e.g., just logging "Volume spike" instead of the exact percentage).

**Files Modified:**
- [breakout.py](file:///Users/lamer/Project/stock/project_hermes/src/strategies/breakout.py)
  - `Upper band breakout` → Now explicitly logs the current price and the targeted upper Bollinger Band value it surpassed.
  - `Volume spike` → Now calculates and logs the volume percentage versus the 20MA volume.
  - `Momentum acceleration` → Now logs the exact acceleration percentage compared to the previous tick.
- [pullback_trend.py](file:///Users/lamer/Project/stock/project_hermes/src/strategies/pullback_trend.py)
  - `RSI rebound` → Logs the exact RSI entry value that crossed the threshold.
  - `MA9 breakout` → Logs the current price versus the MA9.
  - `Volume spike` → Logs the exact volume ratio.
- [mean_reversion.py](file:///Users/lamer/Project/stock/project_hermes/src/strategies/mean_reversion.py)
  - `Volume spike` → Updated alongside other existing detailed logs to include the exact volume percentage.

### B. Execution Transparency in the Manager

The actual buy execution logs did not previously print the exact current price or the effective Stop-Loss percentage at that exact moment.

**Files Modified:**
- [manager.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/manager.py)
  - Updated the log format inside [_execute_buy](file:///Users/lamer/Project/stock/project_hermes/src/utils/manager.py#290-396) to append Stop-Loss % (`SL`) and the exact evaluated target price (`CP`).
  - Example of new log format:
    `🟢 매수 실행: KRW-DOOD | 금액: 22,000 KRW | SL: -5.0% | Target Price: CP 5.30`

## 2. Structural & Logic Improvements (Phase 2)

Based on the deep analysis of continuous losses, the following structural improvements were applied to correct the strategy behavior.

### A. Regime-Strategy Remapping ([manager.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/manager.py))
- **Before**: `volatile` regime used [Breakout](file:///Users/lamer/Project/stock/project_hermes/src/strategies/breakout.py#6-178) strategy. (Buying tops in a choppy market).
- **After**: `volatile` regime now uses [MeanReversion](file:///Users/lamer/Project/stock/project_hermes/src/strategies/mean_reversion.py#6-154). [Breakout](file:///Users/lamer/Project/stock/project_hermes/src/strategies/breakout.py#6-178) is moved to `ranging` regime where it can grab explosive moves out of tightly consolidated ranges.

### B. Dynamic ATR-based Stop-Loss ([risk_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/risk_manager.py), [manager.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/manager.py))
- **Before**: Static -5.5% Stop-Loss regardless of coin volatility.
- **After**: [manager.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/manager.py) fetches the 14-period `ATR` at execution time and saves it as [holding_metadata(atr_14)](file:///Users/lamer/Project/stock/project_hermes/src/utils/portfolio_manager.py#291-333). The [RiskManager](file:///Users/lamer/Project/stock/project_hermes/src/utils/risk_manager.py#5-153) evaluates this ATR explicitly and scales the Stop Loss to `ATR * 2.5` (max 15%). The `-6%` and `-12%` partial Stop-Loss steps are dynamically scaled downwards accordingly. This prevents Whipsaw and gives Altcoins room to breathe.

### C. Breakout Chase Filter ([breakout.py](file:///Users/lamer/Project/stock/project_hermes/src/strategies/breakout.py))
- **Added**: Built-in spike filter. If the 15m candle has already skyrocketed >3% over the previous period, the strategy will block entry and return `HOLD`.

## 3. Profit Suffocation Fixes (Phase 3)

We found that while the Stop-Loss was loosened, the Take-Profit and Trailing Stops were still tight, suffocating long-term profitability.

### A. Dynamic Trailing-Stop & TP Scaling ([risk_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/risk_manager.py))
- **Before**: Static Take Profit (+10~12%) and extremely tight Trailing Stop (+3% trigger, -2% drop).
- **After**: The system now dynamically multiplies the `take_profit_pct`, `trailing_start_pct`, and `trailing_stop_pct` relative to the evaluated ATR. E.g., if the ATR scales the SL by 2x, the Trailing Stop start trigger and safety net distance are also widened by 2x. This gives the asset enough room to fluctuate (e.g. going up 5%, pulling back 3%, and shooting to 15%) without being prematurely halted.

### B. Strategy Extrapolation ([breakout.py](file:///Users/lamer/Project/stock/project_hermes/src/strategies/breakout.py), [pullback_trend.py](file:///Users/lamer/Project/stock/project_hermes/src/strategies/pullback_trend.py))
- **Before**: Existed entirely when RSI simply reached 65 or 70.
- **After**: Raised the breakout RSI exit threshold from `70` to `85`. Removed the "Bollinger Upper Touch" premature sell logic from `pullback_trend`, relying solely on the RSI momentum deterioration (now shifted to `80`). This permits "band walking" in explosive trends, boosting the Risk/Reward ratio.

## 4. Validation Results

- The python compilation test (`python -m py_compile`) was executed successfully over all modified strategy and manager scripts to ensure no syntax errors were introduced during string formatting.
- [task.md](file:///Users/lamer/.gemini/antigravity/brain/82e237f3-b11e-4c8f-b306-e3f731ed2790/task.md) was completed successfully.

With these changes, the backend logs will explicitly display the mathematical thresholds when triggering trades, enabling easy diagnosis of "fakeouts" and tight stop losses.
