from src.strategies.base import BaseStrategy, Signal, SignalType


class MultiIndicatorConvergenceStrategy(BaseStrategy):
    """
    복합 지표 수렴 전략

    핵심 로직:
    - RSI + 볼린저 밴드 위치 + MA 추세 3가지 지표가 동시에 같은 방향을 가리킬 때만 진입
    - 매수: RSI 과매도 + BB 하단 근처 + 상승 추세(MA20 > MA50) → 3중 확인
    - 매도: RSI 과매수 + BB 상단 근처 → 2중 확인

    특징:
    - 복합 필터로 거짓 시그널 최소화 → 높은 승률
    - 진입 빈도가 낮지만 질이 높은 매매
    - 전 시장 상황에서 안정적 운용 가능
    - Stochastic RSI를 보조 확인 지표로 활용
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("복합 지표 수렴", default)

    def get_default_params(self) -> dict:
        return {
            "rsi_buy": 40,                 # RSI 매수 기준
            "rsi_sell": 65,                # RSI 매도 기준
            "bb_position_buy": 0.3,        # BB 밴드 내 위치 (0=하단, 1=상단) 이하이면 매수
            "bb_position_sell": 0.7,       # BB 밴드 내 위치 이상이면 매도
            "require_trend": True,         # MA 추세 확인 필수 여부
            "stoch_rsi_confirm": True,     # Stochastic RSI 보조 확인
            "position_size_ratio": 0.3,    # 가용 현금의 30%씩 진입 (보수적)
        }

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = float(market_data.get("current_price", 0))
        rsi = float(market_data.get("rsi_14", 50))
        bb_upper = float(market_data.get("bb_upper", 0))
        bb_lower = float(market_data.get("bb_lower", 0))
        bb_mid = float(market_data.get("bb_mid", 0))
        trend = market_data.get("trend", "ranging")
        stoch_rsi = float(market_data.get("stoch_rsi", 50))

        rsi_buy = self.params.get("rsi_buy", 40)
        rsi_sell = self.params.get("rsi_sell", 65)
        bb_buy_pos = self.params.get("bb_position_buy", 0.3)
        bb_sell_pos = self.params.get("bb_position_sell", 0.7)
        require_trend = self.params.get("require_trend", True)
        use_stoch = self.params.get("stoch_rsi_confirm", True)
        position_ratio = self.params.get("position_size_ratio", 0.3)

        # BB 데이터 필수
        if bb_upper == 0 or bb_lower == 0:
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason="볼린저 밴드 데이터 없음 → 판단 보류",
                strength=0.0
            )

        # BB 밴드 내 위치 계산 (0 = 하단, 1 = 상단)
        bb_range = bb_upper - bb_lower
        bb_position = (current_price - bb_lower) / bb_range if bb_range > 0 else 0.5

        # === 매수 조건 점수 시스템 ===
        buy_score = 0
        buy_reasons = []

        # 조건 1: RSI 과매도
        if rsi < rsi_buy:
            buy_score += 1
            buy_reasons.append(f"RSI({rsi:.1f})<{rsi_buy}")

        # 조건 2: BB 하단 근처
        if bb_position < bb_buy_pos:
            buy_score += 1
            buy_reasons.append(f"BB위치({bb_position:.0%})<{bb_buy_pos:.0%}")

        # 조건 3: 상승 추세
        if trend == "bullish":
            buy_score += 1
            buy_reasons.append("상승추세")
        elif require_trend and trend != "bullish":
            buy_score = 0  # 추세 확인 필수인데 상승이 아니면 매수 불가

        # 조건 4 (보조): Stochastic RSI 과매도
        if use_stoch and stoch_rsi < 20:
            buy_score += 0.5
            buy_reasons.append(f"StochRSI({stoch_rsi:.0f})")

        # 3개 이상 조건 충족 시 매수
        if buy_score >= 3:
            # 점수가 높을수록 강한 시그널
            strength = min(position_ratio * (buy_score / 3), 1.0)
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"🎯 복합 시그널 수렴 ({buy_score:.1f}점): {', '.join(buy_reasons)} → 매수",
                strength=strength
            )

        # === 매도 조건 점수 시스템 ===
        sell_score = 0
        sell_reasons = []

        # 조건 1: RSI 과매수
        if rsi > rsi_sell:
            sell_score += 1
            sell_reasons.append(f"RSI({rsi:.1f})>{rsi_sell}")

        # 조건 2: BB 상단 근처
        if bb_position > bb_sell_pos:
            sell_score += 1
            sell_reasons.append(f"BB위치({bb_position:.0%})>{bb_sell_pos:.0%}")

        # 조건 3 (보조): Stochastic RSI 과매수
        if use_stoch and stoch_rsi > 80:
            sell_score += 0.5
            sell_reasons.append(f"StochRSI({stoch_rsi:.0f})")

        # 조건 4: 하락 추세
        if trend == "bearish":
            sell_score += 0.5
            sell_reasons.append("하락추세")

        # 2개 이상 조건 충족 시 매도
        if sell_score >= 2:
            strength = min((sell_score / 2) * 0.5 + 0.3, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"🚨 복합 매도 시그널 ({sell_score:.1f}점): {', '.join(sell_reasons)} → 매도",
                strength=strength
            )

        # HOLD — 시그널 수렴 미달
        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason=f"복합 지표 미수렴 (매수{buy_score:.1f}점/매도{sell_score:.1f}점), BB위치: {bb_position:.0%}, RSI: {rsi:.1f} → 관망",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        return f"""# 🎯 복합 지표 수렴 전략

## 전략 개요
RSI, 볼린저 밴드, 이동평균 추세, Stochastic RSI 4가지 지표가 동시에 같은 방향을 
가리킬 때만 매매하는 고품질 시그널 전략입니다.
거짓 시그널을 최소화하여 안정적인 수익을 추구합니다.

## 매매 규칙
- **매수 조건** (3개 이상 충족 시):
  1. RSI < {p.get('rsi_buy', 40)} (과매도)
  2. BB 밴드 위치 < {p.get('bb_position_buy', 0.3):.0%} (하단 근처)
  3. MA20 > MA50 (상승 추세)
  4. Stochastic RSI < 20 (보조, +0.5점)
- **매도 조건** (2개 이상 충족 시):
  1. RSI > {p.get('rsi_sell', 65)} (과매수)
  2. BB 밴드 위치 > {p.get('bb_position_sell', 0.7):.0%} (상단 근처)
  3. Stochastic RSI > 80 (보조, +0.5점)
  4. 하락 추세 (보조, +0.5점)
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.3):.0%}

## 현재 파라미터
```json
{{"rsi_buy": {p.get('rsi_buy', 40)}, "rsi_sell": {p.get('rsi_sell', 65)}, "bb_position_buy": {p.get('bb_position_buy', 0.3)}, "bb_position_sell": {p.get('bb_position_sell', 0.7)}, "require_trend": {str(p.get('require_trend', True)).lower()}, "stoch_rsi_confirm": {str(p.get('stoch_rsi_confirm', True)).lower()}, "position_size_ratio": {p.get('position_size_ratio', 0.3)}}}
```

## 장점
- 복합 필터로 거짓 시그널 최소화 → 높은 승률
- 전 시장 구간(상승/횡보/하락)에서 안정적 운용
- 리스크 관리 우수 (보수적 진입)

## 단점
- 진입 빈도가 낮아 기회비용 발생
- 급등주 초기 진입을 놓칠 수 있음
- 파라미터가 많아 최적화 복잡
"""
