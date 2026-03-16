import os
import requests
import jwt
import uuid
import hashlib
from urllib.parse import urlencode
from src.utils.logger import logger
from src.utils.market_data import UpbitMarketData
from decimal import Decimal, ROUND_HALF_UP


class UpbitBroker:
    """Upbit API를 통해 실제 매수 및 매도 주문을 넣는 클래스"""

    BASE_URL = "https://api.upbit.com/v1"

    def __init__(self):
        # 환경 변수에서 키를 읽어옵니다. (보안 목적)
        self.access_key = os.environ.get("UPBIT_OPEN_API_ACCESS_KEY")
        self.secret_key = os.environ.get("UPBIT_OPEN_API_SECRET_KEY")

    def is_configured(self) -> bool:
        """API 키가 정상적으로 설정되어 있는지 확인합니다."""
        return bool(self.access_key and self.secret_key)

    # ==========================================
    # Market Data 분리/캡슐화 영역
    # ==========================================

    @property
    def blacklisted_markets(self) -> set:
        """현재 마켓 데이터 조회 실패로 등록된 블랙리스트 마켓 반환"""
        return UpbitMarketData._blacklisted_markets

    def get_dynamic_target_coins(self, top_n: int = 10) -> list[str]:
        """UpbitMarketData를 통해 시장 데이터를 기반으로 타겟 코인을 동적으로 선정합니다."""
        return UpbitMarketData.get_dynamic_target_coins(top_n=top_n)

    def get_ohlcv_with_indicators_new(
        self, ticker: str, count: int = 100, interval: str = "minutes/60"
    ):
        """UpbitMarketData를 통해 DataFrame 포맷의 고급 시장 데이터를 제공합니다."""
        return UpbitMarketData.get_ohlcv_with_indicators_new(ticker, count, interval)

    def regime_detect(self, df) -> str:
        """UpbitMarketData의 regime 판독 로직을 래핑하여 제공합니다."""
        return UpbitMarketData.regime_detect(df)

    def btc_regime(self) -> str:
        """UpbitMarketData의 regime 판독 로직을 래핑하여 제공합니다."""
        return UpbitMarketData.btc_regime()

    # ==========================================
    # 기존 Broker 고유 영역 (매매 로직 등)
    # ==========================================

    def _generate_headers(self, query: dict = None) -> dict:
        """인증을 위한 JWT 토큰 헤더를 생성합니다."""
        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }

        if query:
            query_string = urlencode(query).encode()
            m = hashlib.sha512()
            m.update(query_string)
            query_hash = m.hexdigest()
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"

        jwt_token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        authorize_token = f"Bearer {jwt_token}"

        return {"Authorization": authorize_token}

    def _format_price(self, price: float) -> str:
        """
        업비트의 최신 호가 단위 정책에 맞춰 가격을 반올림 및 포맷팅합니다.
        """
        p = float(price)

        # 1. 구간별 호가 단위(tick) 정의 (내림차순)
        # (기준 가격, 해당 구간의 호가 단위)
        ticks = [
            (2000000, 1000.0),
            (1000000, 1000.0),
            (500000, 500.0),
            (100000, 100.0),
            (50000, 50.0),
            (10000, 10.0),
            (5000, 5.0),
            (1000, 1.0),
            (100, 1.0),
            (10, 0.1),
            (1, 0.01),
            (0.1, 0.001),
            (0.01, 0.0001),
            (0.001, 0.00001),
            (0.0001, 0.000001),
            (0.00001, 0.0000001),
            (0, 0.00000001),
        ]

        tick = 0.00000001  # 기본값
        for threshold, t_size in ticks:
            if p >= threshold:
                tick = t_size
                break

        # 2. 호가 단위에 맞게 반올림 (부동소수점 오차 방지)
        # adjusted = round(p / tick) * tick
        tick_dec = Decimal(str(tick))
        adjusted = float(
            (Decimal(str(p)) / tick_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            * tick_dec
        )

        # 3. 출력 포맷팅 결정
        # 호가 단위의 소수점 자릿수를 계산하여 그에 맞게 출력
        if tick >= 1.0:
            return str(int(adjusted))
        else:
            # tick이 0.1이면 소수점 1자리, 0.01이면 2자리...
            decimal_places = abs(Decimal(str(tick)).as_tuple().exponent)
            return f"{adjusted:.{decimal_places}f}"

    def _format_volume(self, volume: float) -> str:
        """업비트 수량 형식(최대 소수점 8자리)에 맞춰 포맷팅합니다."""
        vol_str = f"{float(volume):.8f}"
        if "." in vol_str:
            vol_str = vol_str.rstrip("0").rstrip(".")
        return vol_str if vol_str else "0"

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
        """
        매수 또는 매도 주문을 실행합니다.

        Args:
            ticker: 'KRW-BTC' 등
            side: 'bid' (매수) 또는 'ask' (매도)
            volume: 주문 수량
            price: 주문 가격
            ord_type: 'limit' (지정가), 'price' (시장가 매수), 'market' (시장가 매도)
            current_price: 현재가 (슬리피지 보호용)
            slippage_tolerance: 허용 슬리피지 (기본 0.5%)
        """
        mock_trading = os.environ.get("MOCK_TRADING", "False").lower() == "true"

        if mock_trading:
            import time

            uuid_str = f"mock-uuid-{int(time.time())}"
            logger.info(
                f"[Broker API - MOCK] 모의 주문 요청: {side} {ticker} (Type: {ord_type}, Price: {price}, Volume: {volume})"
            )
            logger.info(
                f"[Broker API - MOCK] 주문이 성공적으로 들어갔습니다: uuid={uuid_str}"
            )
            return {"uuid": uuid_str, "state": "done", "mock": True}

        if not self.is_configured():
            logger.error(
                "[Broker API Error] Upbit API 키가 설정되지 않았습니다. 매매가 취소됩니다."
            )
            return {"error": "API keys missing"}

        url = f"{self.BASE_URL}/orders"

        # 기본 쿼리 파라미터 구성
        query = {
            "market": ticker,
            "side": side,
            "ord_type": ord_type,
        }

        # 슬리피지 예방: 시장가 매수/매도를 제한적 지정가(IOC)로 변환
        if ord_type in ("price", "market") and current_price is not None:
            if ord_type == "price":
                if price is None:
                    raise ValueError("시장가 매수(price)는 price 인수가 필수입니다.")
                limit_price = current_price * (1 + slippage_tolerance)
                volume_calc = float(price) / limit_price
                query["price"] = self._format_price(limit_price)
                query["volume"] = self._format_volume(volume_calc)
            elif ord_type == "market":
                if volume is None:
                    raise ValueError("시장가 매도(market)는 volume 인수가 필수입니다.")
                limit_price = current_price * (1 - slippage_tolerance)
                query["price"] = self._format_price(limit_price)
                query["volume"] = self._format_volume(volume)

            ord_type = "limit"
            query["ord_type"] = "limit"
            query["time_in_force"] = "ioc"  # Immediate Or Cancel
            logger.info(
                f"[Broker API] 슬리피지 보호 활성화: {side} {ticker} (Limit IOC, P: {query['price']}, V: {query['volume']}), CP: {current_price}"
            )

        # 시장가 매수(지정된 금액만큼)인 경우 - current_price가 없어서 변환 불가 시
        elif ord_type == "price":
            if price is None:
                raise ValueError("시장가 매수(price)는 price 인수가 필수입니다.")
            query["price"] = self._format_price(price)

        # 시장가 매도(지정된 수량만큼)인 경우 - current_price가 없어서 변환 불가 시
        elif ord_type == "market":
            if volume is None:
                raise ValueError("시장가 매도(market)는 volume 인수가 필수입니다.")
            query["volume"] = self._format_volume(volume)

        # 지정가 매수/매도인 경우
        elif ord_type == "limit":
            if price is None or volume is None:
                raise ValueError(
                    "지정가 매수/매도(limit)는 price와 volume 인수가 모두 필수입니다."
                )
            query["volume"] = self._format_volume(volume)
            query["price"] = self._format_price(price)

        headers = self._generate_headers(query)

        try:
            logger.info(
                f"[Broker API] 주문 요청 중: {side} {ticker} (Type: {ord_type})"
            )
            response = requests.post(url, params=query, headers=headers)
            response.raise_for_status()
            result = response.json()
            logger.info(
                f"[Broker API Success] 주문이 성공적으로 들어갔습니다: uuid={result.get('uuid')}"
            )
            return result
        except requests.exceptions.RequestException as e:
            logger.error(f"[Broker API Error] 매매 실패: {e}")
            if (
                "response" in locals()
                and response
                and hasattr(response, "text")
                and response.text
            ):
                logger.info(f"상세 메시지: {response.text}")
            return {
                "error": str(e),
                "details": (
                    response.text
                    if ("response" in locals() and hasattr(response, "text"))
                    else ""
                ),
            }

    def get_order(self, uuid_str: str) -> dict:
        """
        주문 UUID로 주문 상세 내역을 조회합니다. 체결 수량 및 지불 수수료 확인용.
        """
        if not self.is_configured():
            return {"error": "API keys missing"}

        url = f"{self.BASE_URL}/order"
        query = {"uuid": uuid_str}
        headers = self._generate_headers(query)

        try:
            response = requests.get(url, params=query, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"[Broker API Error] 주문 조회 실패: {e}")
            if (
                "response" in locals()
                and response
                and hasattr(response, "text")
                and response.text
            ):
                logger.info(f"상세 메시지: {response.text}")
            return {
                "error": str(e),
                "details": (
                    response.text
                    if ("response" in locals() and hasattr(response, "text"))
                    else ""
                ),
            }

    def get_balances(self) -> list:
        """현재 계좌의 전체 잔고를 조회합니다."""
        if not self.is_configured():
            return []

        url = f"{self.BASE_URL}/accounts"
        headers = self._generate_headers()

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"[Broker API Error] 잔고 조회 실패: {e}")
            if "response" in locals() and response is not None and response.text:
                logger.info(f"상세 메시지: {response.text}")
            return []


if __name__ == "__main__":
    broker = UpbitBroker()
    if not broker.is_configured():
        logger.info(
            "환경변수 UPBIT_OPEN_API_ACCESS_KEY 와 UPBIT_OPEN_API_SECRET_KEY 를 설정해야 작동합니다."
        )
    else:
        balances = broker.get_balances()
        logger.info(f"현재 잔여 자산 내역: {balances}")
