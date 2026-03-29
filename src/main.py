import re
import os
import sys
import json
import math
import traceback
import glob
from dotenv import load_dotenv

load_dotenv(override=True)
import time
import schedule
import concurrent.futures
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from src.core.manager import ManagerAgent
from src.broker.broker_api import UpbitBroker
from src.data.market_data import UpbitMarketData
from src.core.portfolio_manager import PortfolioManager
from src.communication.telegram_notifier import TelegramNotifier
from src.communication.telegram_listener import run_telegram_listener
from src.data.upbit_websocket import UpbitWebSocketClient

from src.communication.command_handler import CommandQueueHandler
from src.utils.logger import logger

# 코인 대상 티커 설정 (동적으로 업데이트됨)
TARGET_COINS = []  # 기본값

AGENT_NAME = "crypto_manager"


def get_upbit_krw_balance() -> float:
    """Upbit 계좌의 KRW 잔고를 조회합니다."""
    broker = UpbitBroker()
    if not broker.is_configured():
        logger.warning("[System] ⚠️ Upbit API 키 미설정, 기본 자본 1,000,000 KRW 사용")
        return 1000000.0

    balances = broker.get_balances()
    for b in balances:
        if b.get("currency") == "KRW":
            krw = float(b.get("balance", "0"))
            logger.info(f"[System] 💰 Upbit KRW 잔고 조회: {krw:,.0f} KRW")
            return krw

    logger.error("[System] ⚠️ KRW 잔고 조회 실패, 기본 자본 1,000,000 KRW 사용")
    return 1000000.0


def update_target_coins():
    global TARGET_COINS
    logger.info("--- [System] 동적 대상 코인 선정 프로세스 시작 ---")
    new_coins = UpbitMarketData.get_dynamic_target_coins(top_n=20)
    if new_coins:
        TARGET_COINS = new_coins
    logger.info(f"--- [System] 현재 타겟 코인: {TARGET_COINS} ---")


def execute_trading_cycle(manager: ManagerAgent):
    logger.info("--- [System] Running Trading Cycle (Every 3 min) ---")

    # 0. Get Advanced Market Data (OHLCV + Indicators) for each Target Coin
    setup_market_data = UpbitMarketData.get_multiple_ohlcv_with_indicators(
        TARGET_COINS, count=200, interval="minutes/60"
    )
    entry_market_data = UpbitMarketData.get_multiple_ohlcv_with_indicators(
        TARGET_COINS, count=200, interval="minutes/15"
    )

    # 교집합만 유지 (둘 다 성공한 경우만)
    valid_tickers = set(setup_market_data.keys()).intersection(
        set(entry_market_data.keys())
    )
    setup_market_data = {k: setup_market_data[k] for k in valid_tickers}
    entry_market_data = {k: entry_market_data[k] for k in valid_tickers}

    if not setup_market_data:
        logger.error("[System] Failed to fetch market data from Upbit. Skipping cycle.")
        return

    market_regime = UpbitMarketData.market_regime()
    logger.info(f"[Market Indicators] Market regime: {market_regime}")

    # 2. Manager evalutes market
    manager.execute_cycle(setup_market_data, entry_market_data, market_regime)

    logger.info("--- [System] High Frequency Loop Completed ---")


def execute_scalp_cycle(manager):
    kst = ZoneInfo("Asia/Seoul")
    now = datetime.now(kst)

    # KST 09:35 ~ 10:30 사이클 검사
    if not (
        (now.hour == 9 and now.minute >= 35) or (now.hour == 10 and now.minute <= 30)
    ):
        return

    if (
        manager.portfolio_manager
        and manager.portfolio_manager.has_traded_strategy_today(
            manager.name, "OpeningScalp"
        )
    ):
        # logger.info("[System] 금일 스캘핑 전략(OpeningScalp) 매수 1회 달성 완료. 금일 스캘핑 루프 펑가 종료.")
        return

    logger.info(
        f"--- [System] Running 5-Minute Scalp Cycle at {now.strftime('%H:%M')} ---"
    )

    scalp_market_data = UpbitMarketData.get_multiple_ohlcv_with_indicators(
        TARGET_COINS, count=100, interval="minutes/5"
    )

    if not scalp_market_data:
        logger.error("[System] Failed to fetch 5m market data. Skipping scalp.")
        return

    # 1. 컨텍스트 구성
    ctx = manager._build_cycle_context(scalp_market_data, "volatile")

    # 2. OpeningScalp 전략 직접 펑가 (매도X)
    scalp_strategy = manager.strategy_manager.get_strategy("OpeningScalp")
    if not scalp_strategy:
        logger.error("[System] OpeningScalp strategy not found. Skipping scalp.")
        return

    for ticker, df in scalp_market_data.items():
        is_held = ticker in ctx.holdings and ctx.holdings[ticker]["volume"] > 0
        if is_held:
            continue

        # Evaluate
        signal = scalp_strategy.evaluate(ticker, None, df, ctx.portfolio_info)

        # BUY 시그널 후보 처리
        if signal.type.value == "BUY":
            if (
                not ctx.buy_filter_passed
                or ctx.available_cash < manager.MIN_ORDER_AMOUNT
            ):
                continue

            ctx.buy_candidates.append((signal, scalp_strategy, df))

    # 3. 최적 매수 1건 실행
    manager._select_and_execute_buy(ctx)
    manager._finalize_cycle(ctx, "scalp")
    logger.info("--- [System] 5-Minute Scalp Loop Completed ---")


