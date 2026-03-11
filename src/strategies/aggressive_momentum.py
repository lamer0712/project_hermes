from src.strategies.base import BaseStrategy, Signal, SignalType


class AggressiveMomentumStrategy(BaseStrategy):
    """
    공격적 모멘텀 전략 (agent_gamma용)
    
    핵심 로직:
    - RSI 과매도 구간을 넓게 잡아 (< 45) 더 자주 매수 기회 포착
    - RSI 과매수 매도 기준을 낮춰 (> 65) 빠른 이익 실현
    - 추세 확인 없이 RSI만으로 공격적 진입 → 횡보장에서도 적극 매매
    - 큰 포지션 크기(60%)로 확신 시 집중 투자
    
    특징:
    - 높은 회전율 (매수/매도 빈번) → 수수료 부담 있으나 수익 기회 극대화
    - 추세 무관 진입 → 강한 하락장에서 손실 위험이 있으나 반등 기회도 포착
    - Alpha 대비 2배 넓은 매수 구간, 더 빠른 익절
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("공격적 모멘텀", default)

    def get_default_params(self) -> dict:
        return {
            "buy_rsi_threshold": 45,        # 넓은 매수 구간 (Alpha는 35)
            "sell_rsi_threshold": 65,        # 빠른 익절 (Alpha는 70)
            "require_bullish_trend": False,  # 추세 무관 진입
            "position_size_ratio": 0.6,      # 가용 현금의 60% 대규모 진입
        }

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = float(market_data.get("current_price", 0))
        rsi = float(market_data.get("rsi_14", 50))
        trend = market_data.get("trend", "ranging")

        buy_rsi = self.params.get("buy_rsi_threshold", 45)
        sell_rsi = self.params.get("sell_rsi_threshold", 65)
        require_bullish = self.params.get("require_bullish_trend", False)
        position_ratio = self.params.get("position_size_ratio", 0.6)

        # 매수 시그널: RSI 과매도 (넓은 구간) + (옵션) 추세 확인
        if rsi < buy_rsi:
            if require_bullish and trend != "bullish":
                return Signal(
                    type=SignalType.HOLD,
                    ticker=ticker,
                    reason=f"RSI({rsi:.1f}) < {buy_rsi} 이지만 추세가 {trend}로 상승장 아님 → 대기",
                    strength=0.0
                )
            
            # RSI가 낮을수록 더 강한 매수 시그널 (공격적 계수)
            strength = min(position_ratio * (1 + (buy_rsi - rsi) / buy_rsi * 1.5), 1.0)
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"🔥 RSI({rsi:.1f}) < {buy_rsi}, 추세: {trend} → 공격적 모멘텀 매수",
                strength=strength
            )

        # 매도 시그널: RSI 과매수 (낮은 기준 → 빠른 익절)
        if rsi > sell_rsi:
            strength = min((rsi - sell_rsi) / (100 - sell_rsi) + 0.5, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"⚡ RSI({rsi:.1f}) > {sell_rsi} → 빠른 익절 매도",
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
        return f"""# 🔥 공격적 모멘텀 전략

## 전략 개요
RSI 기반의 공격적 단기 트레이딩 전략입니다.
넓은 매수 구간과 빠른 익절로 높은 회전율을 추구하며, 추세 확인 없이 과감하게 진입합니다.

## 매매 규칙
- **매수 조건**: RSI < {p.get('buy_rsi_threshold', 45)} (추세 무관 진입)
- **매도 조건**: RSI > {p.get('sell_rsi_threshold', 65)}
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.6):.0%}

## 현재 파라미터
```json
{{"buy_rsi_threshold": {p.get('buy_rsi_threshold', 45)}, "sell_rsi_threshold": {p.get('sell_rsi_threshold', 65)}, "require_bullish_trend": {str(p.get('require_bullish_trend', False)).lower()}, "position_size_ratio": {p.get('position_size_ratio', 0.6)}}}
```

## 장점
- 넓은 매수 구간으로 더 많은 기회 포착
- 빠른 익절로 수익 확보 + 높은 회전율
- 추세 무관 진입으로 횡보장에서도 적극 매매

## 단점
- 빈번한 매매로 수수료 부담 증가
- 강한 하락장에서 조기 진입 위험
- 추세 확인 없이 진입하므로 역추세 손실 가능
"""
