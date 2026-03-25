import time
import requests
import pandas as pd
from src.interfaces.market_data import BaseMarketData
from src.utils.logger import logger
import talib


class KisMarketData(BaseMarketData):
    """한국투자증권 API를 통해 거시 데이터, 캔들 데이터 및 지표를 계산하는 클래스"""

    # 실패한 마켓 저장 
    _blacklisted_markets = set()

    @classmethod
    def get_ohlcv(cls, ticker: str, count: int, interval: str) -> pd.DataFrame:
        """
        todo: API 연동
        한투 국내주식 기간별 시세 조회 API 연동 필요.
        """
        logger.warning("[KisMarketData] get_ohlcv not fully implemented yet.")
        return pd.DataFrame()

    @classmethod
    def get_ohlcv_with_indicators_new(
        cls, ticker: str, count: int, interval: str
    ) -> pd.DataFrame:
        df = cls.get_ohlcv(ticker, count, interval)
        if df.empty:
            return df
            
        # TODO: Add TALib indicators similar to UpbitMarketData
        return df

    @classmethod
    def get_multiple_ohlcv_with_indicators(
        cls, tickers: list[str], count: int, interval: str
    ) -> dict:
        results = {}
        # TODO: Implement parallel fetching
        return results

    @classmethod
    def get_dynamic_target_coins(cls, top_n: int) -> list:
        """
        국내 주식 거래량 상위 종목 검색 등 동적 타겟팅 구현부
        """
        logger.warning("[KisMarketData] get_dynamic_target_coins returns dummy KOSPI tickers")
        return ["005930", "000660"] # 삼성전자, SK하이닉스 

    @staticmethod
    def regime_detect(ticker: str, df: pd.DataFrame) -> str:
        # TODO: 기존 암호화폐 regime_detect 로직이 주식시장에도 맞는지 변동성/파라미터 백테스트 후 수정
        return "ranging"

    @staticmethod
    def market_regime() -> str:
        """
        코스피 지표(0001 업종)를 가져와서 한국 시장 전체의 체제를 판단합니다.
        """
        logger.warning("[KisMarketData] market_regime returns dummy 'bullish'")
        return "bullish"
