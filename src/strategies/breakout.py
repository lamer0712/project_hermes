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
                "bb_width_threshold": 0.05,  # 0.06 -> 0.05 (더 강한 수축 요구)
                "adx_threshold": 12,
            },
            "entry": {
                "timeframe": "15m",
                "volume_multiplier": 2.2,  # 1.5 -> 2.2 (가짜 돌파 방지 위해 대폭 상향)
                "breakout_buffer": 0.005,  # 0.003 -> 0.005 (확실한 돌파 버퍼)
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
        bb_lower = float(current.get("bb_lower", price))
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 1.0

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
        # 1. 수축 확인: 밴드 폭이 임계값(0.05) 이하여야 에너지가 응축됨
        if bb_width > self.params["setup"]["bb_width_threshold"]:
             return Signal(SignalType.HOLD, ticker, f"진입대기 - 변동성 발산 중 (BB Width:{bb_width:.3f})", 0, 0.05)

        # 2. 거시적 하락세(60분봉 정배열 확인: EMA 20 > 50 > 200) 필터링 추가
        if not self.is_bullish_trend_htf(setup_market_data):
            return Signal(
                SignalType.HOLD,
                ticker,
                "진입대기 - 거시 하락/횡보장(60m 역배열) 필터링",
                0,
                0.1,
            )

        # 3. RSI 과매수 필터링 (70 -> 75로 완화하여 강한 추세 초입 허용)
        if rsi > 75:
            return Signal(SignalType.HOLD, ticker, "진입대기 - RSI 이미 과열", 0, 0.1)

        # 4. 돌파 고점 확인 (20캔들 전고점 사용) 룩백 확장
        recent_high = entry_market_data.high.rolling(20).max().iloc[-2]
        prev_prev_close = entry_market_data.close.iloc[-3]

        reasons = []

        entry_cfg = self.params["entry"]

        # 확실한 돌파: 고점 대비 버퍼(+0.5%) 초과 필요
        if price <= recent_high * (1.0 + entry_cfg["breakout_buffer"]):
            return Signal(SignalType.HOLD, ticker, "진입대기 - 돌파 조건 미달", 0, 0.1)

        strength = 0.4

        # volume trigger (2.2배 이상 강력한 수급 확인)
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

        # 🔥 Bullish Confirmation (15m 종가 양봉 혹은 긴 밑꼬리)
        if not self.is_bullish_candle(entry_market_data):
            return Signal(
                SignalType.HOLD, ticker, "진입대기 - 양봉/밑꼬리 컨펌 부족", 0, strength
            )

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
            f"진입대기",
            0,
            strength,
        )
