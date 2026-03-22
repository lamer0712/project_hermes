from typing import Optional, Dict
from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class BreakoutStrategy(BaseStrategy):
    """
    Breakout Strategy (코인용 개선)

    구조
    - Setup (변동성 수축)
    - Entry (돌파 + 거래량)
    - Exit (모멘텀 약화)
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()

        if params:
            default.update(params)

        super().__init__("Breakout", default)

    def get_default_params(self):

        return {
            "regime": "volatile",
            "setup": {
                "timeframe": "1h",
                "bb_width_threshold": 0.06,
                "adx_threshold": 12,
            },
            "entry": {
                "timeframe": "15m",
                "volume_multiplier": 1.3,
                "breakout_buffer": 0.002,
            },
            "exit": {
                "rsi_threshold": 85,
            },
            "position_size_ratio": 0.5,
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = None,
    ) -> Signal:

        holdings = portfolio_info.get("holdings", {})
        is_held = ticker in holdings and holdings[ticker]["volume"] > 0

        if entry_market_data is None or len(entry_market_data) < 20:
            return Signal(SignalType.HOLD, ticker, "데이터 부족", 0)

        current = entry_market_data.iloc[-1]
        prev = entry_market_data.iloc[-2]

        price = float(current.close)
        prev_price = float(prev.close)

        volume = float(current.volume)
        volume_ma = float(current.get("volume_ma20", volume))

        bb_upper = float(current.get("bb_upper", price))
        bb_mid = float(current.get("bb_mid", price))

        rsi = float(current.get("rsi_14", 50))

        # =========================
        # HOLDING → SELL
        # =========================

        if is_held:
            # RSI 초강력 과열 (밴드워킹 중 조기 청산 방지)
            if rsi > self.params["exit"]["rsi_threshold"]:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"Exit rsi:{rsi:.1f} extreme overbought",
                    0.8,
                )

            return Signal(
                SignalType.HOLD,
                ticker,
                "보유 중, 추세 유지",
                0,
            )

        # =========================
        # ENTRY (15m)
        # =========================
        recent_high = entry_market_data.high.rolling(10).max().iloc[-2]
        prev_prev_close = entry_market_data.close.iloc[-3]

        reasons = []

        entry_cfg = self.params["entry"]

        strength = 0.5

        # breakout
        if price > recent_high * 0.998:
            strength += 0.3
            reasons.append("breakout")

        # volume trigger
        if volume > volume_ma * entry_cfg["volume_multiplier"]:
            strength += 0.2
            vol_ratio = (volume / volume_ma) * 100 if volume_ma > 0 else 0
            reasons.append(f"Volume")

        # price acceleration
        if price > prev_price * 1.002:
            strength += 0.2
            accel_pct = (
                ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
            )
            reasons.append(f"Momentum")

        # pullback breakout
        if price > recent_high * 0.995 and prev_prev_close > prev_price < price:
            strength += 0.2
            reasons.append("pullback")

        # Overheating penalty
        if prev_price > prev_prev_close * 1.02 and price > prev_price * 1.02:
            strength *= 0.8
            reasons.append("(-)Overheating")

        # downtrend penalty
        if self.is_downtrend(entry_market_data):
            strength *= 0.7
            reasons.append("(-)Downtrend")

        if strength >= 0.6:

            size_ratio = self.params["position_size_ratio"]

            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons),
                strength * size_ratio,
            )

        return Signal(
            SignalType.HOLD,
            ticker,
            f"Entry 대기 {strength}>0.6, reasons: {reasons}",
            0,
        )
