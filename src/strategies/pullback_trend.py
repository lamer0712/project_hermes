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
                "rsi_threshold": 55,  # 55 미만 (조정 중 확인)
                "bb_position_threshold": 0.5,  # BB 중앙 이하 (확실한 눌림)
                "adx_threshold": 28,  # 22 -> 28 (초강성 추세만 타겟)
            },
            "entry": {
                "timeframe": "15m",
                "rsi_threshold": 40,  # 45 -> 40 (더 깊은 조정 대기)
                "volume_multiplier": 2.2,  # 1.8 -> 2.2 (강력한 수급 확인)
            },
            "exit": {
                "rsi_threshold": 80,  # 85 -> 80 (롤백: 적정 수익 확보)
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
            rsi_sell = rsi_entry > self.params["exit"].get("rsi_threshold", 80)

            if rsi_sell:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"[익절] RSI 단기과열 ({rsi_entry:.1f})",
                    1.0,
                    1.0,
                )

            # 눌림목은 생명선(MA20) 이탈 시 빠른 손절이 생명
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
        ma20_setup = float(setup_market_data.ma_20.iloc[-1])
        bb_pos = float(setup_market_data.bb_position.iloc[-1])
        adx_setup = float(setup_market_data.adx_14.iloc[-1])

        setup_cfg = self.params["setup"]

        # 1. 초강력 추세 확인: ADX가 일정 수준 이상이어야 눌림목이 유효함
        if adx_setup < setup_cfg["adx_threshold"]:
            return Signal(
                SignalType.HOLD,
                ticker,
                f"진입대기 - 추세 강도 부족 (ADX:{adx_setup:.1f})",
                0,
                0.01,
            )

        # 2. 상위 타임프레임(1h) 정배열 필터링 추가
        if not self.is_bullish_trend_htf(setup_market_data):
            return Signal(
                SignalType.HOLD,
                ticker,
                "진입대기 - 상위 타임프레임(1h) 역배열 필터링",
                0,
                0.01,
            )

        # 3. RSI/BB 셋업 (충분한 조정이 되었는지 확인)
        setup_ok = (
            rsi_setup < setup_cfg["rsi_threshold"]
            and bb_pos < setup_cfg["bb_position_threshold"]
        )
        if not setup_ok:
            return Signal(SignalType.HOLD, ticker, "진입대기 - 1h 눌림폭 부족", 0, 0.01)

        # ------------------------------
        # ENTRY (Continuous Scaling Confidence)
        # ------------------------------
        reasons = []
        
        # 1. Trend Strength Score (ADX 28 -> 0, 45 -> 1.0)
        adx = float(entry_market_data.adx_14.iloc[-1])
        adx_score = min(max((adx - 28) / 17.0, 0.0), 1.0)

        # 2. Bounce Intensity Score (RSI 반등폭 1 -> 0, 8 -> 1.0)
        rsi_diff = rsi_entry - prev_rsi_entry
        rsi_bounce_score = min(max((rsi_diff - 1.0) / 7.0, 0.0), 1.0)

        # 3. Volume Score (Multiplier 2.2 -> 0, 4.4 -> 1.0)
        vol_mult = self.params["entry"]["volume_multiplier"] # 2.2
        vol_score = min(max((volume / (vol_ma * vol_mult) - 1.0), 0.0), 1.0)

        # 가중합 (ADX 40% : Bounce 30% : Vol 30%)
        base_score = 0.4 * adx_score + 0.3 * rsi_bounce_score + 0.3 * vol_score
        
        # 최소 조건 (MA9 돌파 혹은 RSI 크로스 중 하나는 필수)
        ma_cross = prev_price <= prev_ma9 and current_price > ma9
        rsi_cross = rsi_entry > self.params["entry"]["rsi_threshold"]
        
        if (ma_cross or rsi_cross) and volume > vol_ma * 1.5:
            # 최종 정규화 (0.3 ~ 1.0)
            rsi_bonus = self.rsi_tiebreaker(rsi_entry, mode="oversold")
            final_conf = min(0.3 + (base_score * 0.7) + rsi_bonus, 1.0)
            
            size_ratio = self.params["position_size_ratio"]
            return Signal(
                SignalType.BUY,
                ticker,
                " | ".join(reasons) if reasons else "추세눌림목진입",
                final_conf * size_ratio,
                final_conf,
            )

        return Signal(SignalType.HOLD, ticker, f"진입대기", 0, 0.1)
