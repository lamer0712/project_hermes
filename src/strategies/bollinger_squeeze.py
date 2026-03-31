from typing import Optional, Dict
from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd
import numpy as np


class BollingerSqueezeStrategy(BaseStrategy):
    """
    Bollinger Band Squeeze Strategy

    Logic:
    1. Setup: Bollinger Band Width narrow (Squeeze) on 1h timeframe.
    2. Filter: 60m MA20 > MA50 (Bullish trend context).
    3. Entry: 15m Price breaks above Upper Bollinger Band with high volume.
    4. Exit: RSI overheating (>80) or price falls back below 15m MA20.
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("BollingerSqueeze", default)

    def get_default_params(self) -> dict:
        return {
            "regime": "ranging",  # Squeeze usually starts in ranging/low vol
            "setup": {
                "timeframe": "1h",
                "bw_threshold": 0.10,  # Adaptive Bandwidth threshold
            },
            "entry": {
                "timeframe": "15m",
                "volume_multiplier": 1.4,
                "rsi_threshold": 50,
            },
            "exit": {
                "rsi_threshold": 80,
            },
            "position_size_ratio": 0.6,
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = None,
    ) -> Signal:
        holdings, is_held = self.parse_holdings(ticker, portfolio_info)

        if entry_market_data is None or entry_market_data.empty:
            return Signal(SignalType.HOLD, ticker, "No entry data", 0, 0)

        current = entry_market_data.iloc[-1]
        price = float(current.close)
        rsi = float(current.get("rsi_14", 50))
        ma20 = float(current.get("ma_20", price))

        # =========================
        # HOLDING -> SELL
        # =========================
        if is_held:
            # RSI Overheat
            if rsi > self.params["exit"]["rsi_threshold"]:
                return Signal(
                    SignalType.SELL, ticker, f"RSI Overheat ({rsi:.1f})", 1.0, 1.0
                )

            # MA20 Exit
            if price < ma20:
                return Signal(SignalType.SELL, ticker, "Price < MA20", 1.0, 1.0)

            return Signal(SignalType.HOLD, ticker, "Trend holds", 0, 0)

        # =========================
        # ENTRY
        # =========================

        # 1. 1h Setup Check (Squeeze)
        if setup_market_data is None or setup_market_data.empty:
            return Signal(SignalType.HOLD, ticker, "No setup data", 0, 0)

        macro = setup_market_data.iloc[-1]
        bw = float(macro.get("bb_width", 1.0))

        # Safe rolling calculation
        if "bb_width" in setup_market_data.columns:
            bw_ma = setup_market_data["bb_width"].rolling(20).mean().iloc[-1]
        else:
            bw_ma = bw  # Fallback if column still missing

        # Squeeze check: current bandwidth < 20-period avg bandwidth
        is_squeeze = bw < bw_ma * 0.9 or bw < self.params["setup"]["bw_threshold"]

        if not is_squeeze:
            # logger.debug(f"[BollingerSqueeze] {ticker} Not in squeeze: bw={bw:.4f}, bw_ma={bw_ma:.4f}")
            return Signal(SignalType.HOLD, ticker, "Not in squeeze", 0, 0.1)

        # 2. Trend Filter (60m)
        macro_ema20 = float(macro.get("ema_20", price))
        macro_ema50 = float(macro.get("ema_50", price)) if "ema_50" in macro else price
        if macro_ema20 < macro_ema50:
            return Signal(
                SignalType.HOLD,
                ticker,
                f"Macro Downtrend ({macro_ema20:.0f} < {macro_ema50:.0f})",
                0,
                0.1,
            )

        # 3. 15m Breakout Check
        bb_upper = float(current.get("bb_upper", price))
        volume = float(current.volume)
        vol_ma = float(current.get("volume_ma20", volume))

        is_breakout = price > bb_upper
        is_high_vol = volume > vol_ma * self.params["entry"]["volume_multiplier"]

        if is_breakout:
            if not is_high_vol:
                return Signal(
                    SignalType.HOLD,
                    ticker,
                    f"Breakout but Low Vol ({volume:.0f} < {vol_ma*1.4:.0f})",
                    0,
                    0.3,
                )
            if rsi <= self.params["entry"]["rsi_threshold"]:
                return Signal(
                    SignalType.HOLD, ticker, f"Breakout but Low RSI ({rsi:.1f})", 0, 0.3
                )

        if is_breakout and is_high_vol and rsi > self.params["entry"]["rsi_threshold"]:
            reasons = ["BB Squeeze Breakout", "High Volume", f"RSI={rsi:.1f}"]
            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons),
                self.params["position_size_ratio"],
                0.8,
            )

        return Signal(SignalType.HOLD, ticker, "Wait for breakout", 0, 0.2)
