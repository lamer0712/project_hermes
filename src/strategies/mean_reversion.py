from typing import Optional, Dict
from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion 전략 (코인용)
    급락 + 과매도 구간에서 반등을 노리는 전략
    """

    def __init__(self, params: dict = None):

        default_params = self.get_default_params()

        if params:
            default_params.update(params)

        super().__init__("MeanReversion", default_params)

    def get_default_params(self):

        return {
            "regime": "ranging",
            "setup": {
                "timeframe": "1h",
                "rsi_threshold": 48,
                "bb_position_threshold": 0.25,
            },
            "entry": {
                "timeframe": "15m",
                "rsi_threshold": 33,
                "bb_lower_threshold": 0.12,
                "volume_multiplier": 1.3,
                "panic_drop_pct": -0.04,
            },
            "exit": {
                "rsi_threshold": 70,
                "bb_position_threshold": 0.8,
            },
            "position_size_ratio": 0.3,
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = None,
    ) -> Signal:

        holdings, is_held = self.parse_holdings(ticker, portfolio_info)

        hold_signal = self.validate_entry_data(ticker, entry_market_data)
        if hold_signal:
            return hold_signal

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
                reasons.append(f"RSI회복({rsi:.0f})")
                strength += 0.5

            if bb_position >= exit_cfg["bb_position_threshold"]:
                reasons.append(f"BB중앙도달")
                strength += 0.5

            if strength > 0:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    " ".join(reasons),
                    strength,
                    1.0,
                )

            return Signal(SignalType.HOLD, ticker, "보유유지 - 추세 유지 중", 0, 0.0)

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
                return Signal(
                    SignalType.HOLD, ticker, "진입대기 - Setup 미충족", 0, 0.01
                )

        # ------------------------------
        # ENTRY
        # ------------------------------
        prev_price = entry_market_data.close.iloc[-2]

        # 반등 확인
        if price <= prev_price or self.is_downtrend(entry_market_data):
            return Signal(SignalType.HOLD, ticker, "진입대기 - 하락세", 0, 0.1)

        is_fake_dip, reason = self.is_fake_dip(entry_market_data)
        if is_fake_dip:
            return Signal(
                SignalType.HOLD, ticker, f"진입대기 - 가짜 눌림목: {reason}", 0, 0.2
            )

        conditions = 0
        reasons = []

        if rsi < entry_cfg["rsi_threshold"]:
            conditions += 1
            reasons.append(f"RSI침체")

        if bb_position < entry_cfg["bb_lower_threshold"]:
            conditions += 1
            reasons.append(f"BB이탈")

        if volume > vol_ma * entry_cfg["volume_multiplier"]:
            conditions += 1
            reasons.append("투매거래량")

        if change_5 < entry_cfg["panic_drop_pct"]:
            conditions += 1
            reasons.append(f"단기급락({change_5*100:.1f}%)")

        conf = min(0.4 + (conditions * 0.15), 1.0)
        if conditions >= 2:

            rsi_val = float(entry_market_data.iloc[-1].get("rsi_14", 50))
            rsi_bonus = self.rsi_tiebreaker(rsi_val, mode="oversold")
            final_conf = min(conf + rsi_bonus, 1.0)

            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons),
                conf * self.params["position_size_ratio"],
                final_conf,
            )

        return Signal(SignalType.HOLD, ticker, f"진입대기", 0, conf)
