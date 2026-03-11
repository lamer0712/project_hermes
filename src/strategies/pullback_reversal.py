from src.strategies.base import BaseStrategy, Signal, SignalType
import pandas as pd
import json


class PullbackReversalStrategy(BaseStrategy):
    """
    Pullback Reversal 전략

    구조
    - Trend filter
    - Setup (눌림)
    - Entry trigger (반등)

    특징
    - 상승장 눌림 매수
    - 반등 확인 후 진입
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("Pullback Reversal", default)

    def get_default_params(self) -> dict:
        return {
            "setup_rsi_threshold": 40,
            "entry_rsi_threshold": 45,
            "bb_position_threshold": 0.4,
            "require_bullish_trend": True,
            "position_size_ratio": 0.3,
            "volume_multiplier": 1.3
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = None
    ) -> Signal:

        # === Entry timeframe 데이터 ===
        current_price = float(entry_market_data.close.iloc[-1])
        rsi_entry = float(entry_market_data.rsi_14.iloc[-1])
        prev_rsi_entry = float(entry_market_data.rsi_14.iloc[-2])
        ma9 = float(entry_market_data.ma_9.iloc[-1])
        prev_price = float(entry_market_data.close.iloc[-2])
        prev_ma9 = float(entry_market_data.ma_9.iloc[-2])

        # === Setup timeframe 데이터 ===
        rsi_setup = float(setup_market_data.rsi_14.iloc[-1])
        ma20 = float(setup_market_data.ma_20.iloc[-1])
        ma50 = float(setup_market_data.ma_50.iloc[-1])
        bb_pos = float(setup_market_data.bb_position.iloc[-1])
        setup_price = float(setup_market_data.close.iloc[-1])

        # === Trend 판단 ===
        trend = "ranging"
        if ma20 > ma50 * 1.01:
            trend = "bullish"
        elif ma20 < ma50 * 0.99:
            trend = "bearish"

        require_bullish = self.params["require_bullish_trend"]
        position_ratio = self.params["position_size_ratio"]
        # === Trend Filter ===
        if require_bullish and trend != "bullish":
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason=f"추세 {trend} → 매수 금지",
                strength=0.0
            )

        # === Setup 조건 ===
        setup_ok = (
            rsi_setup < self.params["setup_rsi_threshold"]
            and bb_pos < self.params["bb_position_threshold"]
            and setup_price < ma20
        )

        if not setup_ok:
            return Signal(
                type=SignalType.HOLD,
                ticker=ticker,
                reason=f"Setup 미충족 (RSI {rsi_setup:.1f}, BB {bb_pos:.2f})",
                strength=0.0
            )

        # === Entry trigger ===
        rsi_cross_trigger = rsi_entry > self.params["entry_rsi_threshold"] and prev_rsi_entry <= self.params["entry_rsi_threshold"]
        ma_cross = prev_price <= prev_ma9 and current_price > ma9
        volume_trigger = entry_market_data.volume.iloc[-1] > entry_market_data.volume_ma20.iloc[-1] * self.params["volume_multiplier"]

        if rsi_cross_trigger and ma_cross and volume_trigger:

            strength = min(
                position_ratio * (1 + (50 - rsi_setup) / 50),
                1.0
            )

            return Signal(
                type=SignalType.BUY,
                ticker=ticker,
                reason=(
                    f"Pullback + Reversal "
                    f"(RSI_setup {rsi_setup:.1f}, RSI_entry {rsi_entry:.1f})"
                ),
                strength=strength
            )

        # === Exit ===
        rsi_sell = rsi_entry > 65
        bb_upper_touch = entry_market_data.close.iloc[-1] > entry_market_data.bb_upper.iloc[-1]

        if rsi_sell or bb_upper_touch:

            strength = min((rsi_entry - 65) / 35 + 0.5, 1.0)

            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=f"RSI {rsi_entry:.1f} 과매수 또는 BB 상단 터치",
                strength=strength
            )

        return Signal(
            type=SignalType.HOLD,
            ticker=ticker,
            reason="Setup 유지 중, Entry 대기",
            strength=0.0
        )

    def get_strategy_description(self) -> str:
        p = self.params
        p_json = json.dumps(p, ensure_ascii=False, indent=2)
        return f"""# 📉 Pullback Reversal 전략

## 전략 개요
상승장에서 눌림(pullback) 구간을 찾은 뒤
단기 반등이 시작될 때 진입하는 전략입니다.

## 매매 규칙

### Setup (눌림 확인)
- RSI < {p['setup_rsi_threshold']}
- BB position < {p['bb_position_threshold']}
- price < MA20

### Entry (반등 시작)
- RSI > {p['entry_rsi_threshold']}
- price crosses MA9
- volume > MA20 volume * {p['volume_multiplier']}

### Exit
- RSI > 65
- BB upper 터치

### 포지션 크기
가용 현금의 {p['position_size_ratio']:.0%}

## 현재 파라미터
```json
{{"setup_rsi_threshold": {p.get('setup_rsi_threshold', 40)}, "entry_rsi_threshold": {p.get('entry_rsi_threshold', 45)}, "bb_position_threshold": {p.get('bb_position_threshold', 0.4)}, "require_bullish_trend": {str(p.get('require_bullish_trend', True)).lower()}, "position_size_ratio": {p.get('position_size_ratio', 0.3)}, "volume_multiplier": {p.get('volume_multiplier', 1.3)}}}
```
### 장점
- 눌림 매수로 리스크 감소
- 반등 확인 후 진입하여 false signal 감소

### 단점
- 강한 상승장에서 진입 기회 감소
- 횡보장에서 신호 품질 저하
"""