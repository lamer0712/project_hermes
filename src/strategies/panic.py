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
            },
            "exit": {
                "profit_target": 1.01,
                "stop_loss": 0.98,
            },
            "position_size_ratio": 0.1,
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

        entry_price = holdings.get(ticker, {}).get("avg_price", 0)

        # =========================
        # HOLD → 거의 무조건 탈출
        # =========================
        if is_held:
            if price <= entry_price * self.params["exit"]["stop_loss"]:
                return Signal(SignalType.SELL, ticker, "[손절] 패닉스탑", 1.0, 1.0)

            if price > entry_price * self.params["exit"]["profit_target"]:
                return Signal(SignalType.SELL, ticker, "[익절] 기술적 반등 성공", 1.0, 1.0)

            return Signal(SignalType.HOLD, ticker, "홀딩 (반등 중)", 0, 0.0)

        # =========================
        # ENTRY → 극단 상황만
        # =========================
        rebound = (
            rsi > self.params["entry"]["rsi_rebound"]
            and prev_rsi < self.params["entry"]["rsi_rebound"]
        )

        if rebound and rsi < 35:
            rsi_bonus = self.rsi_tiebreaker(rsi, mode="oversold")
            final_conf = min(0.9 + rsi_bonus, 1.0)

            return Signal(
                SignalType.BUY,
                ticker,
                "극단적 투매 반등 (RSI 침체)",
                self.params["position_size_ratio"],
                final_conf,
            )

        return Signal(SignalType.HOLD, ticker, "대기 (투매 관망)", 0, 0.0)
