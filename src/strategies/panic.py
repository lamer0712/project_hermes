from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class PanicStrategy(BaseStrategy):
    def __init__(self, params=None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("Panic", default)

    def get_default_params(self):
        return {
            "regime": "panic",
            "entry": {
                "rsi_rebound": 30,
                "disparity_threshold": 0.95, # MA20 대비 5% 이상 하락했을 때만
                "volume_multiplier": 1.5,     # 평균거래량 대비 1.5배 이상
            },
            "position_size_ratio": 0.2, # 10% -> 20% 상향 (하락장 기회 포착 강화)
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = None,
    ):
        holdings, is_held = self.parse_holdings(ticker, portfolio_info)

        price = float(entry_market_data.close.iloc[-1])
        rsi = float(entry_market_data.rsi_14.iloc[-1])
        prev_rsi = float(entry_market_data.rsi_14.iloc[-2])
        ma20 = float(entry_market_data.ma_20.iloc[-1])
        vol = float(entry_market_data.volume.iloc[-1])
        vol_ma = float(entry_market_data.volume_ma20.iloc[-1])

        # =========================
        # HOLD → 리스크 매니저에 위임
        # =========================
        if is_held:
            return Signal(SignalType.HOLD, ticker, "리스크 매니저 추적 중", 0, 0.0)

        # =========================
        # ENTRY → 기술적 분석 강화
        # =========================
        
        # 1. RSI 침체 구간 탈출 (반등 시작)
        rebound = (
            rsi > self.params["entry"]["rsi_rebound"]
            and prev_rsi <= self.params["entry"]["rsi_rebound"]
        )

        # 2. 괴리율 필터 (낙폭 과대 확인)
        disparity = price / ma20
        is_oversold = disparity < self.params["entry"]["disparity_threshold"]

        # 3. 거래량 필터 (신뢰도 확인)
        is_volume_spike = vol > vol_ma * self.params["entry"]["volume_multiplier"]

        if rebound and is_oversold and is_volume_spike:
            # RSI가 낮을수록 더 높은 신뢰도 부여
            rsi_bonus = self.rsi_tiebreaker(rsi, mode="oversold")
            final_conf = min(0.85 + rsi_bonus, 1.0)

            return Signal(
                SignalType.BUY,
                ticker,
                f"패닉 반등 포착 (RSI:{rsi:.1f}, 괴리율:{disparity:.2f}, 거래량:{vol/vol_ma:.1f}x)",
                self.params["position_size_ratio"],
                final_conf,
            )

        return Signal(SignalType.HOLD, ticker, "대기 (투매 관망)", 0, 0.0)
