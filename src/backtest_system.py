import os
import sys
import time
import logging
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import requests
import warnings
import argparse
import json

warnings.filterwarnings("ignore")

# Add root directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.manager import ManagerAgent
from src.core.portfolio_manager import PortfolioManager
from src.data.market_data import UpbitMarketData
from src.utils.logger import logger, setup_logger


def reconfigure_logger_for_backtest():
    """백테스트를 위해 로그 설정을 초기화하고 backtest.log로 격리합니다."""
    # 기존 핸들러 제거
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # 새로운 포맷터
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. File Handler (Isolated for Backtest)
    log_file = "logs/backtest.log"
    os.makedirs("logs", exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 텔레그램 핸들러는 추가하지 않음 (격리 완료)
    logger.info("========== Backtest Logger Initialized (Isolated) ==========")


# 백테스트 시작 전 로거 재설정
reconfigure_logger_for_backtest()


class MockBroker:
    """명령을 서버로 보내지 않고 가상으로 성공 체결을 에뮬레이션하는 브로커"""

    def __init__(self, pm=None):
        self.pending_orders = {}
        self.uuid_counter = 1
        self.pm = pm

    def is_configured(self):
        return True

    def get_balances(self):
        # ManagerAgent 초기화가 실패하지 않도록 더미 잔고 제공
        balances = [{"currency": "KRW", "balance": "10000000", "avg_buy_price": "0"}]
        if self.pm:
            holdings = self.pm.get_holdings("crypto_manager")
            for ticker, data in holdings.items():
                cur = ticker.split("-")[1] if "-" in ticker else ticker
                balances.append(
                    {
                        "currency": cur,
                        "balance": str(data["volume"]),
                        "avg_buy_price": str(data["avg_price"]),
                    }
                )
        return balances

    def get_orderbook(self, ticker):
        # 호가창 필터 무조건 통과 (충분한 매도/매수 잔량 모방)
        return [{"total_ask_size": 10000, "total_bid_size": 10000}]

    def place_order(
        self, ticker, side, price=None, volume=None, ord_type=None, current_price=None
    ):
        if current_price is None:
            logger.error(f"[MockBroker] current_price 누락 ({ticker})")
            return {"error": "missing current price"}

        uuid = f"mock_{self.uuid_counter}"
        self.uuid_counter += 1

        # 즉시 체결(done) 상태로 저장
        if side == "bid":
            executed_vol = float(price) / current_price
            executed_funds = float(price)
        else:  # ask
            executed_vol = float(volume)
            executed_funds = executed_vol * current_price

        self.pending_orders[uuid] = {
            "uuid": uuid,
            "state": "done",
            "executed_volume": str(executed_vol),
            "trades": [{"funds": str(executed_funds)}],
            "paid_fee": "0",
        }
        return {"uuid": uuid}

    def get_order(self, uuid):
        return self.pending_orders.get(uuid, {"error": "not found"})


class MockNotifier:
    """Telegram 메시지를 콘솔로 출력하는 가짜 알림 객체"""

    def __init__(self):
        self._buffer = []
        self._is_buffering = False

    def start_buffering(self):
        self._is_buffering = True

    def flush_buffer(self):
        # 백테스트 속도를 위해 출력 생략
        self._buffer.clear()
        self._is_buffering = False

    def send_message(self, message: str, parse_mode="markdown"):
        # 백테스트 속도를 위해 출력 생략
        pass


def fetch_and_prepare_historical_data(
    ticker: str, days: int, interval: str, end_time: datetime = None
) -> pd.DataFrame:
    """Upbit API에서 N일치 데이터를 묶음 스크롤링하여 가져오고, 지표까지 한 번에 붙인 데이터프레임을 반환합니다."""
    # SQLite 캐시 처리
    cache_db = "data/market_data_cache.db"
    conn = sqlite3.connect(cache_db)

    # 캐시 테이블 생성 (없을 경우)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv (
            ticker TEXT,
            interval TEXT,
            time TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (ticker, interval, time)
        )
    """
    )

    # 60m이면 하루에 24개, 15m이면 하루에 96개
    candles_per_day = 24 if "60" in interval else 96
    total_candles = days * candles_per_day

    # 1. DB에서 먼저 확인
    query = f"SELECT * FROM ohlcv WHERE ticker='{ticker}' AND interval='{interval}' ORDER BY time DESC LIMIT {total_candles + 100}"
    df_cached = pd.read_sql(query, conn)

    if len(df_cached) >= total_candles:
        logger.info(f"[{ticker}] 캐시된 데이터 사용 ({len(df_cached)} rows)")
        df = df_cached
    else:
        logger.info(
            f"[{ticker}] API에서 데이터 수집 중... (필요: {total_candles}, 캐시: {len(df_cached)})"
        )

        if end_time is None:
            end_time = datetime.now().astimezone()
        frames = []
        candles_fetched = 0

        while candles_fetched < total_candles:
            url = f"https://api.upbit.com/v1/candles/{interval}"
            params = {
                "market": ticker,
                "count": 200,
                "to": end_time.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            }
            headers = {"accept": "application/json"}

            res = requests.get(url, headers=headers, params=params)
            if res.status_code == 429:
                time.sleep(1.0)
                continue
            if res.status_code != 200:
                break

            data = res.json()
            if not data:
                break

            df_chunk = pd.DataFrame(data)
            frames.append(df_chunk)
            candles_fetched += len(data)

            last_candle_time = pd.to_datetime(data[-1]["candle_date_time_utc"])
            end_time = last_candle_time
            time.sleep(0.1)  # Rate limiting

        if not frames:
            conn.close()
            return pd.DataFrame()

        # 데이터 정리
        df_new = pd.concat(frames, ignore_index=True)
        df_new = df_new.rename(
            columns={
                "candle_date_time_utc": "time",
                "opening_price": "open",
                "high_price": "high",
                "low_price": "low",
                "trade_price": "close",
                "candle_acc_trade_volume": "volume",
            }
        )
        df_new = df_new[["time", "open", "high", "low", "close", "volume"]]
        df_new["time"] = pd.to_datetime(df_new["time"]).dt.strftime("%Y-%m-%dT%H:%M:%S")

        # DB에 저장 (중복 무시를 위해 temporary table 사용)
        df_new.to_sql("temp_ohlcv", conn, if_exists="replace", index=False)
        conn.execute(
            f"INSERT OR IGNORE INTO ohlcv (ticker, interval, time, open, high, low, close, volume) SELECT '{ticker}', '{interval}', time, open, high, low, close, volume FROM temp_ohlcv"
        )
        conn.commit()
        conn.execute("DROP TABLE temp_ohlcv")

        df = pd.read_sql(query, conn)

    conn.close()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    # 요청한 기간(days)만큼만 자르지 않고, 지표 계산을 위해 전체 다 사용하되
    # 최소 candles_fetched 만큼은 확보되었는지 확인
    if len(df) < 50:  # 최소 50캔들은 있어야 지표 계산 가능
        return pd.DataFrame()

    # 지표 계산용 임시 메서드 호출
    # UpbitMarketData 구조상 내부 메서드를 차용하기 위해 df를 패치
    try:
        # Mock classmethod overriding or static indicator generator
        import talib

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        df["high_10"] = high.rolling(10).max()
        df["low_10"] = low.rolling(10).min()
        df["high_20"] = high.rolling(20).max()
        df["low_20"] = low.rolling(20).min()
        df["ma_9"] = close.rolling(9).mean()
        df["ma_20"] = close.rolling(20).mean()
        df["ma_50"] = close.rolling(50).mean()
        df["ma_60"] = close.rolling(60).mean()
        df["volume_ma20"] = volume.rolling(20).mean()
        df["rsi_14"] = talib.RSI(close, timeperiod=14)
        df["bb_upper"], df["bb_mid"], df["bb_lower"] = talib.BBANDS(
            close, timeperiod=20, nbdevup=2, nbdevdn=2
        )
        # Double Bollinger Bands (1.0 SD)
        df["bb_upper1"], _, df["bb_lower1"] = talib.BBANDS(
            close, timeperiod=20, nbdevup=1, nbdevdn=1
        )
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (df["bb_mid"] + 1e-8)
        df["bb_position"] = (close - df["bb_lower"]) / (
            df["bb_upper"] - df["bb_lower"] + 1e-8
        )
        df["atr_14"] = talib.ATR(high, low, close, timeperiod=14)
        df["adx_14"] = talib.ADX(high, low, close, timeperiod=14)
        df["plus_di_14"] = talib.PLUS_DI(high, low, close, timeperiod=14)
        df["minus_di_14"] = talib.MINUS_DI(high, low, close, timeperiod=14)

        tp = (high + low + close) / 3
        pv = volume * tp
        window = 96 if "15" in interval else 24
        df["vwap"] = pv.rolling(window).sum() / (volume.rolling(window).sum() + 1e-8)
        df["ema_20"] = talib.EMA(close, timeperiod=20)
        df["ema_50"] = talib.EMA(close, timeperiod=50)
        df["ema_200"] = talib.EMA(close, timeperiod=200)
        df["change_5"] = df["close"].pct_change(5) * 100

    except Exception as e:
        logger.error(f"Failed to calculate indicators for {ticker}: {e}")
        return pd.DataFrame()

    return df


def backtest_system(days: int = 5, update: bool = False, force_strategy: str = None):
    logger.info(
        f"========== System execution_trading_cycle {days} Days Backtest Start {' (Strategy: ' + force_strategy + ')' if force_strategy else ''} =========="
    )

    # 1. 사이드이펙트 격리된 포트폴리오 매니저 초기화
    import os

    temp_db_path = "data/backtest_portfolio.db"
    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)

    pm = PortfolioManager(total_capital=1000000, db_path=temp_db_path)
    # 실환경의 manager/portfolio.md 파일 변조 방지를 위해 export 메서드 묵음 처리
    pm.export_portfolio_report = lambda *args, **kwargs: None
    pm.allocate("crypto_manager", 1000000)

    manager = ManagerAgent("crypto_manager", pm)
    
    # 전략 강제 고정 (개별 전략 분석용)
    if force_strategy:
        logger.info(f"[Backtest] 전략 고정: {force_strategy}")
        # 모든 장세에 대해 해당 전략만 활성화
        new_map = {}
        regimes = ["recovery", "weakbullish", "bullish", "earlybreakout", "ranging", "volatile", "neutral"]
        for r in regimes:
            new_map[r] = [force_strategy]
        manager.STRATEGY_MAP = new_map

    # 핵심 통신·결제 모듈 Mocking
    manager.broker = MockBroker(pm)
    manager.notifier = MockNotifier()
    manager.execution_manager.broker = manager.broker
    manager.execution_manager.notifier = manager.notifier

    value_history = []

    # 2. 타겟 코인 및 시간 정보 로드/업데이트
    config_path = "data/backtest_target.json"
    backtest_config = {}
    if os.path.exists(config_path) and not update:
        with open(config_path, "r") as f:
            backtest_config = json.load(f)
            logger.info(f"기존 백테스트 설정 로드: {config_path}")

    # 티커 결정
    if "tickers" in backtest_config and not update:
        tickers = backtest_config["tickers"]
    else:
        tickers = UpbitMarketData.get_dynamic_target_coins(100)
        backtest_config["tickers"] = tickers

    # 기준 시간(end_time) 결정
    if "end_time" in backtest_config and not update:
        end_time_str = backtest_config["end_time"]
        fixed_end_time = datetime.fromisoformat(end_time_str)
        logger.info(f"고정된 기준 시간 사용: {end_time_str}")
    else:
        fixed_end_time = datetime.now().astimezone()
        backtest_config["end_time"] = fixed_end_time.isoformat()
        logger.info(f"새로운 기준 시간 설정: {backtest_config['end_time']}")

    # 설정 저장
    if update or not os.path.exists(config_path):
        with open(config_path, "w") as f:
            json.dump(backtest_config, f, indent=4)
            logger.info(f"백테스트 설정 저장 완료: {config_path}")

    logger.info(f"선정된 타겟 코인 ({len(tickers)}개): {tickers}")

    setup_full_data = {}
    entry_full_data = {}

    for ticker in tickers:
        setup_df = fetch_and_prepare_historical_data(
            ticker, days=days, interval="minutes/60", end_time=fixed_end_time
        )
        entry_df = fetch_and_prepare_historical_data(
            ticker, days=days, interval="minutes/15", end_time=fixed_end_time
        )
        if not setup_df.empty and not entry_df.empty:
            setup_full_data[ticker] = setup_df
            entry_full_data[ticker] = entry_df

    if "KRW-BTC" not in setup_full_data:
        logger.error(
            "KRW-BTC 데이터 수집 실패로 마켓 Regime 계산이 불가하여 종료합니다."
        )
        return None

    # 3. 공통 타임라인 생성
    # BTC 15m 캔들의 Time 인덱스를 시계열로 삼음 (이미 지표 계산때문에 초반 100개 캔들은 결측치 소거)
    btc_entry = (
        entry_full_data["KRW-BTC"].dropna(
            subset=["adx_14", "ema_50", "ma_60", "bb_upper"]
        )
        if "ma_60" in entry_full_data["KRW-BTC"]
        else entry_full_data["KRW-BTC"].dropna()
    )
    timeline = btc_entry["time"].tolist()

    logger.info(f"가상 타임라인 생성: {len(timeline)} 사이클 반복 예정 (15분 간격)")

    # 4. 본격적인 사이클 순회 (Live Simulation)
    # 로깅 레벨 일시 조정 (속도 향상)
    original_log_level = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)

    # 미리 데이터를 타임스탬프 기반으로 필터링하기 위한 최적화
    # Dict of items: {ticker: (df, time_col_values)}
    memo_setup = {t: (df, df["time"].values) for t, df in setup_full_data.items()}
    memo_entry = {t: (df, df["time"].values) for t, df in entry_full_data.items()}

    for t_idx, current_time in enumerate(timeline):
        # T 시간 이하의 데이터로 자르기 (과거만 볼 수 있게)
        setup_slice = {}
        entry_slice = {}

        for ticker in memo_setup:
            df_s, times_s = memo_setup[ticker]
            df_e, times_e = memo_entry[ticker]

            s_mask = times_s <= current_time
            e_mask = times_e <= current_time

            # 최소 캔들 보장이 안되면 무시 (초반 지표 워밍업)
            if s_mask.sum() > 10 and e_mask.sum() > 50:
                setup_slice[ticker] = df_s[s_mask]
                entry_slice[ticker] = df_e[e_mask]

        if not setup_slice or not entry_slice:
            continue

        # 자체 Regime 판독 엔진 모방 (KRW-BTC의 setup_slice 기준)
        regime = "ranging"
        if "KRW-BTC" in setup_slice:
            btc_setup = setup_slice["KRW-BTC"]
            if len(btc_setup) >= 2:
                price = btc_setup.close.iloc[-1]
                prev_price = btc_setup.close.iloc[-2]
                atr = btc_setup.atr_14.iloc[-1]
                change = (price - prev_price) / prev_price
                volatility = atr / price
                adx = btc_setup.adx_14.iloc[-1]
                ema20 = btc_setup.ma_20.iloc[-1]
                ema50 = btc_setup.ma_50.iloc[-1]

                if change < -0.04:
                    regime = "panic"
                elif volatility > 0.035:
                    regime = "volatile"
                elif adx > 25:
                    regime = "bullish" if ema20 > ema50 else "bearish"

        # 실제 사이클 수행
        manager.execute_cycle(setup_slice, entry_slice, regime)

        # 자산 가치 기록 (MDD 계산용)
        current_total = pm.get_total_value("crypto_manager")
        value_history.append(current_total)

    # 로깅 레벨 복구
    if "original_log_level" in locals():
        logging.getLogger().setLevel(original_log_level)

    # 4.5 마지막 시점에 모든 보유 종목 매도 처리 (정확한 수익률/승률 계산을 위해)
    holdings = pm.get_holdings("crypto_manager")
    if holdings:
        logger.info(
            f"🏁 백테스트 종료 시점: 모든 보유 종목({len(holdings)}개) 강제 청산 중..."
        )
        for ticker, h in list(holdings.items()):
            if ticker in entry_full_data:
                final_price = float(entry_full_data[ticker].close.iloc[-1])
                from src.strategies.base import Signal, SignalType

                pm.record_sell("crypto_manager", ticker, h["volume"], final_price)

    def calculate_mdd(history):
        if not history:
            return 0.0
        df = pd.Series(history)
        peak = df.cummax()
        drawdown = (df - peak) / peak
        return drawdown.min() * 100

    # 최종 리포트 출력
    logger.info("========== 시스템 백테스트 완료 ==========")

    # Mock Broker에 잔고 요청 대신 PortfolioManager의 내부 인메모리 정보로 정산
    summary = pm.get_portfolio_summary("crypto_manager")
    summary["mdd"] = calculate_mdd(value_history)

    logger.info(f"초기 캐피탈: {summary['initial_capital']:,.0f} KRW")
    logger.info(
        f"최종 캐피탈: {summary['total_value']:,.0f} KRW (수익률 {summary['return_rate']:+.2f}%)"
    )
    logger.info(f"총 매매 횟수: {summary['total_trades']}")
    logger.info(f"승률: {summary['win_rate']:.1f}%")
    logger.info(f"Profit Factor: {summary['profit_factor']:.2f}")
    logger.info(f"손익비 (RR): {summary['risk_reward_ratio']:.2f}")
    logger.info(f"최대 낙폭 (MDD): {summary['mdd']:+.2f}%")

    return summary

    if summary["holdings"]:
        logger.info("\n[현재 가상 보유 종목]")
        for ticker, h in summary["holdings"].items():
            logger.info(
                f" - {ticker} | 수량: {h['volume']:.6f} | 평단: {h['avg_price']:,.2f} | 현재 평가비용: {h['total_cost']:,.0f}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes System Backtest")
    parser.add_argument(
        "--days", type=int, default=5, help="Number of days to backtest (default: 5)"
    )
    parser.add_argument(
        "--update", action="store_true", help="Update target coins and end time"
    )
    args = parser.parse_args()

    backtest_system(days=args.days, update=args.update)
