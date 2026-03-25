from abc import ABC, abstractmethod
import pandas as pd


class BaseMarketData(ABC):
    """모든 거래소 시장 데이터(시세, 종목, 보조지표 등)의 공통 인터페이스입니다."""

    @classmethod
    @abstractmethod
    def get_ohlcv(cls, ticker: str, count: int, interval: str) -> pd.DataFrame:
        pass

    @classmethod
    @abstractmethod
    def get_ohlcv_with_indicators_new(
        cls, ticker: str, count: int, interval: str
    ) -> pd.DataFrame:
        pass

    @classmethod
    @abstractmethod
    def get_multiple_ohlcv_with_indicators(
        cls, tickers: list[str], count: int, interval: str
    ) -> dict:
        pass

    @classmethod
    @abstractmethod
    def get_dynamic_target_coins(cls, top_n: int) -> list:
        pass

    @staticmethod
    @abstractmethod
    def regime_detect(ticker: str, df: pd.DataFrame) -> str:
        pass

    @staticmethod
    @abstractmethod
    def market_regime() -> str:
        """
        시장의 전체적인 장세(상승장, 하락장 등)를 파악합니다.
        (기존 btc_regime 의 범용 버전)
        """
        pass
