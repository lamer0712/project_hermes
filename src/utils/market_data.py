import time
import requests
from src.utils.logger import logger
import pandas as pd


class UpbitMarketData:
    """Upbit API를 통해 거시 데이터, 캔들 데이터 및 지표를 계산하는 클래스"""
    BASE_URL = "https://api.upbit.com/v1"
    
    # 실패한 마켓 저장 (최소 주기 동안 재요청 방지)
    _blacklisted_markets = set()

    @staticmethod
    def is_bullish(df: pd.DataFrame) -> bool:

        last = df.iloc[-1]

        trend = (
            (last.ma_20 > last.ma_60) or
            (last.rsi > 52)
        )

        market_ok = trend and (last.close > last.ma_120)

        return market_ok

    @classmethod
    def get_ohlcv(
        cls,
        ticker: str,
        count: int = 200,
        interval: str = "minutes/60"
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
                    time.sleep(2 ** attempt)
                    continue

                r.raise_for_status()
                data = r.json()

                if not data:
                    return pd.DataFrame()

                df = pd.DataFrame(data)

                df = df.rename(columns={
                    "candle_date_time_utc": "time",
                    "opening_price": "open",
                    "high_price": "high",
                    "low_price": "low",
                    "trade_price": "close",
                    "candle_acc_trade_volume": "volume"
                })

                df = df[["time", "open", "high", "low", "close", "volume"]]

                df["time"] = pd.to_datetime(df["time"])

                # 업비트는 최신순 → 시간순 정렬
                df = df.sort_values("time").reset_index(drop=True)

                # logger.info(f"[Market Data] {ticker} {interval} candles fetched ({len(df)})")

                return df

            except requests.exceptions.HTTPError as e:
                if r.status_code in [400, 404]:
                    cls._blacklisted_markets.add(ticker)
                    logger.info(f"[Market Data] unsupported market → blacklist: {ticker}")
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
    def get_ohlcv_with_indicators_new(cls, ticker: str, count: int = 100, interval: str = "minutes/15"):
        df = cls.get_ohlcv(ticker, count, interval)
        if df.empty:
            return df
        
        close = df["close"]

        # Moving averages
        df["ma_9"] = close.rolling(9).mean()
        df["ma_20"] = close.rolling(20).mean()
        df["ma_50"] = close.rolling(50).mean()
        df["ma_60"] = close.rolling(60).mean()
        df["ma_120"] = close.rolling(120).mean()

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        rs = gain.rolling(14).mean() / loss.rolling(14).mean()
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi_14"] = df["rsi"]

        close = df["close"]

        sd = close.rolling(20).std()

        df["bb_mid"] = df["ma_20"]
        df["bb_upper"] = df["ma_20"] + 2.0 * sd
        df["bb_lower"] = df["ma_20"] - 2.0 * sd
        df["bb_position"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
        
        df["volume_ma20"] = df["volume"].rolling(20).mean()

        return df
        
    @classmethod
    def get_ohlcv_with_indicators(cls, ticker: str, count: int = 100, interval: str = "minutes/15") -> dict:
        """
        [Pure Python Data Fetcher]
        pandas-ta 없이 파이썬 로직만으로 캔들 데이터를 불러와 실시간 지표(RSI, MA, Trend)를 계산합니다.
        """

        # 블랙리스트 체크: 이전에 404/400 응답을 받은 마켓은 즉시 스킵
        if ticker in cls._blacklisted_markets:
            return {}

        url = f"{cls.BASE_URL}/candles/{interval}"
        querystring = {"market": ticker, "count": count}
        headers = {"accept": "application/json"}

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=headers, params=querystring)
                if response.status_code == 429:
                    logger.info(f"[Market Data API] Rate limit hit (429) for {ticker}. Retrying in {2 ** attempt} seconds...")
                    time.sleep(2 ** attempt)
                    continue
                response.raise_for_status()
                data = response.json()
                
                if not data:
                    return {}

                # 과거부터 현재 순서로 정렬 (업비트는 기본 최신순 반환)
                data.reverse()
                closes = [item['trade_price'] for item in data]
                
                # MA 20 & MA 50
                ma_20 = sum(closes[-20:]) / len(closes[-20:]) if len(closes) >= 20 else closes[-1]
                ma_50 = sum(closes[-50:]) / len(closes[-50:]) if len(closes) >= 50 else closes[-1]

                # RSI 14
                rsi_14 = 50.0
                if len(closes) > 14:
                    gains, losses = [], []
                    for i in range(1, len(closes)):
                        change = closes[i] - closes[i-1]
                        if change > 0:
                            gains.append(change)
                            losses.append(0)
                        else:
                            gains.append(0)
                            losses.append(abs(change))
                    
                    # 매우 간단한 단순이동평균(SMA) 방식의 초기 RSI
                    avg_gain = sum(gains[-14:]) / 14
                    avg_loss = sum(losses[-14:]) / 14
                    
                    if avg_loss == 0:
                        rsi_14 = 100.0
                    elif avg_gain == 0:
                        rsi_14 = 0.0
                    else:
                        rs = avg_gain / avg_loss
                        rsi_14 = 100 - (100 / (1 + rs))

                current_price = closes[-1]
                
                trend = "ranging"
                if ma_20 > ma_50 * 1.01:
                    trend = "bullish"
                elif ma_20 < ma_50 * 0.99:
                    trend = "bearish"

                # 볼린저 밴드 (20기간, 2σ)
                bb_mid = ma_20
                bb_upper = 0.0
                bb_lower = 0.0
                if len(closes) >= 20:
                    recent_20 = closes[-20:]
                    mean_20 = sum(recent_20) / 20
                    variance = sum((x - mean_20) ** 2 for x in recent_20) / 20
                    std_dev = variance ** 0.5
                    bb_upper = mean_20 + 2 * std_dev
                    bb_lower = mean_20 - 2 * std_dev

                # N일 최고가/최저가 (20기간)
                high_20 = max(closes[-20:]) if len(closes) >= 20 else max(closes)
                low_20 = min(closes[-20:]) if len(closes) >= 20 else min(closes)

                # --- EMA 헬퍼 (지수이동평균) ---
                def _ema(values, period):
                    if len(values) < period:
                        return values[-1] if values else 0.0
                    k = 2 / (period + 1)
                    ema_val = sum(values[:period]) / period  # SMA로 시드
                    for v in values[period:]:
                        ema_val = v * k + ema_val * (1 - k)
                    return ema_val

                # EMA 5 & EMA 13 (스캘핑 전략용)
                ema_5 = _ema(closes, 5)
                ema_13 = _ema(closes, 13)

                # MACD (12, 26, 9)
                macd_line = 0.0
                macd_signal = 0.0
                macd_histogram = 0.0
                if len(closes) >= 26:
                    ema_12 = _ema(closes, 12)
                    ema_26 = _ema(closes, 26)
                    macd_line = ema_12 - ema_26
                    # MACD 시그널: 각 시점의 MACD를 구한 뒤 EMA 9 적용
                    macd_series = []
                    for i in range(26, len(closes) + 1):
                        sub = closes[:i]
                        macd_series.append(_ema(sub, 12) - _ema(sub, 26))
                    macd_signal = _ema(macd_series, 9) if len(macd_series) >= 9 else macd_line
                    macd_histogram = macd_line - macd_signal

                # Stochastic RSI (14기간 RSI를 0~100 정규화)
                stoch_rsi = 50.0
                if len(closes) > 28:  # RSI 14개 이상 필요
                    rsi_series = []
                    for i in range(15, len(closes) + 1):
                        sub = closes[:i]
                        g, l = [], []
                        for j in range(1, len(sub)):
                            ch = sub[j] - sub[j-1]
                            g.append(max(ch, 0))
                            l.append(abs(min(ch, 0)))
                        ag = sum(g[-14:]) / 14
                        al = sum(l[-14:]) / 14
                        if al == 0:
                            rsi_series.append(100.0)
                        elif ag == 0:
                            rsi_series.append(0.0)
                        else:
                            rsi_series.append(100 - (100 / (1 + ag / al)))
                    if len(rsi_series) >= 14:
                        recent_rsi = rsi_series[-14:]
                        rsi_min = min(recent_rsi)
                        rsi_max = max(recent_rsi)
                        if rsi_max - rsi_min > 0:
                            stoch_rsi = ((rsi_series[-1] - rsi_min) / (rsi_max - rsi_min)) * 100
                        else:
                            stoch_rsi = 50.0

                logger.info(f"[Market Data API] Fetching Real Data for {ticker} (Interval: {interval}, Price: {current_price})")
                
                return {
                    "ticker": ticker,
                    "current_price": current_price,
                    "rsi_14": round(rsi_14, 2),
                    "ma_20": round(ma_20, 2),
                    "ma_50": round(ma_50, 2),
                    "trend": trend,
                    "bb_upper": round(bb_upper, 2),
                    "bb_lower": round(bb_lower, 2),
                    "bb_mid": round(bb_mid, 2),
                    "high_20": round(high_20, 2),
                    "low_20": round(low_20, 2),
                    "ema_5": round(ema_5, 2),
                    "ema_13": round(ema_13, 2),
                    "macd_line": round(macd_line, 4),
                    "macd_signal": round(macd_signal, 4),
                    "macd_histogram": round(macd_histogram, 4),
                    "stoch_rsi": round(stoch_rsi, 2),
                }

            except requests.exceptions.HTTPError as e:
                # 400 (Bad Request), 404 (Not Found) 등의 에러는 재시도해도 의미 없음
                if response.status_code in [400, 404]:
                    cls._blacklisted_markets.add(ticker)
                    logger.info(f"[Market Data API] 지원하지 않는 마켓 → 블랙리스트 등록: {ticker} (총 {len(cls._blacklisted_markets)}개)")
                    return {}
                
                logger.error(f"[Market Data Error] 캔들/지표 조회 중 오류 발생 (시도 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    return {}
            except Exception as e:
                logger.error(f"[Market Data Error] 캔들/지표 조회 중 오류 발생 (시도 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    return {}
        return {}

    @classmethod
    def get_dynamic_target_coins(cls, top_n: int = 10) -> list:
        """
        [Dynamic Selection]
        주간 상승률 순위와 일 매수 체결강도 순위를 종합하여 상위 N개 코인을 반환합니다.
        가장 먼저 24시간 거래대금이 충분한(300억 원 이상) KRW 마켓 코인 약 15~20개를 필터링합니다.
        """
        headers = {"accept": "application/json"}
        logger.info(f"[Market Data API] 동적 대상 코인 Top {top_n} 검색 시작...")
        
        try:
            # 1. 모든 코인 가져와서 KRW 마켓만 필터링 후 24시간 거래대금 기준 1차 필터링
            market_url = f"{cls.BASE_URL}/market/all?isDetails=true"
            markets = requests.get(market_url, headers=headers).json()
            krw_markets = [m['market'] for m in markets if m['market'].startswith("KRW-") and m['market_event']["warning"] == False]
            
            ticker_url = f"{cls.BASE_URL}/ticker?markets={','.join(krw_markets)}"
            tickers = requests.get(ticker_url, headers=headers).json()
            # 거래대금 100억 이상인 탄탄한 종목만 1차 후보로 선정
            solid_markets = [t['market'] for t in tickers if t.get('acc_trade_price_24h', 0) >= 10000000000]
            
            stats = []
            for market in solid_markets:
                try:
                    # 2. 주간 상승률 계산
                    week_url = f"{cls.BASE_URL}/candles/weeks?market={market}&count=1"
                    week_data = requests.get(week_url, headers=headers).json()
                    opening = week_data[0]['opening_price']
                    trade = week_data[0]['trade_price']
                    weekly_return = ((trade - opening) / opening) * 100
                    
                    # 3. 매수 체결강도 계산 (최근 500개 틱 기준 근사치)
                    tick_url = f"{cls.BASE_URL}/trades/ticks?market={market}&count=500"
                    ticks = requests.get(tick_url, headers=headers).json()
                    buy_vol = sum(t['trade_volume'] for t in ticks if t['ask_bid'] == 'ASK')
                    sell_vol = sum(t['trade_volume'] for t in ticks if t['ask_bid'] == 'BID')
                    exec_strength = (buy_vol / sell_vol * 100) if sell_vol > 0 else 100.0
                    
                    stats.append({
                        "market": market,
                        "weekly_return": weekly_return,
                        "exec_strength": exec_strength
                    })
                    time.sleep(0.1) # API Rate Limit 보호
                except Exception as inner_e:
                    # 일부 코인은 틱이 부족하거나 에러가 날 수 있으므로 무시하고 다음 진행
                    pass
            
            if not stats:
                return ["KRW-BTC", "KRW-ETH", "KRW-SOL"] # Fallback
                
            # 4. 순위 매기기 로직
            # 주간 상승률 순위 (높을수록 좋음, 0이 1등)
            stats.sort(key=lambda x: x['weekly_return'], reverse=True)
            for i, s in enumerate(stats):
                s['rtn_rank'] = i
                
            # 체결 강도 순위 (높을수록 좋음, 0이 1등)
            stats.sort(key=lambda x: x['exec_strength'], reverse=True)
            for i, s in enumerate(stats):
                s['str_rank'] = i
                
            # 종합 순위 (두 순위의 합이 낮을수록 좋음)
            for s in stats:
                s['total_score'] = s['rtn_rank'] + s['str_rank']
                
            stats.sort(key=lambda x: x['total_score'])
            
            # 상위 N개 마켓명 추출
            top_coins =  {s['market'] for s in stats[:top_n]} | {"KRW-BTC", "KRW-ETH"}

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
