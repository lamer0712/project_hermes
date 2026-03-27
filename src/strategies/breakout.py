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
                "rsi_threshold": 88,
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

        holdings, is_held = self.parse_holdings(ticker, portfolio_info)

        hold_signal = self.validate_entry_data(ticker, entry_market_data)
        if hold_signal:
            return hold_signal

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
            ma20 = float(current.get("ma_20", price))

            # RSI 초강력 과열 (밴드워킹 중 조기 청산 방지 위해 상향)
            if rsi > self.params["exit"].get("rsi_threshold", 88):
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"[익절] RSI 극과열 ({rsi:.1f})",
                    1.0,
                    1.0,
                )

            # 2연속 봉 MA20 하회 시에만 청산 (단발 노이즈 필터링)
            prev_ma20 = float(prev.get("ma_20", prev_price))
            if price < ma20 and prev_price < prev_ma20:
                avg_price = holdings[ticker].get("avg_price", 0)
                tag = "[익절]" if price > avg_price else "[손절]"
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"{tag} 생명선(MA20) 2연속 이탈",
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
        # ENTRY (15m)
        # =========================
        recent_high = entry_market_data.high.rolling(10).max().iloc[-2]
        prev_prev_close = entry_market_data.close.iloc[-3]

        reasons = []

        entry_cfg = self.params["entry"]

        # not breakout (확실한 돌파: 고점 대비 +0.3% 초과 필요)
        if price <= recent_high * 1.003:
            return Signal(SignalType.HOLD, ticker, "진입대기 - 돌파 조건 미달", 0, 0.0)

        strength = 0.3

        # volume trigger
        if volume > volume_ma * entry_cfg["volume_multiplier"]:
            strength += 0.2
            vol_ratio = (volume / volume_ma) * 100 if volume_ma > 0 else 0
            reasons.append("거래량터짐")

        # price acceleration
        if price > prev_price * 1.002:
            strength += 0.2
            accel_pct = (
                ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
            )
            reasons.append("가속도붙음")

        # pullback breakout
        if price > recent_high * 0.995 and prev_prev_close > prev_price < price:
            strength += 0.2
            reasons.append("눌림목돌파")

        # Overheating penalty
        if prev_price > prev_prev_close * 1.02 and price > prev_price * 1.02:
            strength *= 0.8
            reasons.append("단기과열감점")

        # downtrend penalty
        if self.is_downtrend(entry_market_data):
            strength *= 0.7
            reasons.append("역배열감점")

        if strength >= 0.6:
            size_ratio = self.params["position_size_ratio"]

            rsi_val = float(entry_market_data.iloc[-1].get("rsi_14", 50))
            rsi_bonus = self.rsi_tiebreaker(rsi_val, mode="momentum")
            final_conf = min(strength + rsi_bonus, 1.0)

            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons),
                strength * size_ratio,
                final_conf,
            )

        return Signal(
            SignalType.HOLD,
            ticker,
            f"진입대기 - 점수미달{strength:.1f}",
            0,
            0.0,
        )