def execute_daily_sync(pm, manager, notifier):
    logger.info("--- Running Daily Sync Loop (Midnight) ---")
    logger.info("[Manager] Performing daily audit...")
    update_target_coins()
    try:
        sync_result = pm.synchronize_balances(manager.name)
        notifier.send_message(sync_result)

        # 매일 자정에 7일 지난 과거 DB 거래이력 찌꺼기 삭제 수행
        pm.clean_old_trade_history(days=7)
    except Exception as e:
        logger.error(f"Daily sync error: {e}")


def main():
    logger.info("=" * 60)
    logger.info("🏢 Project Hermes start")
    logger.info("=" * 60)

    # 0. 텔레그램 알림 초기화
    notifier = TelegramNotifier()
    logger.info(f"📱 텔레그램 알림 초기화 완료")

    # 1. 포트폴리오 매니저 초기화
    update_target_coins()
    total_capital = get_upbit_krw_balance()
    pm = PortfolioManager(total_capital=total_capital)

    # 기존 상태가 없으면 초기 배분
    if not pm.portfolios or AGENT_NAME not in pm.portfolios:
        logger.info(
            f"💰 기존 포트폴리오 상태가 없거나 {AGENT_NAME}가 없어 초기화합니다."
        )
        pm.allocate(AGENT_NAME, total_capital)
    else:
        logger.info(f"💰 기존 포트폴리오 상태 복원 완료")
        for name in pm.portfolios:
            s = pm.get_portfolio_summary(name)
            logger.info(
                f"  - {name}: 현금 {s['cash']:,.0f} KRW, 수익률 {s['return_rate']:+.2f}%"
            )

    # 2. 투자 매니저 및 전략 객체 초기화
    manager = ManagerAgent(AGENT_NAME, portfolio_manager=pm)

    # 3. 실시간 리스크 훅 처리용 웹소켓 클라이언트 시작
    # 타겟 코인 + 현재 보유 코인 통합
    broker = UpbitBroker()
    balances = broker.get_balances()
    held_coins = [
        f"KRW-{b.get('currency')}"
        for b in balances
        if b.get("currency") != "KRW" and float(b.get("balance", "0")) > 0
    ]
    ws_tickers = list(set(TARGET_COINS + held_coins))

    ws_client = UpbitWebSocketClient(
        tickers=ws_tickers, callbacks=[manager.handle_realtime_tick]
    )
    ws_client.start()

    def stop_systems():
        logger.info("🛑 시스템 종료 중...")
        ws_client.stop()
        pm.save_state()
        sys.exit(0)

    # 스케줄러 등록
    pm.export_portfolio_report(AGENT_NAME)

    # 5. 스레드 풀 초기화
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    # Schedule Jobs
    logger.info(f"⏰ 스케줄러 시작")
    # 정규 15분 스케줄 (기존)
    for m in [0, 15, 30, 45]:
        schedule.every().hour.at(f"{m:02d}:30").do(
            execute_trading_cycle, manager=manager
        )

    # 오프닝 스캘핑 5분 스케줄 (09:35 ~ 10:30 구간에만 동작)
    for m in range(0, 60, 5):
        schedule.every().hour.at(f"{m:02d}:30").do(execute_scalp_cycle, manager=manager)

    schedule.every().day.at("00:00").do(
        execute_daily_sync, pm=pm, manager=manager, notifier=notifier
    )

    # 텔레그램 명령 큐 처리 (2초마다)
    command_handler = CommandQueueHandler(pm, manager, notifier)
    schedule.every(2).seconds.do(command_handler.process)

    logger.info("\n" + "=" * 60)
    logger.info("⏰ Scheduler started. Press Ctrl+C to exit.")
    logger.info("📱 텔레그램 명령 큐 모니터링 활성화 (2초 주기)")
    logger.info("=" * 60)

    # 1. 텔레그램 리스너 백그라운드 스레드 시작
    logger.info("🚀 텔레그램 봇 리스너 백그라운드 시작 중...")
    telegram_thread = threading.Thread(target=run_telegram_listener, daemon=True)
    telegram_thread.start()

    # 첫 사이클 즉시 실행
    execute_trading_cycle(manager=manager)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
