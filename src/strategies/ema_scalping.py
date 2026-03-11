from src.strategies.base import BaseStrategy, Signal, SignalType


class EMAScalpingStrategy(BaseStrategy):
    """
    EMA 스캘핑 전략

    핵심 로직:
    - 단기 EMA(5)가 중기 EMA(13)를 상향 돌파(골든크로스)하면 매수
    - 단기 EMA(5)가 중기 EMA(13)를 하향 돌파(데드크로스)하면 매도
    - RSI 필터로 과열 구간 진입 방지

    특징:
    - 매우 빠른 진입/이탈로 단기 수익 극대화
    - 높은 회전율 → 수수료 부담 있으나 빈번한 수익 기회
    - 변동성이 큰 암호화폐 시장에 특히 적합
    - 15분봉 기준 EMA로 노이즈 필터링
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("EMA 스캘핑", default)

    def get_default_params(self) -> dict:
        return {
            "rsi_buy_filter": 65,         # RSI가 이 값 미만일 때만 매수
            "rsi_sell_threshold": 70,      # RSI가 이 값 초과 시 강제 매도
            "ema_cross_margin": 0.003,     # EMA 크로스 확인 마진 0.1% -> 0.3% 로 상향하여 휩소 방지
            "position_size_ratio": 0.4,    # 가용 현금의 40%씩 진입
        }

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = float(market_data.get("current_price", 0))
        rsi = float(market_data.get("rsi_14", 50))
        ema_5 = float(market_data.get("ema_5", 0))
        ema_13 = float(market_data.get("ema_13", 0))
        trend = market_data.get("trend", "ranging")

        rsi_buy_filter = self.params.get("rsi_buy_filter", 65)
        rsi_sell_th = self.params.get("rsi_sell_threshold", 70)
        cross_margin = self.params.get("ema_cross_margin", 0.001)
        position_ratio = self.params.get("position_size_ratio", 0.4)

        # EMA 데이터 없으면 보류
        if ema_5 == 0 or ema_13 == 0:
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason="EMA 데이터 없음 → 판단 보류",
                strength=0.0
            )

        # EMA 크로스 비율 계산
        ema_ratio = (ema_5 - ema_13) / ema_13 if ema_13 > 0 else 0
        # 현재가의 EMA 대비 위치
        price_vs_ema5 = (current_price - ema_5) / ema_5 if ema_5 > 0 else 0

        # 매수 시그널: EMA 5 > EMA 13 (골든크로스) + RSI 필터
        if ema_ratio > cross_margin:
            if rsi >= rsi_buy_filter:
                return Signal(
                    type=SignalType.HOLD,
                    ticker=ticker,
                    reason=f"EMA 골든크로스이나 RSI({rsi:.1f}) ≥ {rsi_buy_filter} → 과열, 대기",
                    strength=0.0
                )

            # 크로스 강도 + 현재가가 EMA 위에 있는 강도
            cross_strength = min(ema_ratio / 0.01, 1.0)  # 1% 크로스면 최대
            strength = min(position_ratio * (0.5 + cross_strength * 0.5), 1.0)
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"⚡ EMA5({ema_5:,.0f}) > EMA13({ema_13:,.0f}) 골든크로스 ({ema_ratio:+.2%}), RSI: {rsi:.1f} → 스캘핑 매수",
                strength=strength
            )

        # 매도 시그널 1: RSI 과매수
        if rsi > rsi_sell_th:
            strength = min((rsi - rsi_sell_th) / (100 - rsi_sell_th) + 0.5, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"🔥 RSI({rsi:.1f}) > {rsi_sell_th} → 과매수 빠른 익절",
                strength=strength
            )

        # 매도 시그널 2: EMA 데드크로스
        if ema_ratio < -cross_margin:
            cross_strength = min(abs(ema_ratio) / 0.01, 1.0)
            strength = min(0.5 + cross_strength * 0.3, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"📉 EMA5({ema_5:,.0f}) < EMA13({ema_13:,.0f}) 데드크로스 ({ema_ratio:+.2%}) → 스캘핑 매도",
                strength=strength
            )

        # HOLD — 크로스 마진 내 (관망)
        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason=f"EMA5/13 차이({ema_ratio:+.3%}) 마진 내, RSI({rsi:.1f}) → 관망",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        return f"""# ⚡ EMA 스캘핑 전략

## 전략 개요
단기 EMA(5)와 중기 EMA(13)의 크로스오버를 감지하여 빠르게 진입/이탈하는 스캘핑 전략입니다.
높은 회전율로 빈번한 수익 기회를 포착하며, RSI 필터로 과열 구간 진입을 방지합니다.
15분봉 기준 EMA를 사용하여 단기 노이즈를 필터링합니다.

## 매매 규칙
- **매수 조건**: EMA(5) > EMA(13) × (1 + {p.get('ema_cross_margin', 0.003):.1%}) AND RSI < {p.get('rsi_buy_filter', 65)}
- **매도 조건**: EMA(5) < EMA(13) × (1 - {p.get('ema_cross_margin', 0.003):.1%}) OR RSI > {p.get('rsi_sell_threshold', 70)}
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.4):.0%}

## 현재 파라미터
```json
{{"rsi_buy_filter": {p.get('rsi_buy_filter', 65)}, "rsi_sell_threshold": {p.get('rsi_sell_threshold', 70)}, "ema_cross_margin": {p.get('ema_cross_margin', 0.003)}, "position_size_ratio": {p.get('position_size_ratio', 0.4)}}}
```

## 장점
- 빠른 진입/이탈로 단기 수익 극대화
- 높은 회전율로 복리 효과
- 변동성 높은 암호화폐 시장에 최적화

## 단점
- 빈번한 매매로 수수료 부담 증가
- 횡보장에서 가짜 크로스 발생 가능
- 큰 추세를 놓칠 수 있음 (빠른 익절)
"""
