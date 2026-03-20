import time
import requests
import concurrent.futures
from src.utils.logger import logger
import pandas as pd
import talib


class UpbitMarketData:
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
    def btc_regime():
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

        ma20 = df.ma_20.iloc[-1]
        ma60 = df.ma_60.iloc[-1]

        rsi = df.rsi_14.iloc[-1]
        adx = df.adx_14.iloc[-1]

        high20 = df.high_20.iloc[-1]
        low20 = df.low_20.iloc[-1]

        ema_now = df.ema_20.iloc[-1]
        ema_prev = df.ema_20.iloc[-5]

        volatility = (high20 - low20) / price
        trend_strength = abs(ma20 - ma60) / ma60
        ema_slope = (ema_now - ema_prev) / ema_prev

        # print(
        #     f"Regime {ticker:<10} | ma20: {ma20:.3f},{ma60:.3f} | rsi(55↑,44↓): {rsi:.3f} | adx: {adx>18} | ema_slope: {ema_slope>0} | volatility: {volatility>0.05} | trend_strength: {trend_strength>0.02}"
        # )
        # Bullish
        # 1. Panic
        if rsi < 35 and ema_slope < -0.01 and volatility > 0.06 and adx > 20:
            return "panic"

        # 2. Volatile (먼저!)
        if volatility > 0.05 and trend_strength < 0.015:
            return "volatile"

        # 3. Bullish
        if ma20 > ma60 * 1.005 and rsi > 55 and ema_slope > 0 and adx > 20:
            return "bullish"

        # 4. Bearish
        if ma20 < ma60 * 0.995 and rsi < 45 and ema_slope < 0 and adx > 20:
            return "bearish"

        # 5. Ranging
        return "ranging"

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
        cls, ticker: str, count: int = 100, interval: str = "minutes/15"
    ):
        df = cls.get_ohlcv(ticker, count, interval)
        if df.empty:
            return df

        close = df["close"]
        high = df["high"]
        low = df["low"]

        # high / low
        df["high_20"] = high.rolling(20).max()
        df["low_20"] = low.rolling(20).min()

        # Moving averages
        df["ma_9"] = close.rolling(9).mean()
        df["ma_20"] = close.rolling(20).mean()
        df["ma_50"] = close.rolling(50).mean()
        df["ma_60"] = close.rolling(60).mean()
        df["volume_ma20"] = df["volume"].rolling(20).mean()

        ## RSI
        df["rsi_14"] = talib.RSI(close, timeperiod=14)

        ## Bollinger Bands
        df["bb_upper"], df["bb_mid"], df["bb_lower"] = talib.BBANDS(
            close, timeperiod=20, nbdevup=2, nbdevdn=2
        )
        df["bb_position"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])

        ## ATR
        df["atr_14"] = talib.ATR(high, low, close, timeperiod=14)

        ## ADX
        df["adx_14"] = talib.ADX(high, low, close, timeperiod=14)
        df["plus_di_14"] = talib.PLUS_DI(high, low, close, timeperiod=14)
        df["minus_di_14"] = talib.MINUS_DI(high, low, close, timeperiod=14)

        ## VWAP (Volume Weighted Average Price - Daily Cumulative)
        df["typical_price"] = (high + low + close) / 3.0
        df["date"] = df["time"].dt.date
        df["vwap"] = (df["volume"] * df["typical_price"]).groupby(
            df["date"]
        ).cumsum() / df["volume"].groupby(df["date"]).cumsum()

        ## EMA
        df["ema_20"] = talib.EMA(close, timeperiod=20)

        ## Change 5
        df["change_5"] = df["close"].pct_change(5) * 100
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
    def get_dynamic_target_coins(cls, top_n: int = 20) -> list:
        """
        [Dynamic Selection]
        주간 상승률 순위와 일 매수 체결강도 순위를 종합하여 상위 N개 코인을 반환합니다.
        가장 먼저 24시간 거래대금이 충분한(100억 원 이상) KRW 마켓 코인 약 15~20개를 필터링합니다.
        """
        headers = {"accept": "application/json"}
        logger.info(f"[Market Data API] 동적 대상 코인 Top {top_n} 검색 시작...")

        try:
            # 1. 모든 코인 가져와서 KRW 마켓만 필터링 후 24시간 거래대금 기준 1차 필터링
            market_url = f"{cls.BASE_URL}/market/all?isDetails=true"
            markets = requests.get(market_url, headers=headers).json()
            krw_markets = [
                m["market"]
                for m in markets
                if m["market"].startswith("KRW-")
                and m["market_event"]["warning"] == False
            ]

            ticker_url = f"{cls.BASE_URL}/ticker?markets={','.join(krw_markets)}"
            tickers = requests.get(ticker_url, headers=headers).json()
            # 거래대금 100억 이상인 탄탄한 종목만 1차 후보로 선정
            solid_markets = [
                t["market"]
                for t in tickers
                if t.get("acc_trade_price_24h", 0) >= 10000000000
            ]

            stats = []
            for market in solid_markets:
                try:
                    # 2. 주간 상승률 계산
                    week_url = f"{cls.BASE_URL}/candles/weeks?market={market}&count=1"
                    week_data = requests.get(week_url, headers=headers).json()
                    opening = week_data[0]["opening_price"]
                    trade = week_data[0]["trade_price"]
                    weekly_return = ((trade - opening) / opening) * 100

                    # 3. 매수 체결강도 계산 (최근 500개 틱 기준 근사치)
                    tick_url = f"{cls.BASE_URL}/trades/ticks?market={market}&count=500"
                    ticks = requests.get(tick_url, headers=headers).json()
                    buy_vol = sum(
                        t["trade_volume"] for t in ticks if t["ask_bid"] == "ASK"
                    )
                    sell_vol = sum(
                        t["trade_volume"] for t in ticks if t["ask_bid"] == "BID"
                    )
                    exec_strength = (
                        (buy_vol / sell_vol * 100) if sell_vol > 0 else 100.0
                    )

                    stats.append(
                        {
                            "market": market,
                            "weekly_return": weekly_return,
                            "exec_strength": exec_strength,
                        }
                    )
                    time.sleep(0.1)  # API Rate Limit 보호
                except Exception as inner_e:
                    # 일부 코인은 틱이 부족하거나 에러가 날 수 있으므로 무시하고 다음 진행
                    pass

            if not stats:
                return ["KRW-BTC", "KRW-ETH", "KRW-SOL"]  # Fallback

            # 4. 순위 매기기 로직
            # 주간 상승률 순위 (높을수록 좋음, 0이 1등)
            stats.sort(key=lambda x: x["weekly_return"], reverse=True)
            for i, s in enumerate(stats):
                s["rtn_rank"] = i

            # 체결 강도 순위 (높을수록 좋음, 0이 1등)
            stats.sort(key=lambda x: x["exec_strength"], reverse=True)
            for i, s in enumerate(stats):
                s["str_rank"] = i

            # 종합 순위 (두 순위의 합이 낮을수록 좋음)
            for s in stats:
                s["total_score"] = s["rtn_rank"] + s["str_rank"]

            stats.sort(key=lambda x: x["total_score"])

            # 상위 N개 마켓명 추출
            top_coins = {s["market"] for s in stats[:top_n]} | {
                "KRW-BTC",
                "KRW-ETH",
            }

            logger.info(f"[Market Data API] 동적 대상 코인 선정 완료: {top_coins}")
            return list(top_coins)

        except Exception as e:
            logger.error(f"[Market Data Error] 동적 코인 선정 실패, 기본값 반환: {e}")
            return ["KRW-BTC", "KRW-ETH", "KRW-SOL"]


if __name__ == "__main__":
    # 테스트 로직
    logger.info("Testing dynamic coin selection...")
    res_coins = UpbitMarketData.get_dynamic_target_coins(5)
    logger.info(res_coins)
