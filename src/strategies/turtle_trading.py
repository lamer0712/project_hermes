from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class TurtleTradingStrategy(BaseStrategy):
    """
    Turtle Trading 전략 (Crypto Adaptation)
    
    - Entry: 20기간 최고점 돌파 (Donchian Channel Upper)
    - Exit: 10기간 최저점 이탈 (Donchian Channel Lower)
    - Filter: 장기 추세(EMA 200) 확인
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("TurtleTrading", default)

    def get_default_params(self) -> dict:
        return {
            "entry_period": 20,
            "exit_period": 10,
            "ema_filter_period": 200,
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
        
        if entry_market_data is None or len(entry_market_data) < 20:
            return Signal(SignalType.HOLD, ticker, "데이터 부족", 0, 0.0)

        current_price = float(entry_market_data.close.iloc[-1])
        prev_price = float(entry_market_data.close.iloc[-2])
        
        high_20 = float(entry_market_data.high_20.iloc[-2]) # 전봉 기준 최고가
        low_10 = float(entry_market_data.low_10.iloc[-2])   # 전봉 기준 최저가
        
        ema_200 = float(entry_market_data.ema_200.iloc[-1])

        # =========================
        # HOLDING → SELL
        # =========================
        if is_held:
            # 10기간 최저점 이탈 시 청산 (Turtle Exit Rule)
            if current_price < low_10:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    f"터틀 청산 (10일 저점 {low_10:,.0f} 이탈)",
                    1.0,
                    1.0,
                )
            
            return Signal(SignalType.HOLD, ticker, "보유 유지 (채널 내 위치)", 0, 0.0)

        # =========================
        # ENTRY (15m)
        # =========================
        
        # 1. 20기간 최고점 돌파 확인
        is_breakout = current_price > high_20
        
        # 2. 장기 추세 필터 (EMA 200 위에서만 매수)
        is_bullish = current_price > ema_200
        
        if is_breakout and is_bullish:
            # 돌파 강도 계산 (최고점 대비 얼마나 올랐는지)
            breakout_strength = (current_price - high_20) / high_20
            confidence = min(0.6 + breakout_strength * 10, 0.95)
            
            return Signal(
                SignalType.BUY,
                ticker,
                f"터틀 돌파 (20일 고점 {high_20:,.0f} 돌파)",
                self.params["position_size_ratio"],
                confidence,
            )

        return Signal(SignalType.HOLD, ticker, "진입 대기 (돌파 미발생)", 0, 0.0)
