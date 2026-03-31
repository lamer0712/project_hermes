import os
import sys
import time
from datetime import datetime, timedelta
import pandas as pd
import requests

# Add root directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.strategies.opening_scalp import OpeningScalpStrategy
from src.data.market_data import UpbitMarketData
from src.utils.logger import logger
from src.core.portfolio_manager import PortfolioManager
from src.core.risk_manager import RiskManager
import tempfile
import os


def fetch_historical_ohlcv(ticker: str, days: int) -> pd.DataFrame:
    """
    Upbit API에서 마지막 `days`간의 5분봉 데이터를 수집합니다.
    """
    logger.info(f"[{ticker}] 과거 {days}일치 5분봉 데이터 수집 중...")

    end_time = datetime.now().astimezone()
    # KST 기준 자정으로 클립
    end_time = end_time.replace(hour=23, minute=59, second=59)

    frames = []
    total_candles = days * 24 * 60 // 5

    # 200캔들씩 끊어서 요청 (API 제한)
    candles_fetched = 0
    while candles_fetched < total_candles:
        url = "https://api.upbit.com/v1/candles/minutes/5"
        count = min(200, total_candles - candles_fetched)
        params = {
            "market": ticker,
            "count": count,
            "to": end_time.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        }
        headers = {"accept": "application/json"}

        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 429:
            time.sleep(1.0)
            continue

        if res.status_code != 200:
            logger.error(f"Failed to fetch data: {res.text}")
            break

        data = res.json()
        if not data:
            break

        df_chunk = pd.DataFrame(data)
        frames.append(df_chunk)

        candles_fetched += len(data)

        # 마지막 캔들 시간 기준으로 다음 `to` 시간 설정
        last_candle_time = pd.to_datetime(data[-1]["candle_date_time_utc"])
        # Upbit API 'to' format은 UTC 기준
        end_time = last_candle_time
        time.sleep(0.1)  # Rate limiting

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # 컬럼 정리
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
    df = df.sort_values("time").reset_index(drop=True)

    return df


