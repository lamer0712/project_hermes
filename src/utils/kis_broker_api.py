import os
import time
import json
import requests
from src.interfaces.broker import BaseBroker
from src.utils.logger import logger
from src.utils.kis_market_data import KisMarketData
from decimal import Decimal, ROUND_HALF_UP

class KisBroker(BaseBroker):
    """한국투자증권(KIS) API를 통해 실제 매수 및 매도 주문을 넣는 클래스"""

    def __init__(self):
        self.app_key = os.environ.get("KIS_APP_KEY")
        self.app_secret = os.environ.get("KIS_APP_SECRET")
        self.cano = os.environ.get("KIS_CANO")        # 종합계좌번호 (8자리)
        self.acnt_prdt_cd = os.environ.get("KIS_ACNT_PRDT_CD", "01") # 계좌상품코드 (보통 01)
        
        # 실전/모의투자 URL 분기
        self.is_paper = os.environ.get("KIS_IS_PAPER", "true").lower() == "true"
        if self.is_paper:
            self.base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self.base_url = "https://openapi.koreainvestment.com:9443"
            
        self.token_file = "kis_token.json"
        self._access_token = None
        self._token_expired_at = 0

    def is_configured(self) -> bool:
        """API 키가 정상적으로 설정되어 있는지 확인합니다."""
        return bool(self.app_key and self.app_secret and self.cano and self.acnt_prdt_cd)

    def _get_access_token(self) -> str:
        """액세스 토큰을 발급받거나 캐시된 토큰을 반환합니다."""
        if not self.is_configured():
            return ""

        now = time.time()
        # 메모리 캐시 확인
        if self._access_token and now < self._token_expired_at:
            return self._access_token
        
        # 파일 캐시 확인
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, "r") as f:
                    data = json.load(f)
                    if now < data.get("expired_at", 0):
                        self._access_token = data["access_token"]
                        self._token_expired_at = data["expired_at"]
                        return self._access_token
            except Exception as e:
                logger.warning(f"[KisBroker] 토큰 파일 읽기 실패: {e}")

        # 새로 발급
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        headers = {"content-type": "application/json"}
        
        try:
            logger.info("[KisBroker] 새로운 KIS Access Token 발급 요청...")
            res = requests.post(url, json=payload, headers=headers)
            res.raise_for_status()
            res_data = res.json()
            
            self._access_token = res_data["access_token"]
            # KIS 토큰은 24시간 유효. 넉넉하게 23시간(82800초)만 유지
            self._token_expired_at = now + 82800 
            
            with open(self.token_file, "w") as f:
                json.dump({
                    "access_token": self._access_token,
                    "expired_at": self._token_expired_at
                }, f)
                
            logger.info("[KisBroker] 새로운 KIS Access Token 발급 및 캐싱 완료")
            return self._access_token
        except Exception as e:
            logger.error(f"[KisBroker Error] 토큰 발급 실패: {e}")
            return ""

    def _get_common_headers(self, tr_id: str) -> dict:
        """한투 API 공통 헤더를 생성합니다."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id
        }

    # ==========================================
    # Market Data 분리/캡슐화 영역
    # ==========================================

    @property
    def blacklisted_markets(self) -> set:
        return KisMarketData._blacklisted_markets

    def get_dynamic_target_coins(self, top_n: int = 10) -> list[str]:
        return KisMarketData.get_dynamic_target_coins(top_n=top_n)

    def get_ohlcv_with_indicators_new(self, ticker: str, count: int = 100, interval: str = "minutes/60"):
        return KisMarketData.get_ohlcv_with_indicators_new(ticker, count, interval)

    def get_multiple_ohlcv_with_indicators(self, tickers: list[str], count: int = 100, interval: str = "minutes/60") -> dict:
        return KisMarketData.get_multiple_ohlcv_with_indicators(tickers, count, interval)

    def regime_detect(self, ticker: str, df) -> str:
        return KisMarketData.regime_detect(ticker, df)

    def market_regime(self) -> str:
        return KisMarketData.market_regime()

    # ==========================================
    # 기존 Broker 고유 영역 (매매 로직 등)
    # ==========================================

    def _format_price(self, price: float, is_kospi: bool = True) -> str:
        """
        국내 주식 시장 호가 단위 (2023 변경 기준 적용)
        - 2,000원 미만: 1원
        - 5,000원 미만: 5원
        - 20,000원 미만: 10원
        - 50,000원 미만: 50원
        - 200,000원 미만: 100원
        - 500,000원 미만: 500원
        - 500,000원 이상: 1,000원
        """
        p = float(price)
        if p < 2000: tick = 1
        elif p < 5000: tick = 5
        elif p < 20000: tick = 10
        elif p < 50000: tick = 50
        elif p < 200000: tick = 100
        elif p < 500000: tick = 500
        else: tick = 1000
            
        tick_dec = Decimal(str(tick))
        adjusted = float((Decimal(str(p)) / tick_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_dec)
        return str(int(adjusted))

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
        """한국투자증권 매수/매도 주문 실행"""
        if not self.is_configured():
            return {"error": "API keys missing"}
            
        # TODO: 주식 주문 API 연동 (TTTC0802U 등)
        logger.warning(f"[KisBroker] 주문 로직 연동 안됨. (ticker: {ticker}, side: {side})")
        return {"uuid": "dummy-uuid", "state": "done", "mock": True}

    def get_order(self, uuid_str: str) -> dict:
        """주문 UUID (또는 주문번호)로 상세 내역 조회"""
        return {"error": "Not implemented"}

    def get_balances(self) -> list:
        """현재 계좌의 전체 잔고 조회"""
        if not self.is_configured():
            return []
            
        # 실전: TTTC8434R, 모의: VTTC8434R
        tr_id = "VTTC8434R" if self.is_paper else "TTTC8434R"
        headers = self._get_common_headers(tr_id)
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        try:
            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            data = res.json()
            
            # TODO: 업비트 잔고 포맷 [{"currency": "KRW", "balance": "100000"}] 과 맞추어 리턴파싱 필요
            return [{"currency": "KRW", "balance": data.get("output2", [{}])[0].get("dnca_tot_amt", "0")}]
        except Exception as e:
            logger.error(f"[KisBroker Error] 잔고 조회 실패: {e}")
            return []

    def get_orderbook(self, ticker: str) -> list:
        """한투 호가창 조회"""
        return []

if __name__ == "__main__":
    broker = KisBroker()
    if not broker.is_configured():
        logger.info("KIS 환경변수(KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ACNT_PRDT_CD)가 설정되어야 합니다.")
    else:
        logger.info("[테스트] 한투 액세스 토큰 발급 시도...")
        token = broker._get_access_token()
        logger.info(f"토큰 발급 성공: {token[:10]}...")
        
        logger.info("[테스트] 잔고 조회 시도...")
        balances = broker.get_balances()
        logger.info(f"조회된 잔고: {balances}")
