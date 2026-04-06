import time
import numpy as np
import requests
import concurrent.futures
from src.utils.logger import logger
import pandas as pd
import talib
from src.interfaces.market_data import BaseMarketData


class UpbitMarketData(BaseMarketData):
    """Upbit API를 통해 거시 데이터, 캔들 데이터 및 지표를 계산하는 클래스"""

    BASE_URL = "https://api.upbit.com/v1"

    # 실패한 마켓 저장 (최소 주기 동안 재요청 방지)
    _blacklisted_markets = set(
        [
            "KRW-WEMIX",
            "KRW-XYM",
            "KRW-MEETONE",
            "KRW-APENFT",
            "KRW-ADD",
            "KRW-CHL",
            "KRW-HORUS",
            "KRW-BLACK",
        ]
    )

    @staticmethod
    def calculate_adx(df, period=14):
        high = df.high
        low = df.low
        close = df.close

        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        tr = (
            pd.concat(
                [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            )
        ).max(axis=1)

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)

        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        plus_dm = plus_dm.ewm(alpha=1 / period, adjust=False).mean()
        minus_dm = minus_dm.ewm(alpha=1 / period, adjust=False).mean()

        plus_di = 100 * plus_dm / atr
        minus_di = 100 * minus_dm / atr

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=1 / period, adjust=False).mean()

        return adx, plus_di, minus_di

    @staticmethod
    def market_regime():
        df = UpbitMarketData.get_ohlcv_with_indicators_new(
            "KRW-BTC", count=100, interval="minutes/60"
        )

        price = df.close.iloc[-1]
        prev_price = df.close.iloc[-2]
        atr = df.atr_14.iloc[-1]
        change = (price - prev_price) / prev_price
        volatility = atr / price

        adx = df.adx_14.iloc[-1]
        ema20 = df.ma_20.iloc[-1]
        ema50 = df.ma_50.iloc[-1]

        # 패닉 덤프
        if change < -0.04:
            return "panic"

        # 고변동
        if volatility > 0.035:
            return "volatile"

        # 추세
        if adx > 25:
            if ema20 > ema50:
                return "bullish"
            else:
                return "bearish"

        return "ranging"

    @staticmethod
    def regime_detect(ticker: str, df):
        price = df.close.iloc[-1]
        high20 = df.high_20.iloc[-1]

        ma20 = df.ma_20.iloc[-1]
        ma60 = df.ma_60.iloc[-1]

        rsi = df.rsi_14.iloc[-1]
        adx = df.adx_14.iloc[-1]

        atr = df.atr_14.iloc[-1]
        volatility = atr / price
        vol_mean = df.atr_14.rolling(50).mean().iloc[-1] / price

        volume = df.volume.iloc[-1]
        volume_mean = df.volume.rolling(20).mean().iloc[-1]

        drop = df.close.pct_change(3).iloc[-1]

        trend_strength = (ma20 - ma60) / ma60
        ema_slope = df.ema_20.pct_change(5).iloc[-1]

        # 1. Panic (event)
        if (
            drop < -0.05
            and volume > volume_mean * 1.5
            and rsi < 40
            and ema_slope < 0
            and trend_strength < 0
        ):
            return "panic"

        # 🔥 1.5 Recovery (하락 → 반등 초입)
        if (
            ema_slope > 0
            and price > ma20
            and volume > volume_mean * 1.2
            and trend_strength < 0
        ):
            return "recovery"

        # 🔥 2. Early Breakout (강화)
        if (
            price >= high20
            and volume > volume_mean * 1.5
            and ema_slope > 0
            and price > ma20
        ):
            return "earlybreakout"

        # 3. Strong trends
        if trend_strength > 0.025 and adx > 25 and ema_slope > 0:
            return "bullish"

        if trend_strength < -0.025 and adx > 25 and ema_slope < 0:
            return "bearish"

        # 4. Weak trends (빠르게 감지)
        if trend_strength > 0.008 and ema_slope > 0:
            return "weakbullish"

        if trend_strength < -0.008 and ema_slope < 0:
            return "weakbearish"

        # 5. Range / Volatility (조건 타이트하게)
        if adx < 18 and abs(trend_strength) < 0.006:
            if volatility > vol_mean * 1.3:
                return "volatile"
            else:
                return "ranging"

        return "neutral"

    @classmethod
    def get_ohlcv(
        cls, ticker: str, count: int = 200, interval: str = "minutes/60"
    ) -> pd.DataFrame:

        if ticker in cls._blacklisted_markets:
            return pd.DataFrame()

        url = f"{cls.BASE_URL}/candles/{interval}"
        params = {"market": ticker, "count": count}
        headers = {"accept": "application/json"}

        max_retries = 3

        for attempt in range(max_retries):
            try:
                r = requests.get(url, headers=headers, params=params)

                if r.status_code == 429:
                    time.sleep(2**attempt)
                    continue

                r.raise_for_status()
                data = r.json()

                if not data:
                    return pd.DataFrame()

                df = pd.DataFrame(data)

                df = df.rename(
                    columns={
                        "candle_date_time_utc": "time",
                        "opening_price": "open",
                        "high_price": "high",
                        "low_price": "low",
                        "trade_price": "close",
                        "candle_acc_trade_volume": "volume",
                    }
                )

                df = df[["time", "open", "high", "low", "close", "volume"]]

                df["time"] = pd.to_datetime(df["time"])

                # 업비트는 최신순 → 시간순 정렬
                df = df.sort_values("time").reset_index(drop=True)

                return df

            except requests.exceptions.HTTPError as e:
                if r.status_code in [400, 404]:
                    cls._blacklisted_markets.add(ticker)
                    logger.info(
                        f"[Market Data] unsupported market → blacklist: {ticker}"
                    )
                    return pd.DataFrame()

                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    logger.error(f"[Market Data Error] {e}")
                    return pd.DataFrame()

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    logger.error(f"[Market Data Error] {e}")
                    return pd.DataFrame()

        return pd.DataFrame()

    @classmethod
    def get_ohlcv_with_indicators_new(
        cls,
        ticker: str,
        count: int = 100,
        interval: str = "minutes/15",
        current_price: float = None,
    ):
        df = cls.get_ohlcv(ticker, count, interval)
        if df.empty:
            return df

        if current_price is not None:
            df.loc[df.index[-1], "close"] = current_price
            df.loc[df.index[-1], "high"] = max(df["high"].iloc[-1], current_price)
            df.loc[df.index[-1], "low"] = min(df["low"].iloc[-1], current_price)

        # 위에서 df가 이미 수정되었으므로, 아래 변수들은 최신값을 가집니다.
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # high / low
        df["high_10"] = high.rolling(10).max()
        df["low_10"] = low.rolling(10).min()
        df["high_20"] = high.rolling(20).max()
        df["low_20"] = low.rolling(20).min()

        # Moving averages
        df["ma_9"] = close.rolling(9).mean()
        df["ma_20"] = close.rolling(20).mean()
        df["ma_50"] = close.rolling(50).mean()
        df["ma_60"] = close.rolling(60).mean()
        df["volume_ma20"] = volume.rolling(20).mean()

        ## RSI
        df["rsi_14"] = talib.RSI(close, timeperiod=14)

        ## Bollinger Bands
        df["bb_upper"], df["bb_mid"], df["bb_lower"] = talib.BBANDS(
            close, timeperiod=20, nbdevup=2, nbdevdn=2
        )
        # Double Bollinger Bands (1.0 SD)
        df["bb_upper1"], _, df["bb_lower1"] = talib.BBANDS(
            close, timeperiod=20, nbdevup=1, nbdevdn=1
        )
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (df["bb_mid"] + 1e-8)
        df["bb_position"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])

        ## ATR
        df["atr_14"] = talib.ATR(high, low, close, timeperiod=14)

        ## ADX
        df["adx_14"] = talib.ADX(high, low, close, timeperiod=14)
        df["plus_di_14"] = talib.PLUS_DI(high, low, close, timeperiod=14)
        df["minus_di_14"] = talib.MINUS_DI(high, low, close, timeperiod=14)

        ## VWAP (Volume Weighted Average Price)

        tp = (high + low + close) / 3
        pv = volume * tp
        window = 96  # 15m 기준 1일
        df["vwap"] = pv.rolling(window).sum() / volume.rolling(window).sum()

        ## EMA
        df["ema_20"] = talib.EMA(close, timeperiod=20)
        df["ema_50"] = talib.EMA(close, timeperiod=50)
        df["ema_200"] = talib.EMA(close, timeperiod=200)

        ## Change 5
        df["change_5"] = close.pct_change(5) * 100
        return df

    @classmethod
    def get_multiple_ohlcv_with_indicators(
        cls, tickers: list[str], count: int = 100, interval: str = "minutes/15"
    ) -> dict:
        results = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(10, max(1, len(tickers)))
        ) as executor:
            future_to_ticker = {
                executor.submit(
                    cls.get_ohlcv_with_indicators_new, ticker, count, interval
                ): ticker
                for ticker in tickers
            }
            for future in concurrent.futures.as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    df = future.result()
                    if not df.empty:
                        results[ticker] = df
                except Exception as exc:
                    logger.error(
                        f"[Market Data API] {ticker} generated an exception: {exc}"
                    )
        return results

    @classmethod
    def get_weights(cls, regime: str) -> tuple[float, float]:
        return {
            "bullish": (0.75, 0.25),  # 추세 추종
            "bearish": (0.35, 0.65),  # 방어 + 유동성 중심
            "ranging": (0.45, 0.55),  # mean-reversion 대비
            "volatile": (0.30, 0.70),  # 노이즈 → 거래대금 중요
            "panic": (0.20, 0.80),  # 생존 모드 (유동성 최우선)
        }.get(regime, (0.5, 0.5))

    @classmethod
    def get_dynamic_target_coins(cls, top_n: int = 20) -> list:
        """
        [Dynamic Selection]
        24시간 변화율과 거래대금을 z-score 정규화하여 상위 N개 코인을 반환합니다.
        /ticker API 한 번으로 모든 데이터를 확보합니다 (추가 API 호출 없음).
        """
        headers = {"accept": "application/json"}
        logger.info(f"[Market Data API] 동적 대상 코인 Top {top_n} 검색 시작...")

        try:
            # 1. KRW 마켓 중 경고 없는 코인만 필터링
            market_url = f"{cls.BASE_URL}/market/all?isDetails=true"
            markets = requests.get(market_url, headers=headers).json()
            krw_markets = [
                m["market"]
                for m in markets
                if m["market"].startswith("KRW-")
                and m["market_event"]["warning"] == False
            ]

            # 2. /ticker 한 번 호출로 모든 데이터 확보
            ticker_url = f"{cls.BASE_URL}/ticker?markets={','.join(krw_markets)}"
            tickers = requests.get(ticker_url, headers=headers).json()

            # 3. 거래대금 필터 + 스코어링 데이터 추출
            stats = []
            for t in tickers:
                acc_trade = t.get("acc_trade_price_24h", 0)
                if acc_trade < 5_000_000_000:  # 5,000백만 미만 제외
                    continue

                change_rate = np.tanh(t.get("signed_change_rate", 0) * 3)  # → %
                volume_score = np.log(acc_trade) * abs(
                    change_rate
                )  # log 스케일 거래대금

                stats.append(
                    {
                        "market": t["market"],
                        "change_rate": change_rate,
                        "volume_score": volume_score,
                    }
                )

            if not stats:
                return ["KRW-BTC", "KRW-ETH"]  # Fallback

            # 4. z-score 정규화 후 가중합
            cr = np.array([s["change_rate"] for s in stats])
            vs = np.array([s["volume_score"] for s in stats])

            cr_norm = (cr - cr.mean()) / (cr.std() + 1e-6)
            vs_norm = (vs - vs.mean()) / (vs.std() + 1e-6)

            regime = cls.market_regime()
            w_cr, w_vs = cls.get_weights(regime)

            for i, s in enumerate(stats):
                s["score"] = w_cr * cr_norm[i] + w_vs * vs_norm[i]

            # 스코어 높은 순으로 정렬 → 순서 보존 list
            stats.sort(key=lambda x: x["score"], reverse=True)

            # 상위 N개 추출 (순서 보존) + BTC/ETH 보장
            if regime in ["panic", "volatile"]:
                top_n = max(5, top_n // 2)

            top_coins = [s["market"] for s in stats[:top_n]]
            for must_have in ["KRW-BTC", "KRW-ETH"]:
                if must_have not in top_coins:
                    top_coins.append(must_have)

            logger.info(f"[Market Data API] 동적 대상 코인 선정 완료: {top_coins}")
            return top_coins

        except Exception as e:
            logger.error(f"[Market Data Error] 동적 코인 선정 실패, 기본값 반환: {e}")
            return ["KRW-BTC", "KRW-ETH"]

    @classmethod
    def get_current_prices_simple(cls, tickers: list[str]) -> dict:
        """
        /v1/ticker API를 사용하여 여러 종목의 현재가를 빠르게 조회합니다.
        """
        if not tickers:
            return {}
        
        headers = {"accept": "application/json"}
        url = f"{cls.BASE_URL}/ticker?markets={','.join(tickers)}"
        
        try:
            r = requests.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
            return {item["market"]: float(item["trade_price"]) for item in data}
        except Exception as e:
            logger.error(f"[Market Data Error] 현재가 조회 실패: {e}")
            return {}


if __name__ == "__main__":
    # 테스트 로직
    logger.info("Testing dynamic coin selection...")
    res_coins = UpbitMarketData.get_dynamic_target_coins(20)
    logger.info(res_coins)