def backtest_opening_scalp(days: int = 5):
    logger.info(f"========== OpeningScalp {days}일 백테스트 시작 ==========")

    strategy = OpeningScalpStrategy()

    # 타겟 코인 동적 로드
    tickers = UpbitMarketData.get_dynamic_target_coins(20)
    logger.info(f"테스트 대상: {tickers}")

    results = []

    for ticker in tickers:
        df = fetch_historical_ohlcv(ticker, days=days)
        if df.empty:
            continue

        # 일별로 분리해서 테스트
        start_date = df["time"].min().date()
        end_date = df["time"].max().date()

        current_date = start_date
        while current_date <= end_date:
            # 해당 일자의 00:30 ~ 01:30 UTC 구간 시뮬레이션 (09:30 ~ 10:30 KST)
            day_df = df[df["time"].dt.date == current_date]

            sim_df = day_df[
                ((day_df["time"].dt.hour == 0) & (day_df["time"].dt.minute >= 30))
                | ((day_df["time"].dt.hour == 1) & (day_df["time"].dt.minute <= 30))
            ]

            if sim_df.empty:
                current_date += timedelta(days=1)
                continue

            entry_signal = None
            entry_price = 0
            tp_price = 0
            sl_price = 0
            entry_idx = -1

            # 한 캔들씩 주입하면서 평가 (09:35부터)
            for idx in range(1, len(sim_df)):
                eval_slice = sim_df.iloc[: idx + 1]
                latest_time = eval_slice.iloc[-1]["time"]

                # 09:35 이후로만 평가 (최소 2개 캔들)
                if len(eval_slice) < 2:
                    continue

                signal = strategy.evaluate(ticker, None, eval_slice, portfolio_info={})

                if signal.type.value == "BUY":
                    entry_signal = signal
                    entry_price = float(eval_slice.iloc[-1]["close"])
                    tp_price = float(signal.custom_tp_price) if signal.custom_tp_price is not None else None
                    sl_price = float(signal.custom_sl_price) if signal.custom_sl_price is not None else None
                    entry_idx = idx

                    tp_str = f"{tp_price:,.2f}" if tp_price else "None"
                    sl_str = f"{sl_price:,.2f}" if sl_price else "None"
                    logger.info(
                        f"[BUY] {latest_time} | {ticker} | Entry: {entry_price:,.2f} | TP: {tp_str} | SL: {sl_str}"
                    )
                    break  # 해당 일자는 진입 완료

            # 진입했다면 해당 일자 이후 캔들에서 언제 팔리는지(TP/SL) 결과 확인
            if entry_signal:
                result = None
                exit_price = 0

                # Initialize portfolio for this trade
                db_path = os.path.join(
                    tempfile.gettempdir(), f"mock_{ticker}_{current_date}.db"
                )
                if os.path.exists(db_path):
                    os.remove(db_path)
                pm = PortfolioManager(total_capital=1000000, db_path=db_path)
                pm.allocate("crypto_manager", 1000000)

                # Mock a buy (10만원 어치)
                volume = 100000 / entry_price
                pm.record_buy(
                    "crypto_manager",
                    ticker,
                    volume,
                    entry_price,
                    paid_fee=0.0005,
                    strategy="OpeningScalp",
                )
                pm.update_holding_metadata(
                    "crypto_manager",
                    ticker,
                    custom_sl_price=sl_price,
                    custom_tp_price=tp_price,
                )
                rm = RiskManager(pm)

                future_df = day_df.iloc[
                    day_df.index.get_loc(sim_df.index[entry_idx]) + 1 :
                ]

                for _, row in future_df.iterrows():
                    op = float(row["open"])
                    hi = float(row["high"])
                    lo = float(row["low"])
                    cl = float(row["close"])

                    # 매우 보수적인 틱 평가 (고점을 찍은 후 저점으로 갔는지 역순 적용)
                    ticks = [op, lo, hi, cl] if cl > op else [op, hi, lo, cl]

                    clean_ticks = []
                    for t in ticks:
                        if not clean_ticks or t != clean_ticks[-1]:
                            clean_ticks.append(t)

                    for tick_price in clean_ticks:
                        risk_signal = rm.evaluate_risk(
                            "crypto_manager", ticker, tick_price
                        )
                        if risk_signal:
                            result = f"SELL ({risk_signal.reason})"
                            exit_price = tick_price
                            break
                    if result:
                        break

                if not result:
                    # 종가 청산 (당일 시간 끝날 때)
                    result = "HOLD (Closed at EOD)"
                    exit_price = (
                        float(future_df.iloc[-1]["close"])
                        if not future_df.empty
                        else entry_price
                    )

                pnl_pct = (exit_price - entry_price) / entry_price * 100
                logger.info(
                    f"  -> {result} | Exit: {exit_price:,.2f} | PnL: {pnl_pct:+.2f}%"
                )

                results.append(
                    {
                        "Date": current_date,
                        "Ticker": ticker,
                        "TickerRank": tickers.index(ticker),
                        "EntryTime": latest_time,
                        "Result": result,
                        "PnL(%)": pnl_pct,
                    }
                )

            current_date += timedelta(days=1)

    # -------- 일별 1종목 필터링 로직 (main.py와 동일한 조건) --------
    daily_trades = {}
    for r in results:
        d = r["Date"]
        if d not in daily_trades:
            daily_trades[d] = []
        daily_trades[d].append(r)
        
    filtered_results = []
    # 매일 가장 먼저 발생한 진입(EntryTime), 동시간대면 우선순위(TickerRank)가 높은 종목 1개만 선택
    for d, trades in sorted(daily_trades.items()):
        trades.sort(key=lambda x: (x["EntryTime"], x["TickerRank"]))
        filtered_results.append(trades[0])
        
    results = filtered_results
    # -----------------------------------------------------------

    # 결과 집계
    total_trades = len(results)
    if total_trades > 0:
        win_trades = sum(1 for r in results if r["PnL(%)"] > 0)
        loss_trades = total_trades - win_trades
        win_rate = (win_trades / total_trades) * 100
        avg_pnl = sum(r["PnL(%)"] for r in results) / total_trades

        logger.info("\n========== 백테스트 결과 요약 ==========")
        logger.info(f"Total Trades: {total_trades}")
        logger.info(
            f"Win/Loss: {win_trades} / {loss_trades} (Win Rate: {win_rate:.1f}%)"
        )
        logger.info(f"Average PnL per trade: {avg_pnl:+.2f}%")

        for r in results:
            print(
                f"{r['Date']} | {r['EntryTime'].strftime('%H:%M')} 진입 | {r['Ticker']:<10} \t {r['Result']:<50} \t {r['PnL(%)']:+.2f}%"
            )
    else:
        logger.info("\n========== 백테스트 결과 ==========")
        logger.info("거래 기회가 없었습니다.")


if __name__ == "__main__":
    backtest_opening_scalp(days=5)
