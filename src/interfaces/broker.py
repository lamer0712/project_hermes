from abc import ABC, abstractmethod


class BaseBroker(ABC):
    """모든 브로커(업비트, 한투 등)의 공통 매매 및 조회 인터페이스입니다."""

    @abstractmethod
    def is_configured(self) -> bool:
        """API 키 등이 정상적으로 설정되어 있는지 확인합니다."""
        pass

    @abstractmethod
    def place_order(
        self,
        ticker: str,
        side: str,
        volume: str = None,
        price: str = None,
        ord_type: str = "limit",
        current_price: float = None,
        slippage_tolerance: float = 0.005,
    ) -> dict:
        """매수 또는 매도 주문을 실행합니다."""
        pass

    @abstractmethod
    def get_order(self, uuid_str: str) -> dict:
        """주문 UUID로 주문 상세 내역을 조회합니다."""
        pass

    @abstractmethod
    def get_balances(self) -> list:
        """현재 계좌의 전체 잔고를 조회합니다."""
        pass

    @abstractmethod
    def get_orderbook(self, ticker: str) -> list:
        """특정 마켓의 오더북(호가창)을 조회합니다."""
        pass
