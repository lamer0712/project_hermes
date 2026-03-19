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

    def evaluate(self, ticker, setup_df, entry_df, regime, portfolio_info={}):
        holdings = portfolio_info.get("holdings", {})
        is_held = ticker in holdings and holdings[ticker]["volume"] > 0

        price = float(entry_df.close.iloc[-1])
        rsi = float(entry_df.rsi_14.iloc[-1])
        prev_rsi = float(entry_df.rsi_14.iloc[-2])

        entry_price = holdings.get(ticker, {}).get("avg_price", 0)

        # =========================
        # HOLD → 거의 무조건 탈출
        # =========================
        if is_held:
            if price <= entry_price * self.params["exit"]["stop_loss"]:
                return Signal(SignalType.SELL, ticker, "Panic stop", 1.0)

            if price > entry_price * self.params["exit"]["profit_target"]:
                return Signal(SignalType.SELL, ticker, "Quick bounce exit", 1.0)

            return Signal(SignalType.HOLD, ticker, "Very risky hold", 0)

        # =========================
        # ENTRY → 극단 상황만
        # =========================
        rebound = rsi > self.params["entry"]["rsi_rebound"] and prev_rsi < self.params["entry"]["rsi_rebound"]

        if rebound and rsi < 35:
            return Signal(
                SignalType.BUY,
                ticker,
                "Extreme oversold bounce",
                self.params["position_size_ratio"],
            )

        return Signal(SignalType.HOLD, ticker, "No trade (panic)", 0)