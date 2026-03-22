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
                "volume_multiplier": 1.4,
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
        # SETUP (1h)
        # =========================
        setup_ok = False
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
        high_20 = entry_market_data.high.rolling(20).max()
        recent_high = high_20.iloc[-2]
        prev_high = high_20.iloc[-3]

        if self.is_downtrend(entry_market_data):
            return Signal(SignalType.HOLD, ticker, "하락 추세", 0)

        reasons = []

        entry_cfg = self.params["entry"]

        breakout = prev_price <= prev_high and price > recent_high

        if not breakout:
            return Signal(
                SignalType.HOLD,
                ticker,
                f"not breakout, price:{price} (>{recent_high})",
                0,
            )

        strength = 0.7
        reasons.append(f"High breakout (P:{price:.2f} > High:{recent_high:.2f})")

        volume_trigger = volume > volume_ma * entry_cfg["volume_multiplier"]

        if volume_trigger:
            strength += 0.2
            vol_ratio = (volume / volume_ma) * 100 if volume_ma > 0 else 0
            reasons.append(f"Volume spike ({vol_ratio:.1f}%)")

        price_acceleration = price > prev_price * 1.003

        if price_acceleration:
            strength += 0.1
            accel_pct = (
                ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
            )
            reasons.append(f"Momentum acceleration ({accel_pct:.2f}%)")

        # 4. 과열 패널티
        if (
            prev_price > entry_market_data.close.iloc[-3] * 1.02
            and price > prev_price * 1.02
        ):
            strength -= 0.2
            reasons.append("Overheating penalty (-0.2)")

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
