from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd


class RSIMomentumStrategy(BaseStrategy):
    """
    RSI 모멘텀 전략 (agent_alpha 기본 전략)
    
    핵심 로직:
    - RSI가 과매도 구간(threshold 이하)이고 상승 추세일 때 매수
    - RSI가 과매수 구간(threshold 이상)일 때 매도
    - 추세 추종형: 상승장에서만 진입하여 모멘텀을 타고 수익 실현
    
    특징:
    - 트렌드 확인 후 진입 → 승률이 높지만 진입이 늦을 수 있음
    - 강한 상승장에서 위력 발휘
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("RSI 모멘텀", default)

    def get_default_params(self) -> dict:
        return {
            "buy_rsi_threshold": 30,
            "sell_rsi_threshold": 70,
            "require_bullish_trend": True,
            "position_size_ratio": 0.3,   # 가용 현금의 30%씩 진입
        }

    def evaluate(self, ticker: str, setup_market_data: pd.DataFrame, entry_market_data: pd.DataFrame, portfolio_info: dict = None) -> Signal:
        current_price = float(entry_market_data.close.iloc[-1])
        rsi = float(entry_market_data.rsi_14.iloc[-1])
        ma_20 = float(entry_market_data.ma_20.iloc[-1])
        ma_50 = float(entry_market_data.ma_50.iloc[-1])
        trend = "ranging"
        if ma_20 > ma_50 * 1.01:
            trend = "bullish"
        elif ma_20 < ma_50 * 0.99:
            trend = "bearish"

        buy_rsi = self.params.get("buy_rsi_threshold", 35)
        sell_rsi = self.params.get("sell_rsi_threshold", 70)
        require_bullish = self.params.get("require_bullish_trend", True)
        position_ratio = self.params.get("position_size_ratio", 0.3)

        # 매수 시그널: RSI 과매도 + (옵션) 상승 추세
        if rsi < buy_rsi:
            if require_bullish and trend != "bullish":
                return Signal(
                    type=SignalType.HOLD,
                    ticker=ticker,
                    reason=f"RSI({rsi:.1f}) < {buy_rsi} 이지만 추세가 {trend}로 상승장 아님 → 대기",
                    strength=0.0
                )
            
            # RSI가 낮을수록 더 강한 매수 시그널
            strength = min(position_ratio * (1 + (buy_rsi - rsi) / buy_rsi), 1.0)
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"RSI({rsi:.1f}) < {buy_rsi}, 추세: {trend} → 모멘텀 매수",
                strength=strength
            )

        # 매도 시그널: RSI 과매수
        if rsi > sell_rsi:
            # RSI가 높을수록 더 강한 매도 시그널
            strength = min((rsi - sell_rsi) / (100 - sell_rsi) + 0.5, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"RSI({rsi:.1f}) > {sell_rsi} → 과매수 매도",
                strength=strength
            )

        # HOLD
        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason=f"RSI({rsi:.1f}) 중립 구간 ({buy_rsi}~{sell_rsi}) → 관망",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        return f"""# 📈 RSI 모멘텀 전략

## 전략 개요
RSI(상대강도지수)를 활용한 추세 추종형 전략입니다.
과매도 구간에서 상승 추세를 확인한 뒤 매수하고, 과매수 시 차익을 실현합니다.

## 매매 규칙
- **매수 조건**: RSI < {p.get('buy_rsi_threshold', 35)} AND 상승 추세(MA20 > MA50)
- **매도 조건**: RSI > {p.get('sell_rsi_threshold', 70)}
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.3):.0%}

## 현재 파라미터
```json
{{"buy_rsi_threshold": {p.get('buy_rsi_threshold', 35)}, "sell_rsi_threshold": {p.get('sell_rsi_threshold', 70)}, "require_bullish_trend": {str(p.get('require_bullish_trend', True)).lower()}, "position_size_ratio": {p.get('position_size_ratio', 0.3)}}}
```

## 장점
- 추세를 확인하고 진입하므로 역추세 리스크가 낮음
- RSI 과매수 매도로 이익 실현 타이밍 명확

## 단점
- 횡보장에서 시그널이 적음
- 급등주 초기 진입을 놓칠 수 있음
"""
