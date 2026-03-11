from src.strategies.base import BaseStrategy, Signal, SignalType


class BollingerReversionStrategy(BaseStrategy):
    """
    볼린저 밴드 평균회귀 전략 (agent_beta 기본 전략)
    
    핵심 로직:
    - 가격이 볼린저 밴드 하단 근처로 하락하면 "평균으로 회귀할 것"으로 보고 매수
    - 가격이 볼린저 밴드 상단 근처로 상승하면 "평균으로 회귀할 것"으로 보고 매도
    - RSI를 보조 필터로 사용 (극단적 과매도/과매수와 결합)
    
    특징:
    - 횡보장(레인지장)에서 강력한 수익
    - 강한 추세장에서는 역추세 진입으로 손실 위험
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("볼린저 밴드 평균회귀", default)

    def get_default_params(self) -> dict:
        return {
            "bb_buy_threshold": 0.02,    # 하단 밴드 대비 2% 이내면 매수
            "bb_sell_threshold": 0.02,   # 상단 밴드 대비 2% 이내면 매도
            "rsi_oversold_filter": 40,   # RSI 보조 필터 (매수 확인)
            "rsi_overbought_filter": 60, # RSI 보조 필터 (매도 확인)
            "position_size_ratio": 0.25, # 가용 현금의 25%씩 진입
        }

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = float(market_data.get("current_price", 0))
        rsi = float(market_data.get("rsi_14", 50))
        bb_upper = float(market_data.get("bb_upper", 0))
        bb_lower = float(market_data.get("bb_lower", 0))
        bb_mid = float(market_data.get("bb_mid", 0))

        if bb_upper == 0 or bb_lower == 0 or bb_mid == 0:
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason="볼린저 밴드 데이터 없음 → 판단 보류",
                strength=0.0
            )

        buy_th = self.params.get("bb_buy_threshold", 0.02)
        sell_th = self.params.get("bb_sell_threshold", 0.02)
        rsi_oversold = self.params.get("rsi_oversold_filter", 40)
        rsi_overbought = self.params.get("rsi_overbought_filter", 60)
        position_ratio = self.params.get("position_size_ratio", 0.25)

        # 가격이 하단 밴드에 근접 또는 이탈
        lower_distance = (current_price - bb_lower) / bb_lower if bb_lower > 0 else 1.0
        upper_distance = (bb_upper - current_price) / bb_upper if bb_upper > 0 else 1.0

        # 매수 시그널: 하단 밴드 근처 + RSI 과매도 확인
        if lower_distance <= buy_th and rsi < rsi_oversold:
            # 밴드에 가까울수록 더 강한 시그널
            strength = min(position_ratio * (1 + (buy_th - lower_distance) / buy_th), 1.0)
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"가격({current_price:,.0f})이 BB 하단({bb_lower:,.0f}) 근접 (거리: {lower_distance:.2%}), RSI: {rsi:.1f} → 평균회귀 매수",
                strength=strength
            )

        # 매도 시그널: 상단 밴드 근처 + RSI 과매수 확인
        if upper_distance <= sell_th and rsi > rsi_overbought:
            strength = min((sell_th - upper_distance) / sell_th + 0.5, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"가격({current_price:,.0f})이 BB 상단({bb_upper:,.0f}) 근접 (거리: {upper_distance:.2%}), RSI: {rsi:.1f} → 평균회귀 매도",
                strength=strength
            )

        # HOLD
        band_position = (current_price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason=f"BB 밴드 내 위치: {band_position:.0%} (하단 기준), RSI: {rsi:.1f} → 관망",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        return f"""# 📊 볼린저 밴드 평균회귀 전략

## 전략 개요
볼린저 밴드를 활용한 평균회귀(Mean Reversion) 전략입니다.
가격이 밴드 상하단에 도달하면 중심선(평균)으로 회귀할 것으로 판단하여 매매합니다.

## 매매 규칙
- **매수 조건**: 가격이 BB 하단의 {p.get('bb_buy_threshold', 0.02):.0%} 이내 AND RSI < {p.get('rsi_oversold_filter', 40)}
- **매도 조건**: 가격이 BB 상단의 {p.get('bb_sell_threshold', 0.02):.0%} 이내 AND RSI > {p.get('rsi_overbought_filter', 60)}
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.25):.0%}

## 현재 파라미터
```json
{{"bb_buy_threshold": {p.get('bb_buy_threshold', 0.02)}, "bb_sell_threshold": {p.get('bb_sell_threshold', 0.02)}, "rsi_oversold_filter": {p.get('rsi_oversold_filter', 40)}, "rsi_overbought_filter": {p.get('rsi_overbought_filter', 60)}, "position_size_ratio": {p.get('position_size_ratio', 0.25)}}}
```

## 장점
- 횡보장에서 안정적인 수익 창출
- 명확한 진입/출구 기준 (밴드 상하단)

## 단점
- 강한 추세장에서 역추세로 손실 가능
- 변동성 급증 구간에서 밴드 확장으로 시그널 지연
"""
