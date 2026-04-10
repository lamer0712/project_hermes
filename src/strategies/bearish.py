from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class BearishStrategy(BaseStrategy):
    def __init__(self, params=None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("Bearish", default)

    def get_default_params(self):
        return {
            "regime": "bearish",
            "entry": {
                "rsi_rebound": 40,        # 45 -> 40 (조금 더 일찍 포착)
                "volume_multiplier": 1.3,
                "trend_filter": 0.92,     # 0.98 -> 0.92 (역배열 심화 시에도 반등 노림)
            },
            "position_size_ratio": 0.3,   # 0.2 -> 0.3 상향
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
        # ENTRY → 기술적 반등 포착
        # =========================
        
        # 1. RSI 반등
        rebound = (
            rsi > self.params["entry"]["rsi_rebound"]
            and prev_rsi <= self.params["entry"]["rsi_rebound"]
        )

        # 2. 거래량 동반 확인
        volume_ok = vol > vol_ma * self.params["entry"]["volume_multiplier"]

        # 3. 추세 필터 (너무 깊은 하방은 조심하되 기회는 열어둠)
        trend_filter = price > ma20 * self.params["entry"]["trend_filter"]

        if rebound and volume_ok and trend_filter:
            rsi_bonus = self.rsi_tiebreaker(rsi, mode="oversold")
            final_conf = min(0.8 + rsi_bonus, 1.0)

            return Signal(
                SignalType.BUY,
                ticker,
                f"Bearish 반등 포착 (RSI:{rsi:.1f}, Vol:{vol/vol_ma:.1f}x)",
                self.params["position_size_ratio"],
                final_conf,
            )

        return Signal(SignalType.HOLD, ticker, "대기 (하락장 관망)", 0, 0.0)
