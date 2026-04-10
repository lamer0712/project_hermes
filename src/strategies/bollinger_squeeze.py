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
            self.deep_update(default, params)
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
                "volume_multiplier": 1.8,  # 노이즈 필터링 강화 (기존 1.4)
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

        current = entry_market_data.iloc[-1]
        price = float(current.close)
        rsi = float(current.get("rsi_14", 50))
        ma20 = float(current.get("ma_20", price))

        # 0. Data Validation
        validation_error = self.validate_entry_data(ticker, entry_market_data)
        if validation_error:
            return validation_error

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
        if setup_market_data is None or len(setup_market_data) < 20:
            return Signal(SignalType.HOLD, ticker, "Setup data lacking (min 20 candles)", 0, 0)

        macro = setup_market_data.iloc[-1]
        bw = float(macro.get("bb_width", 1.0))

        # Safe rolling calculation
        bw_series = setup_market_data["bb_width"]
        bw_ma = bw_series.rolling(20).mean().iloc[-1]

        # Squeeze check
        if pd.isna(bw_ma):
            bw_ma = bw

        is_squeeze = bw < bw_ma * 0.9 or bw < self.params["setup"]["bw_threshold"]

        if not is_squeeze:
            return Signal(SignalType.HOLD, ticker, "Not in squeeze", 0, 0.1)

        # 2. Trend Filter (60m)
        macro_ema20 = macro.get("ema_20")
        macro_ema50 = macro.get("ema_50")

        if macro_ema20 is None or macro_ema50 is None:
            return Signal(SignalType.HOLD, ticker, "Macro indicators missing", 0, 0.1)

        if float(macro_ema20) < float(macro_ema50):
            return Signal(
                SignalType.HOLD,
                ticker,
                f"Macro Downtrend ({float(macro_ema20):.0f} < {float(macro_ema50):.0f})",
                0,
                0.1,
            )

        # 3. 15m Breakout Check
        bb_upper = float(current.get("bb_upper", price))
        volume = float(current.volume)
        vol_ma = float(current.get("volume_ma20", volume))
        vol_mult = self.params["entry"]["volume_multiplier"]

        is_breakout = price > bb_upper
        is_high_vol = volume > vol_ma * vol_mult
        is_high_rsi = rsi > self.params["entry"]["rsi_threshold"]

        if not is_breakout:
            return Signal(SignalType.HOLD, ticker, "Wait for breakout", 0, 0.2)

        # 4. Filter Breakout Quality
        if not is_high_vol:
            return Signal(
                SignalType.HOLD,
                ticker,
                f"Breakout but Low Vol ({volume:.0f} < {vol_ma * vol_mult:.0f})",
                0,
                0.3,
            )

        if not is_high_rsi:
            return Signal(
                SignalType.HOLD, ticker, f"Breakout but Low RSI ({rsi:.1f})", 0, 0.3
            )

        # 5. 🔥 Bullish Confirmation (15m 종가 양봉 혹은 긴 밑꼬리)
        if not self.is_bullish_candle(entry_market_data):
            return Signal(SignalType.HOLD, ticker, "진입대기 - 양봉/밑꼬리 컨펌 부족", 0, 0.4)

        # 6. ✅ Final Approval
        reasons = ["BB Squeeze Breakout", "High Volume", f"RSI={rsi:.1f}"]
        return Signal(
            SignalType.BUY,
            ticker,
            " | ".join(reasons),
            self.params["position_size_ratio"],
            0.8,
        )

        return Signal(SignalType.HOLD, ticker, "Wait for breakout", 0, 0.2)
