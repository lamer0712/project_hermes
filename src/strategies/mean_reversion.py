from typing import Optional, Dict
from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion 전략 (코인용)
    급락 + 과매도 구간에서 반등을 노리는 전략
    """

    def __init__(self, params: dict = None):

        default_params = {
            "regime": "ranging",
            "setup": {
                "timeframe": "1h",
                "rsi_threshold": 45,
                "bb_position_threshold": 0.2,
            },
            "entry": {
                "timeframe": "15m",
                "rsi_threshold": 30,
                "bb_lower_threshold": -0.15,
                "volume_multiplier": 1.5,
                "panic_drop_pct": -5.0,
            },
            "exit": {
                "rsi_threshold": 70,
                "bb_position_threshold": 0.8,
            },
            "position_size_ratio": 0.0,
        }

        if params:
            default_params.update(params)

        super().__init__("MeanReversion", default_params)

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
            return Signal(SignalType.HOLD, ticker, "데이터 부족", 0.0)

        current = entry_market_data.iloc[-1]

        price = float(current.close)
        rsi = float(current.get("rsi_14", 50))
        bb_position = float(current.get("bb_position", 0.5))
        volume = float(current.get("volume", 0))
        vol_ma = float(current.get("volume_ma20", volume))
        change_5 = float(current.get("change_5", 0))

        entry_cfg = self.params["entry"]
        exit_cfg = self.params["exit"]

        # ------------------------------
        # HOLDING → SELL
        # ------------------------------

        if is_held:
            strength = 0
            reasons = ["Exit"]

            if rsi >= exit_cfg["rsi_threshold"]:
                reasons.append(f"RSI 회복 {rsi:.1f}")
                strength += 0.5

            if bb_position >= exit_cfg["bb_position_threshold"]:
                reasons.append(f"BB midline {bb_position:.2f}")
                strength += 0.5

            if strength > 0:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    " ".join(reasons),
                    strength,
                )

            return Signal(SignalType.HOLD, ticker, "보유 중, 추세 유지", 0)

        # ------------------------------
        # SETUP FILTER (1h)
        # ------------------------------

        if setup_market_data is not None and len(setup_market_data) > 0:

            setup = setup_market_data.iloc[-1]

            setup_rsi = float(setup.get("rsi_14", 50))
            setup_bb = float(setup.get("bb_position", 0.5))

            setup_cfg = self.params["setup"]

            if not (
                setup_rsi < setup_cfg["rsi_threshold"]
                or setup_bb < setup_cfg["bb_position_threshold"]
            ):
                return Signal(SignalType.HOLD, ticker, "Setup 미충족", 0)

        # ------------------------------
        # ENTRY
        # ------------------------------

        strength = 0
        reasons = []

        # RSI oversold
        if rsi < entry_cfg["rsi_threshold"]:
            reasons.append(f"RSI 과매도 {rsi:.1f}")
            strength += 0.4

        # BB deep break
        if bb_position < entry_cfg["bb_lower_threshold"]:
            reasons.append(f"BB 하단 이탈 {bb_position:.2f}")
            strength += 0.4

        # volume spike
        if volume > vol_ma * entry_cfg["volume_multiplier"]:
            reasons.append("Volume spike")
            strength += 0.2

        # panic drop
        if change_5 < entry_cfg["panic_drop_pct"]:
            reasons.append(f"Panic drop {change_5:.1f}%")
            strength += 0.3

        if strength >= 0.5:

            size_ratio = self.params["position_size_ratio"]

            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons),
                strength * size_ratio,
            )

        return Signal(
            SignalType.HOLD, ticker, f"Entry 대기 {strength}>0.5, reasons: {reasons}", 0
        )
