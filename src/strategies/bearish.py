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
                "rsi_rebound": 45,
                "volume_multiplier": 1.2,
                "trend_filter": 0.98,
            },
            "exit": {
                "rsi_over": 55,
                "stop_loss": 0.97,
                "quick_tp": 1.02,
            },
            "position_size_ratio": 0.2,
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = None,
    ):
        holdings = portfolio_info.get("holdings", {})
        is_held = ticker in holdings and holdings[ticker]["volume"] > 0

        price = float(entry_market_data.close.iloc[-1])
        rsi = float(entry_market_data.rsi_14.iloc[-1])
        prev_rsi = float(entry_market_data.rsi_14.iloc[-2])
        ma9 = float(entry_market_data.ma_9.iloc[-1])
        ma20 = float(entry_market_data.ma_20.iloc[-1])

        vol = float(entry_market_data.volume.iloc[-1])
        vol_ma = float(entry_market_data.volume_ma20.iloc[-1])

        entry_price = holdings.get(ticker, {}).get("avg_price", 0)

        # =========================
        # HOLD → 빠른 탈출
        # =========================
        if is_held:
            if price <= entry_price * self.params["exit"]["stop_loss"]:
                return Signal(SignalType.SELL, ticker, "[손절] 칼손절선 도달", 1.0, 1.0)

            # 짧게 먹기
            if price >= entry_price * self.params["exit"]["quick_tp"]:
                return Signal(SignalType.SELL, ticker, "[익절] 단기반등 목표가", 0.7, 1.0)

            if rsi > self.params["exit"]["rsi_over"]:
                return Signal(SignalType.SELL, ticker, "[익절/손절] RSI 반등끝", 0.7, 1.0)

            if price < ma9:
                return Signal(SignalType.SELL, ticker, "[익절/손절] 단기안전선(MA9) 이탈", 0.8, 1.0)

            return Signal(SignalType.HOLD, ticker, "홀딩 (반등 중)", 0, 0.0)

        # =========================
        # ENTRY → 제한적 반등만
        # =========================
        rebound = (
            rsi > self.params["entry"]["rsi_rebound"]
            and prev_rsi <= self.params["entry"]["rsi_rebound"]
        )

        volume_ok = vol > vol_ma * self.params["entry"]["volume_multiplier"]

        # 핵심: 완전 하락추세는 피함
        trend_filter = price > ma20 * self.params["entry"]["trend_filter"]

        if rebound and volume_ok and trend_filter:
            # 동점자 방지를 위한 RSI 과매도 미세가중 (0.00 ~ 0.09) - 낮을수록 보너스
            rsi_bonus = min(max(100 - rsi, 1), 99) / 1000.0
            final_conf = min(0.8 + rsi_bonus, 1.0)

            return Signal(
                SignalType.BUY,
                ticker,
                "단기 하락 과대 (기술적 반등)",
                self.params["position_size_ratio"],
                final_conf,
            )

        return Signal(SignalType.HOLD, ticker, "대기 (하락장 관망)", 0, 0.0)
