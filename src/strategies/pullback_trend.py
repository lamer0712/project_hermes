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
                "rsi_threshold": 85,
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

        holdings, is_held = self.parse_holdings(ticker, portfolio_info)

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
            ma20 = float(entry_market_data.ma_20.iloc[-1])
            rsi_sell = rsi_entry > self.params["exit"].get("rsi_threshold", 85)

            if rsi_sell:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"[익절] RSI 극과열 ({rsi_entry:.1f})",
                    1.0,
                    1.0,
                )

            if current_price < ma20:
                avg_price = holdings[ticker].get("avg_price", 0)
                tag = "[익절]" if current_price > avg_price else "[손절]"
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"{tag} 생명선(MA20) 이탈",
                    1.0,
                    1.0,
                )

            return Signal(
                SignalType.HOLD,
                ticker,
                "보유유지 - 추세 유지 중",
                0,
                0.0,
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
        if not setup_ok:
            return Signal(SignalType.HOLD, ticker, "진입대기 - Setup 미충족", 0, 0.01)

        # =========================
        # ENTRY (15m)
        # =========================

        reasons = []
        strength = 0

        if self.is_downtrend(entry_market_data):
            return Signal(SignalType.HOLD, ticker, "진입대기 - 하락세", 0, 0.1)

        rsi_cross_trigger = (
            rsi_entry > self.params["entry"]["rsi_threshold"]
            and prev_rsi_entry <= self.params["entry"]["rsi_threshold"]
        )

        if rsi_cross_trigger:
            strength += 0.4
            reasons.append(f"RSI반등({rsi_entry:.0f})")

        ma_cross = prev_price <= prev_ma9 and current_price > ma9

        if ma_cross:
            strength += 0.3
            reasons.append(f"MA9돌파")

        volume_trigger = volume > vol_ma * self.params["entry"]["volume_multiplier"]

        if volume_trigger:
            strength += 0.3
            vol_ratio = (volume / vol_ma) * 100 if vol_ma > 0 else 0
            reasons.append(f"거래량급증({vol_ratio:.0f}%)")

        if strength >= 0.5:

            size_ratio = self.params["position_size_ratio"]

            rsi_bonus = self.rsi_tiebreaker(rsi_entry, mode="oversold")
            final_conf = min(strength + rsi_bonus, 1.0)

            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons),
                strength * size_ratio,
                final_conf,
            )

        strong_breakout = (
            current_price > prev_price * 1.02
            and volume > vol_ma * 2
            and rsi_entry > 50
            and current_price > ma9
            and not (prev_price > entry_market_data.close.iloc[-3])
        )

        if strong_breakout:
            size_ratio = self.params["position_size_ratio"]
            rsi_bonus = self.rsi_tiebreaker(rsi_entry, mode="momentum")
            return Signal(
                SignalType.BUY,
                ticker,
                "강한 돌파 (Strong Breakout)",
                0.7 * size_ratio,
                min(0.8 + rsi_bonus, 1.0),
            )

        return Signal(
            SignalType.HOLD,
            ticker,
            (
                f"진입대기 - 점수:{strength:.1f}"
                if setup_ok
                else "진입대기 - (Setup 미충족)"
            ),
            0,
            strength,
        )
