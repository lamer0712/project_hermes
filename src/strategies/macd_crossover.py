from src.strategies.base import BaseStrategy, Signal, SignalType


class MACDCrossoverStrategy(BaseStrategy):
    """
    MACD 크로스오버 전략

    핵심 로직:
    - MACD 라인이 시그널 라인을 상향 돌파(골든크로스)하면 매수
    - MACD 라인이 시그널 라인을 하향 돌파(데드크로스)하면 매도
    - RSI 필터로 과매수 진입 방지 및 과매수 매도 강화
    - 수익률 기반 익절(Take-Profit) 및 손절(Stop-Loss) 로직 적용

    특징:
    - 잦은 휩소(Whipsaw)로 인한 손실을 방지하기 위해 최소 히스토그램 크기 필터 적용
    - 짧은 수익 구간에서 이익을 보존하고, 큰 손실을 방지함
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("MACD 크로스오버", default)

    def get_default_params(self) -> dict:
        return {
            "rsi_buy_filter": 60,            # RSI가 이 값 미만일 때만 매수 허용
            "rsi_sell_threshold": 70,        # RSI가 이 값 초과 시 무조건 매도
            "position_size_ratio": 0.25,      # 가용 현금의 25%씩 진입 (비중 축소)
            "min_histogram_buy": 0.0001,     # 매수 시 히스토그램의 최소 절댓값 (휩소 방지용)
            "take_profit_pct": 1.5,          # 익절: 매입가 대비 1.5% 상승 시
            "stop_loss_pct": -2.0,           # 손절: 매입가 대비 -2.0% 하락 시
        }

    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        ticker = market_data.get("ticker", "Unknown")
        current_price = float(market_data.get("current_price", 0))
        rsi = float(market_data.get("rsi_14", 50))
        macd_line = float(market_data.get("macd_line", 0))
        macd_signal = float(market_data.get("macd_signal", 0))
        macd_histogram = float(market_data.get("macd_histogram", 0))
        trend = market_data.get("trend", "ranging")

        rsi_buy_filter = self.params.get("rsi_buy_filter", 60)
        rsi_sell_th = self.params.get("rsi_sell_threshold", 70)
        position_ratio = self.params.get("position_size_ratio", 0.25)
        min_hist = self.params.get("min_histogram_buy", 0.0001)
        tp_pct = self.params.get("take_profit_pct", 1.5)
        sl_pct = self.params.get("stop_loss_pct", -2.0)

        # 포트폴리오 정보를 통한 보유 종목 수익률 계산 (TP/SL 용도)
        current_return_pct = 0.0
        has_holding = False
        if portfolio_info and "holdings" in portfolio_info:
            holding = portfolio_info["holdings"].get(ticker)
            if holding and holding.get("volume", 0) > 0 and holding.get("avg_price", 0) > 0:
                has_holding = True
                avg_price = holding["avg_price"]
                current_return_pct = ((current_price - avg_price) / avg_price) * 100

        # MACD 데이터 없으면 보류
        if macd_line == 0 and macd_signal == 0:
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason="MACD 데이터 없음 → 판단 보류",
                strength=0.0
            )

        # 매도 시그널 0: 익절/손절 먼저 우선 평가 (보유 중일 때만)
        if has_holding:
            if current_return_pct >= tp_pct:
                return Signal(
                    type=SignalType.SELL,
                    ticker=ticker,
                    reason=f"💰 익절 (수익률 {current_return_pct:+.2f}% ≥ 목표 {tp_pct}%) → 매도",
                    strength=1.0
                )
            if current_return_pct <= sl_pct:
                return Signal(
                    type=SignalType.SELL,
                    ticker=ticker,
                    reason=f"🚨 손절 (수익률 {current_return_pct:+.2f}% ≤ 제한 {sl_pct}%) → 매도",
                    strength=1.0
                )

        # 매수 시그널: MACD 골든크로스 + 히스토그램 필터 통과 + RSI 과매수 아닌 상태
        if macd_histogram > min_hist and macd_line > macd_signal:
            if rsi >= rsi_buy_filter:
                return Signal(
                    type=SignalType.HOLD,
                    ticker=ticker,
                    reason=f"MACD 골든크로스이나 RSI({rsi:.1f}) ≥ {rsi_buy_filter} → 과매수 근접, 대기",
                    strength=0.0
                )

            # 히스토그램 크기에 비례한 시그널 강도
            hist_strength = min(abs(macd_histogram) / max(abs(macd_signal), 0.001) * 0.5, 0.5)
            strength = min(position_ratio + hist_strength, 1.0)
            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=f"📈 MACD 골든크로스 (MACD: {macd_line:.4f} > Signal: {macd_signal:.4f}), RSI: {rsi:.1f} → 매수",
                strength=strength
            )

        # 매도 시그널 1: RSI 과매수
        if rsi > rsi_sell_th:
            strength = min((rsi - rsi_sell_th) / (100 - rsi_sell_th) + 0.5, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"⚠️ RSI({rsi:.1f}) > {rsi_sell_th} 과매수 → 매도",
                strength=strength
            )

        # 매도 시그널 2: MACD 데드크로스 (히스토그램 음수)
        if macd_histogram < 0 and macd_line < macd_signal:
            hist_strength = min(abs(macd_histogram) / max(abs(macd_signal), 0.001) * 0.3, 0.3)
            strength = min(0.5 + hist_strength, 1.0)
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"📉 MACD 데드크로스 (MACD: {macd_line:.4f} < Signal: {macd_signal:.4f}) → 매도",
                strength=strength
            )

        # HOLD
        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason=f"MACD({macd_line:.4f}), Signal({macd_signal:.4f}), RSI({rsi:.1f}) → 관망",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        return f"""# 📈 MACD 크로스오버 전략 (Option A 강화 버전)

## 전략 개요
MACD(이동평균수렴확산)와 RSI를 결합한 전략으로, 단기 장세의 휩소(Whipsaw)에 대응하도록 보완되었습니다.
포지션 당 진입 비중을 줄이고 히스토그램 필터와 명확한 익절/손절 구간을 두어 손실 누적을 최소화합니다.

## 매매 규칙
- **매수 조건**: MACD 골든크로스 (히스토그램 > {p.get('min_histogram_buy', 0.0001)}) AND RSI < {p.get('rsi_buy_filter', 60)}
- **매도 조건**: 
  1. 익절: 매입가 대비 +{p.get('take_profit_pct', 1.5)}% 이상
  2. 손절: 매입가 대비 {p.get('stop_loss_pct', -2.0)}% 이하
  3. 지표 데드크로스: MACD 히스토그램 < 0 또는 RSI > {p.get('rsi_sell_threshold', 70)}
- **포지션 크기**: 가용 현금의 {p.get('position_size_ratio', 0.25):.0%} 분할 진입

## 현재 파라미터
```json
{{"rsi_buy_filter": {p.get('rsi_buy_filter', 60)}, "rsi_sell_threshold": {p.get('rsi_sell_threshold', 70)}, "position_size_ratio": {p.get('position_size_ratio', 0.25)}, "min_histogram_buy": {p.get("min_histogram_buy", 0.0001)}, "take_profit_pct": {p.get("take_profit_pct", 1.5)}, "stop_loss_pct": {p.get("stop_loss_pct", -2.0)}}}
```

## 장점
- 타이트한 손절매와 목표가 익절로 수익 보전력 강화
- 히스토그램 최저치 필터로 횡보 구간 오신호 회피
- 비중 분산으로 리스크 감소
"""
