# Manager Multi-Strategy Mapping

현재 매니저는 시장 상황(`btc_regime`)을 매수 필터로 사용하며, 각 개별 종목의 체제(Regime)에 따라 동적으로 전략을 선택하여 매매합니다.

## 종목별 전략 매핑 (Ticker Regime)
- **bullish**: PullbackTrend
- **ranging**: MeanReversion
- **volatile**: Breakout

## 거시 매수 필터 (BTC Regime)
- **bullish / ranging / volatile**: 매수 허용
- **bearish / panic**: 전 종목 신규 매수 차단 (매도만 수행)
