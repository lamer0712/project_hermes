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
                "bb_width_threshold": 0.05,
                "adx_threshold": 18,
            },
            "entry": {
                "timeframe": "15m",
                "volume_multiplier": 1.6,
                "breakout_buffer": 0.002,
            },
            "exit": {
                "rsi_threshold": 70,
            },
            "position_size_ratio": 0.5,
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        regime: str,
        portfolio_info: dict = None,
    ) -> Signal:

        holdings = portfolio_info.get("holdings", {}) if portfolio_info else {}
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
            # RSI 과열
            if rsi > self.params["exit"]["rsi_threshold"]:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"Exit rsi:{rsi} overbought",
                    0.7,
                )

            return Signal(
                SignalType.HOLD,
                ticker,
                "보유 중, 추세 유지",
                0,
            )

        # =========================
        # SETUP (1h)
        # =========================

        if setup_market_data is not None and len(setup_market_data) > 0:

            setup = setup_market_data.iloc[-1]

            bb_width = float(setup.get("bb_width", 0))
            adx = float(setup.get("adx_14", 0))

            setup_cfg = self.params["setup"]

            setup_ok = (
                bb_width < setup_cfg["bb_width_threshold"]
                and adx > setup_cfg["adx_threshold"]
            )

            if not setup_ok:
                return Signal(
                    SignalType.HOLD,
                    ticker,
                    "Setup 미충족",
                    0,
                )

        # =========================
        # ENTRY (15m)
        # =========================

        reasons = []
        strength = 0

        entry_cfg = self.params["entry"]

        breakout_buffer = entry_cfg["breakout_buffer"]

        breakout = price > bb_upper * (1 + breakout_buffer)

        if breakout:
            strength += 0.5
            reasons.append("Upper band breakout")

        volume_trigger = volume > volume_ma * entry_cfg["volume_multiplier"]

        if volume_trigger:
            strength += 0.4
            reasons.append("Volume spike")

        price_acceleration = price > prev_price * 1.003

        if price_acceleration:
            strength += 0.2
            reasons.append("Momentum acceleration")

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
