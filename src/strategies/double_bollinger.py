from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class DoubleBollingerStrategy(BaseStrategy):
    """
    Double Bollinger Bands 전략 (DBB)
    
    - Buy Zone: 볼린저 밴드 상단 1.0 SD ~ 2.0 SD 사이 (모멘텀 구간)
    - Entry: 가격이 1.0 SD 상단을 상향 돌파하여 Buy Zone에 진입할 때
    - Exit: 리스크 매니저에 위임 (전략 내에서는 채널 이탈 시 신호 발생)
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("DoubleBollinger", default)

    def get_default_params(self) -> dict:
        return {
            "period": 20,
            "dev1": 1.0,
            "dev2": 2.0,
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
        
        if entry_market_data is None or "bb_upper1" not in entry_market_data.columns:
            return Signal(SignalType.HOLD, ticker, "데이터 부족 (DBB 미계산)", 0, 0.0)

        current_price = float(entry_market_data.close.iloc[-1])
        prev_price = float(entry_market_data.close.iloc[-2])
        
        bb_upper1 = float(entry_market_data.bb_upper1.iloc[-1])
        bb_upper2 = float(entry_market_data.bb_upper.iloc[-1]) # 2.0 SD
        
        prev_bb_upper1 = float(entry_market_data.bb_upper1.iloc[-2])
        
        rsi = float(entry_market_data.rsi_14.iloc[-1])

        # =========================
        # HOLDING → SELL
        # =========================
        if is_held:
            # 1.0 SD 밴드 하향 이탈 시 모멘텀 종료로 간주
            if current_price < bb_upper1:
                return Signal(
                    SignalType.SELL,
                    ticker,
                    "모멘텀 종료 (1.0 SD 밴드 하향 이탈)",
                    1.0,
                    1.0,
                )
            
            return Signal(SignalType.HOLD, ticker, "보유 유지 (모멘텀 구간 내 위치)", 0, 0.0)

        # =========================
        # ENTRY (15m)
        # =========================
        
        # 1. Neutral Zone(0~1 SD)에서 Buy Zone(1~2 SD)으로 진입 확인
        is_entering_buy_zone = prev_price <= prev_bb_upper1 and current_price > bb_upper1
        
        # 2. 과열 방지: 2.0 SD를 너무 크게 넘어서면 일단 보류 (오버슈팅 제외)
        is_within_zone = current_price < bb_upper2 * 1.005
        
        # 3. RSI 강도 확인 (보조)
        is_strong_momentum = rsi > 55

        if is_entering_buy_zone and is_within_zone and is_strong_momentum:
            # 위치에 따른 신뢰도 (1.5 SD 부근일 때 가장 높게 책정)
            pos_ratio = (current_price - bb_upper1) / (bb_upper2 - bb_upper1 + 1e-8)
            confidence = min(0.7 + (1.0 - abs(pos_ratio - 0.5)) * 0.2, 0.9)
            
            return Signal(
                SignalType.BUY,
                ticker,
                f"Double Bollinger 모멘텀 진입 (RSI: {rsi:.1f})",
                self.params["position_size_ratio"],
                confidence,
            )

        return Signal(SignalType.HOLD, ticker, "진입 대기 (모멘텀 진입 미발생)", 0, 0.1)
