from src.strategies.base import BaseStrategy, Signal, SignalType
import math

class VolatilityMomentumStrategy(BaseStrategy):
    """
    변동성 가중 모멘텀(Volatility-Weighted Momentum) 전략
    
    특징:
    머신러닝의 특성 조합(Feature Combination)에서 영감을 받아, 
    여러 가지 지표(RSI, MACD, 이동평균 추세)를 결합하여 스코어(Score)를 내고,
    급격한 변동성(ATR 폭주) 시 Whipsaw(가짜 신호)를 막기 위해 패널티를 줍니다.
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("변동성 가중 모멘텀", default)

    def get_default_params(self) -> dict:
        return {
            "score_threshold_buy": 6.0,   # 10점 만점에 이 점수를 넘어야 매수 (기존 7.0에서 하향)
            "take_profit_pct": 1.5,       # 1.5% 익절
            "stop_loss_pct": -2.0,        # -2.0% 손절
            "position_size_ratio": 0.25,  # 자산의 25% 진입
        }

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = float(market_data.get("current_price", 0))
        
        # 기본 지표들 추출
        # (UpbitMarketData.get_ohlcv_with_indicators 에서 해당 데이터들이 제공된다고 전제)
        rsi = float(market_data.get("rsi_14", 50))
        macd = float(market_data.get("macd", 0))
        macd_signal = float(market_data.get("macd_signal", 0))
        macd_hist = float(market_data.get("macd_hist", 0))
        sma_5 = float(market_data.get("sma_5", current_price))
        sma_20 = float(market_data.get("sma_20", current_price))
        
        # ATR 또는 단순 고저차를 이용한 변동성 측정 (정상적인 MarketData에 없다면 근사치 사용)
        high_20 = float(market_data.get("high_20", current_price * 1.05))
        low_20 = float(market_data.get("low_20", current_price * 0.95))
        
        # Feature 1: RSI Score (0~3점)
        # RSI가 50~70 사이일 때 상승 파동이 시작되었다고 판단하여 최고점 부여
        rsi_score = 0
        if 50 <= rsi <= 70:
            rsi_score = 3
        elif 40 <= rsi < 50 or 70 < rsi <= 75:
            rsi_score = 1.5
            
        # Feature 2: MACD Trend Score (0~3점)
        macd_score = 0
        if macd_hist > 0 and macd > macd_signal:
            macd_score = 3
        elif macd_hist > 0:
            macd_score = 1.5

        # Feature 3: Moving Average Momentum Score (0~4점)
        # 완벽한 정배열이 아니더라도 SMA 5 > SMA 20 (골든크로스 상태) 이고 가격이 SMA 20 위에 있으면 높은 점수
        ma_score = 0
        if current_price > sma_20 and sma_5 > sma_20:
            ma_score = 4
        elif current_price > sma_20:
            ma_score = 2

        # Base Score (Max 10)
        base_score = rsi_score + macd_score + ma_score

        # Feature 4: Volatility Penalty (ATR 대용)
        # 단기 20일 고점과 저점의 차이가 현재가의 20% 이상 차이나면 막대한 변동성으로 판단 (기존 15%에서 완화)
        price_range_pct = (high_20 - low_20) / current_price if current_price else 0
        volatility_penalty = 0
        if price_range_pct > 0.20:
            volatility_penalty = -3  # 엄청난 휩소 위험이 있으므로 감점
        elif price_range_pct < 0.02:
            volatility_penalty = -1  # 너무 횡보장이면 추세가 안 나옴
            
        final_score = base_score + volatility_penalty
        
        params = self.params
        score_threshold = params.get("score_threshold_buy", 7.0)

        # ------------------ 매도 로직 (포지션 보유 시) ------------------
        if portfolio_info and portfolio_info.get("volume", 0) > 0:
            avg_price = portfolio_info.get("avg_price", current_price)
            profit_pct = (current_price - avg_price) / avg_price * 100

            # 1. Take Profit
            if profit_pct >= params.get("take_profit_pct", 1.5):
                return Signal(
                    type=SignalType.SELL,
                    ticker=ticker,
                    reason=f"익절 목표 달성 (+{profit_pct:.2f}%)",
                    strength=1.0
                )
            # 2. Stop Loss
            if profit_pct <= params.get("stop_loss_pct", -2.0):
                return Signal(
                    type=SignalType.SELL,
                    ticker=ticker,
                    reason=f"손절 라인 이탈 ({profit_pct:.2f}%)",
                    strength=1.0
                )
            
            # 3. Score Reversal: 샀는데 지표 점수가 급격히 무너지면 (예: 3점 이하) 매도
            if final_score <= 3.0:
                return Signal(
                    type=SignalType.SELL,
                    ticker=ticker,
                    reason=f"총합 지표 모멘텀 붕괴 (Score: {final_score:.1f}/10)",
                    strength=0.8
                )
                
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason=f"포지션 유지 중 (수익률: {profit_pct:.2f}%, Score: {final_score:.1f})",
                strength=0.0
            )

        # ------------------ 매수 로직 (포지션 없을 시) ------------------
        if final_score >= score_threshold:
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"다면적 스코어 매수 임계치 돌파 (Score: {final_score:.1f}/10)",
                strength=params.get("position_size_ratio", 0.25)
            )

        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason=f"관망 (Score: {final_score:.1f}/10)",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        return f"""# 🧠 변동성 가중 모멘텀 전략 (ML-Inspired)

## 전략 개요
머신러닝의 다중 피쳐 결합(Feature Combination)에서 아이디어를 얻어, 
하나의 맹목적인 단일 지표에 의존하지 않고 가격(Price), 동력(RSI), 추세(MACD, MA), 변동성(비정상적 고저차)을 스코어링화하여 매매합니다.

## 매매 규칙
- **매수 조건**: (RSI Score + MACD Score + MA Score) - Volatility Penalty >= {p.get('score_threshold_buy', 6.0)} / 10점
- **매도 조건**: 
  1. 익절 (+{p.get('take_profit_pct', 1.5)}%)
  2. 손절 ({p.get('stop_loss_pct', -2.0)}%)
  3. 보유 중 모멘텀 스코어 3.0점 이하로 붕괴 시
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.25):.0%} 분할 매수

## 현재 파라미터
```json
{{"score_threshold_buy": {p.get('score_threshold_buy', 6.0)}, "take_profit_pct": {p.get('take_profit_pct', 1.5)}, "stop_loss_pct": {p.get('stop_loss_pct', -2.0)}, "position_size_ratio": {p.get('position_size_ratio', 0.25)}}}
```

## 장점
- 여러 시장 역학을 점수화하여 가짜 돌파나 휩소를 수학적으로 필터링.
- 비정상적인 변동성 폭주장에서는 매수를 기피하여 리스크 관리 강화.

## 단점
- 많은 지표의 교환비로 인해 거래 빈도가 예상보다 적을 수 있음.
"""
