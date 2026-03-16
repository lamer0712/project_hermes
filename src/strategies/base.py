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
        self.regime = params.get("regime", None)

    @abstractmethod
    def evaluate(
        self,
        ticker: str,
        setup_market_data: "pd.DataFrame",
        entry_market_data: "pd.DataFrame",
        regime: str,
        portfolio_info: dict = None,
    ) -> Signal:
        """
        시장 데이터와 포트폴리오 정보를 바탕으로 매매 시그널을 반환합니다.

        Args:
            ticker: 종목명
            setup_market_data: 환경판단용 일/장기 캔들 혹은 DataFrame
            entry_market_data: 진입/청산용 단기 캔들 DataFrame
            regime: 감지된 체제
            portfolio_info: 포트폴리오 메타데이터

        Returns:
            Signal 객체
        """
        pass

    def get_default_params(self) -> dict:
        """이 전략의 기본 파라미터를 반환합니다."""
        return {}

    def update_params(self, new_params: dict):
        """파라미터를 업데이트합니다."""
        self.params.update(new_params)
