from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class PullbackTrendStrategy(BaseStrategy):
    """
    Pullback Trend 전략 (코인용 개선)

    구조
    - Trend filter
    - Setup (눌림)
    - Entry trigger (반등 확인)
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)

        super().__init__("PullbackTrend", default)

    def get_default_params(self) -> dict:
        return {
            "regime": "bullish",
            "setup": {
                "timeframe": "1h",
                "rsi_threshold": 50,
                "bb_position_threshold": 0.6,
            },
            "entry": {
                "timeframe": "15m",
                "rsi_threshold": 45,
                "volume_multiplier": 1.4,
            },
            "exit": {
                "rsi_threshold": 80,
            },
            "position_size_ratio": 1.0,
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = {},
    ) -> Signal:

        holdings = portfolio_info.get("holdings", {})
        is_held = ticker in holdings and holdings[ticker]["volume"] > 0

        current_price = float(entry_market_data.close.iloc[-1])
        prev_price = float(entry_market_data.close.iloc[-2])

        rsi_entry = float(entry_market_data.rsi_14.iloc[-1])
        prev_rsi_entry = float(entry_market_data.rsi_14.iloc[-2])

        ma9 = float(entry_market_data.ma_9.iloc[-1])
        prev_ma9 = float(entry_market_data.ma_9.iloc[-2])

        volume = float(entry_market_data.volume.iloc[-1])
        vol_ma = float(entry_market_data.volume_ma20.iloc[-1])

        # =========================
        # HOLDING → SELL
        # =========================

        if is_held:
            rsi_sell = rsi_entry > self.params["exit"]["rsi_threshold"]

            if rsi_sell:
                strength = min((rsi_entry - 60) / 40 + 0.5, 1.0)
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"Exit rsi:{rsi_entry:.1f} overbought",
                    strength,
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

        rsi_setup = float(setup_market_data.rsi_14.iloc[-1])
        ma20 = float(setup_market_data.ma_20.iloc[-1])
        setup_price = float(setup_market_data.close.iloc[-1])
        bb_pos = float(setup_market_data.bb_position.iloc[-1])

        setup_cfg = self.params["setup"]

        setup_ok = (
            rsi_setup < setup_cfg["rsi_threshold"]
            and bb_pos < setup_cfg["bb_position_threshold"]
            # and setup_price < ma20
        )

        # =========================
        # ENTRY (15m)
        # =========================

        reasons = []
        strength = 0

        rsi_cross_trigger = (
            rsi_entry > self.params["entry"]["rsi_threshold"]
            and prev_rsi_entry <= self.params["entry"]["rsi_threshold"]
        )

        if rsi_cross_trigger:
            strength += 0.4
            reasons.append(
                f"RSI rebound ({rsi_entry:.1f} > {self.params['entry']['rsi_threshold']})"
            )

        ma_cross = prev_price <= prev_ma9 and current_price > ma9

        if ma_cross:
            strength += 0.3
            reasons.append(f"MA9 breakout (P:{current_price:.2f} > MA9:{ma9:.2f})")

        volume_trigger = volume > vol_ma * self.params["entry"]["volume_multiplier"]

        if volume_trigger:
            strength += 0.3
            vol_ratio = (volume / vol_ma) * 100 if vol_ma > 0 else 0
            reasons.append(f"Volume spike ({vol_ratio:.1f}%)")

        if setup_ok and strength >= 0.5:

            size_ratio = self.params["position_size_ratio"]

            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons),
                strength * size_ratio,
            )

        strong_breakout = (
            current_price > prev_price * 1.02
            and volume > vol_ma * 2
            and rsi_entry > 50
            and current_price > ma9
            and not (prev_price > entry_market_data.close.iloc[-3])
        )

        if strong_breakout:
            return Signal(
                SignalType.BUY,
                ticker,
                "Strong breakout",
                0.7,
            )

        return Signal(
            SignalType.HOLD,
            ticker,
            (
                f"Entry 대기 {strength}>0.5, reasons: {reasons}"
                if setup_ok
                else "Setup 미충족"
            ),
            0,
        )
