"""
Core 데이터 흐름을 위한 DTO (Data Transfer Objects).
사이클 내 데이터 전달을 타입 안전하게 만듭니다.
"""

from dataclasses import dataclass, field
from typing import Optional
from src.strategies.base import Signal


@dataclass
class TickerEvaluation:
    """종목별 평가 결과"""

    ticker: str
    regime: Optional[str]
    strategy: str
    signal_type: str  # "BUY", "SELL", "HOLD"
    signal_reason: str
    signal_strength: float
    signal_confidence: float
    current_price: float

    def to_dict(self) -> dict:
        """기존 ticker_stats dict 호환용"""
        return {
            "ticker": self.ticker,
            "regime": self.regime,
            "strategy": self.strategy,
            "signal_type": self.signal_type,
            "signal_reason": self.signal_reason,
            "signal_strength": self.signal_strength,
            "signal_confidence": self.signal_confidence,
            "current_price": self.current_price,
        }


@dataclass
class CycleContext:
    """사이클 실행 시 공유되는 컨텍스트"""

    agent_name: str
    market_regime: str
    buy_filter_passed: bool
    available_cash: float
    holdings: dict
    portfolio_info: Optional[dict]
    current_prices: dict = field(default_factory=dict)
    ticker_stats: dict = field(default_factory=dict)  # ticker -> TickerEvaluation
    buy_candidates: list = field(default_factory=list)  # (signal, strategy, market_data)
