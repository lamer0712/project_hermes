import json
from src.strategies.base import BaseStrategy, Signal, SignalType

class MeanReversionStrategy(BaseStrategy):
    """
    하락장 전용 수익 창출 전략 (Buy the Dip).
    단기 낙폭 과대 등 극단적 공포 구간에서 진입하여 짧은 반등에 수익을 내고 빠집니다.
    """
    
    def __init__(self, params: dict = None):
        super().__init__("Mean Reversion (Buy the Dip)", params)

    def get_strategy_description(self) -> str:
        buy_rsi = self.params.get("buy_rsi_threshold", 25)
        buy_bb = self.params.get("buy_bb_lower_break", True)
        sell_rsi = self.params.get("sell_rsi_threshold", 45)
        tp = self.params.get("take_profit_pct", 3.0)
        sl = self.params.get("stop_loss_pct", -4.0)
        
        return f"""
# {self.name} Strategy

## 개요
이 전략은 하락장이나 급락 시 일시적인 가격 괴리(오버슈팅)를 노리는 역추세 매매 기법입니다.
극단적인 공포 심리에 의해 투매가 나올 때 진입하여 기술적 반등(Dead Cat Bounce)에서 수익을 확보합니다.
상승장 추세 추종 전략이 킬 스위치나 다른 이유로 멈췄을 때 방어적이지만 매우 공격적인 단기 수익 창출을 목표로 합니다.

## 매수 조건
- 현재 RSI(14)가 {buy_rsi} 이하일 때 (과매도 극점)
- 볼린저 밴드 하단을 강하게 이탈했을 때 ({'필수 적용' if buy_bb else '미적용'})

## 매도 조건
- RSI가 {sell_rsi} 이상으로 오르며 반등에 성공했을 때
- 강제 단기 익절: +{tp}% (짧고 확실하게 챙김)
- 강제 손절: {sl}% (바닥이 뚫릴 경우 대비)

## 파라미터 
```json
{json.dumps(self.params, indent=2)}
```
"""

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = market_data.get("current_price", 0.0)
        rsi = market_data.get("rsi_14", 50)
        bb_lower = market_data.get("bb_lower", 0.0)
        
        buy_rsi_threshold = self.params.get("buy_rsi_threshold", 25)
        buy_bb_lower_break = self.params.get("buy_bb_lower_break", True)
        sell_rsi_threshold = self.params.get("sell_rsi_threshold", 45)
        take_profit_pct = self.params.get("take_profit_pct", 3.0)
        stop_loss_pct = self.params.get("stop_loss_pct", -4.0)
        position_size_ratio = self.params.get("position_size_ratio", 0.5)

        # 보유 수량 검사
        holdings = portfolio_info.get("holdings", {}) if portfolio_info else {}
        is_holding = ticker in holdings and holdings[ticker].get("volume", 0) > 0
        
        if is_holding:
            # 매도 로직: 짧은 반등 목표 달성 시 이익 실현
            if rsi >= sell_rsi_threshold:
                return Signal(SignalType.SELL, ticker, f"단기 반등 성공 (RSI: {rsi:.1f} >= {sell_rsi_threshold})", 1.0)
                
            avg_price = holdings[ticker].get("avg_price", 0.0)
            if avg_price > 0:
                profit = (current_price - avg_price) / avg_price * 100.0
                if profit >= take_profit_pct:
                    return Signal(SignalType.SELL, ticker, f"단기 과대낙폭 익절 (+{profit:.1f}%)", 1.0)
                if profit <= stop_loss_pct:
                    return Signal(SignalType.SELL, ticker, f"하락장 방어 손절 ({profit:.1f}%)", 1.0)
        else:
            # 매수 로직: 극단적 공포/투매 시 진입
            if rsi <= buy_rsi_threshold:
                # 볼린저 밴드 하단 돌파 여부 확인
                if buy_bb_lower_break and current_price <= bb_lower:
                    # 극단적 과매도, 볼린저 하단 이탈
                    strength = position_size_ratio
                    return Signal(SignalType.BUY, ticker, f"극단적 낙폭 감지 (RSI: {rsi:.1f}, BB Lower 붕괴)", strength)
                elif not buy_bb_lower_break:
                    # RSI 만으로 판단
                    strength = position_size_ratio * 0.8
                    return Signal(SignalType.BUY, ticker, f"과매도 구간 진입 (RSI: {rsi:.1f})", strength)
                    
        return Signal(SignalType.HOLD, ticker, "대기 상태")
