from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """전략이 반환하는 매매 시그널"""
    type: SignalType
    ticker: str
    reason: str
    # 매수 시: 투자금 대비 비율 (0.0 ~ 1.0), 매도 시: 보유 수량 대비 비율
    strength: float = 0.5

    def __str__(self):
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⏸️"}
        return f"{emoji.get(self.type.value, '❓')} {self.type.value} {self.ticker} (strength: {self.strength:.0%}) | {self.reason}"


class BaseStrategy(ABC):
    """
    투자 전략 베이스 클래스.
    각 전략은 이 클래스를 상속받아 evaluate()를 구현합니다.
    """

    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}

    @abstractmethod
    def evaluate(self, market_data: dict, portfolio_info: dict = None) -> Signal:
        """
        시장 데이터와 포트폴리오 정보를 바탕으로 매매 시그널을 반환합니다.

        Args:
            market_data: {'ticker', 'current_price', 'rsi_14', 'ma_20', 'ma_50', 
                          'trend', 'bb_upper', 'bb_lower', 'bb_mid', 
                          'high_20', 'low_20', 'closes' 등}
            portfolio_info: {'cash', 'holdings': {ticker: {volume, avg_price}}, 'total_value'}

        Returns:
            Signal 객체
        """
        pass

    @abstractmethod
    def get_strategy_description(self) -> str:
        """전략에 대한 한국어 설명을 반환합니다 (strategy.md용)."""
        pass

    def get_default_params(self) -> dict:
        """이 전략의 기본 파라미터를 반환합니다."""
        return {}

    def update_params(self, new_params: dict):
        """파라미터를 업데이트합니다."""
        self.params.update(new_params)
