# 📉 Pullback Reversal 전략

## 전략 개요
상승장에서 눌림(pullback) 구간을 찾은 뒤
단기 반등이 시작될 때 진입하는 전략입니다.

## 매매 규칙

### Setup (눌림 확인)
- RSI < 40
- BB position < 0.4
- price < MA20

### Entry (반등 시작)
- RSI > 45
- price crosses MA9
- volume > MA20 volume * 1.3

### Exit
- RSI > 65
- BB upper 터치

### 포지션 크기
가용 현금의 30%

## 현재 파라미터
```json
{"setup_rsi_threshold": 40, "entry_rsi_threshold": 45, "bb_position_threshold": 0.4, "require_bullish_trend": true, "position_size_ratio": 0.3, "volume_multiplier": 1.3}
```
### 장점
- 눌림 매수로 리스크 감소
- 반등 확인 후 진입하여 false signal 감소

### 단점
- 강한 상승장에서 진입 기회 감소
- 횡보장에서 신호 품질 저하
