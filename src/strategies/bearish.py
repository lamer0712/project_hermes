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
            },
            "exit": {
                "rsi_over": 55,
                "stop_loss": 0.97,
            },
            "position_size_ratio": 0.2,
        }

    def evaluate(self, ticker, setup_df, entry_df, regime, portfolio_info={}):
        holdings = portfolio_info.get("holdings", {})
        is_held = ticker in holdings and holdings[ticker]["volume"] > 0

        price = float(entry_df.close.iloc[-1])
        rsi = float(entry_df.rsi_14.iloc[-1])
        prev_rsi = float(entry_df.rsi_14.iloc[-2])
        ma9 = float(entry_df.ma_9.iloc[-1])

        entry_price = holdings.get(ticker, {}).get("avg_price", 0)

        # =========================
        # HOLD → 빠른 탈출
        # =========================
        if is_held:
            if price <= entry_price * self.params["exit"]["stop_loss"]:
                return Signal(SignalType.SELL, ticker, "Bearish stop loss", 1.0)

            if rsi > self.params["exit"]["rsi_over"]:
                return Signal(SignalType.SELL, ticker, "Dead cat bounce exit", 0.8)

            if price < ma9:
                return Signal(SignalType.SELL, ticker, "Trend rejection", 0.7)

            return Signal(SignalType.HOLD, ticker, "Weak hold", 0)

        # =========================
        # ENTRY → 제한적 반등만
        # =========================
        rebound = rsi > self.params["entry"]["rsi_rebound"] and prev_rsi <= self.params["entry"]["rsi_rebound"]

        if rebound:
            return Signal(
                SignalType.BUY,
                ticker,
                "Small rebound scalp",
                self.params["position_size_ratio"],
            )

        return Signal(SignalType.HOLD, ticker, "No trade (bearish)", 0)