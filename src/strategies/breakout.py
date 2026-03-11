from src.strategies.base import BaseStrategy, Signal, SignalType


class BreakoutStrategy(BaseStrategy):
    """
    브레이크아웃(돌파) 전략 (확장용 - 향후 agent_gamma 등)
    
    핵심 로직:
    - 가격이 N일 최고가를 돌파하면 매수 (상승 돌파)
    - 가격이 N일 최저가를 하회하면 매도 (하락 돌파)
    - 터틀 트레이딩 전략에서 영감받은 채널 돌파 매매
    
    특징:
    - 강한 추세 시작 시점에 진입 가능
    - 횡보장에서는 가짜 돌파(whipsaw)로 손실 위험
    - RSI 모멘텀/볼린저와 완전히 다른 접근
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("채널 브레이크아웃", default)

    def get_default_params(self) -> dict:
        return {
            "breakout_period": 20,       # N일 최고/최저가 기준
            "breakout_margin": 0.005,    # 돌파 확인 마진 (0.5%)
            "volume_confirm": True,      # 거래량 확인 (현재 미구현, 확장용)
            "position_size_ratio": 0.35, # 가용 현금의 35%씩 진입
        }

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = float(market_data.get("current_price", 0))
        high_n = float(market_data.get("high_20", 0))
        low_n = float(market_data.get("low_20", 0))
        trend = market_data.get("trend", "ranging")

        if high_n == 0 or low_n == 0:
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason="N일 최고/최저가 데이터 없음 → 판단 보류",
                strength=0.0
            )

        margin = self.params.get("breakout_margin", 0.005)
        position_ratio = self.params.get("position_size_ratio", 0.35)

        # 상승 돌파 매수: 현재가가 N일 최고가를 마진만큼 초과
        upper_breakout = high_n * (1 + margin)
        if current_price >= upper_breakout:
            # 돌파 강도에 비례한 시그널 강도
            breakout_pct = (current_price - high_n) / high_n
            strength = min(position_ratio * (1 + breakout_pct * 10), 1.0)
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"가격({current_price:,.0f})이 {self.params.get('breakout_period', 20)}일 최고가({high_n:,.0f}) 돌파 (+{breakout_pct:.2%}) → 브레이크아웃 매수",
                strength=strength
            )

        # 하락 돌파 매도: 현재가가 N일 최저가를 마진만큼 하회
        lower_breakout = low_n * (1 - margin)
        if current_price <= lower_breakout:
            breakdown_pct = (low_n - current_price) / low_n
            strength = min(breakdown_pct * 10 + 0.5, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"가격({current_price:,.0f})이 {self.params.get('breakout_period', 20)}일 최저가({low_n:,.0f}) 하회 (-{breakdown_pct:.2%}) → 브레이크다운 매도",
                strength=strength
            )

        # 채널 내 위치 계산
        channel_range = high_n - low_n
        if channel_range > 0:
            channel_position = (current_price - low_n) / channel_range
        else:
            channel_position = 0.5

        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason=f"채널 내 위치: {channel_position:.0%} (범위: {low_n:,.0f} ~ {high_n:,.0f}) → 돌파 대기",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        return f"""# 🚀 채널 브레이크아웃 전략

## 전략 개요
N일 가격 채널(최고가/최저가)을 활용한 돌파 매매 전략입니다.
터틀 트레이딩에서 영감받은 채널 브레이크아웃을 기반으로 합니다.

## 매매 규칙
- **매수 조건**: 현재가 > {p.get('breakout_period', 20)}일 최고가 × (1 + {p.get('breakout_margin', 0.005):.1%})
- **매도 조건**: 현재가 < {p.get('breakout_period', 20)}일 최저가 × (1 - {p.get('breakout_margin', 0.005):.1%})
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.35):.0%}

## 현재 파라미터
```json
{{"breakout_period": {p.get('breakout_period', 20)}, "breakout_margin": {p.get('breakout_margin', 0.005)}, "volume_confirm": {str(p.get('volume_confirm', True)).lower()}, "position_size_ratio": {p.get('position_size_ratio', 0.35)}}}
```

## 장점
- 강한 추세의 시작을 포착 가능 (초기 진입)
- 단순하고 객관적인 기준

## 단점
- 횡보장에서 가짜 돌파(whipsaw) 손실 위험
- 이미 충분히 오른 후 진입할 수 있음
"""
