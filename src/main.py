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
from src.utils.manager import ManagerAgent
from src.utils.broker_api import UpbitBroker
from src.utils.portfolio_manager import PortfolioManager
from src.utils.telegram_notifier import TelegramNotifier
from src.interfaces.telegram_listener import run_telegram_listener
from src.interfaces.upbit_websocket import UpbitWebSocketClient

from src.utils.command_handler import CommandQueueHandler
from src.utils.logger import logger

# 코인 대상 티커 설정 (동적으로 업데이트됨)
TARGET_COINS = []  # 기본값


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
    broker = UpbitBroker()
    new_coins = broker.get_dynamic_target_coins(top_n=20)
    if new_coins:
        TARGET_COINS = new_coins
    logger.info(f"--- [System] 현재 타겟 코인: {TARGET_COINS} ---")


def execute_trading_cycle(manager: ManagerAgent):
    logger.info("--- [System] Running Trading Cycle (Every 3 min) ---")

    # 매수/매도 대상을 추리기 위해 타겟 코인 + 현재 보유 코인 통합
    broker = UpbitBroker()
    balances = broker.get_balances()
    held_coins = [
        f"KRW-{b.get('currency')}"
        for b in balances
        if b.get("currency") != "KRW" and float(b.get("balance", "0")) > 0
    ]
    all_tickers = [
        t for t in set(TARGET_COINS + held_coins) if t not in broker.blacklisted_markets
    ]

    logger.info(
        f"--- [System] Monitoring {len(all_tickers)} Tickers (블랙리스트 {len(broker.blacklisted_markets)}개 제외) ---"
    )

    # 0. Get Advanced Market Data (OHLCV + Indicators) for each Target Coin
    setup_market_data = broker.get_multiple_ohlcv_with_indicators(
        all_tickers, count=200, interval="minutes/60"
    )
    entry_market_data = broker.get_multiple_ohlcv_with_indicators(
        all_tickers, count=200, interval="minutes/15"
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

    market_regime = broker.market_regime()
    logger.info(f"[Market Indicators] Market regime: {market_regime}")

    # 2. Manager evalutes market
    manager.execute_cycle(setup_market_data, entry_market_data, market_regime)

    logger.info("--- [System] High Frequency Loop Completed ---")


def execute_daily_sync(pm, manager, notifier):
    logger.info("--- Running Daily Sync Loop (Midnight) ---")
    logger.info("[Manager] Performing daily audit...")
    update_target_coins()
    try:
        sync_result = pm.synchronize_balances(manager)
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
    if not pm.portfolios or "manager" not in pm.portfolios:
        logger.info(f"💰 기존 포트폴리오 상태가 없거나 manager가 없어 초기화합니다.")
        pm.allocate("manager", total_capital)
    else:
        logger.info(f"💰 기존 포트폴리오 상태 복원 완료")
        for name in pm.portfolios:
            s = pm.get_portfolio_summary(name)
            logger.info(
                f"  - {name}: 현금 {s['cash']:,.0f} KRW, 수익률 {s['return_rate']:+.2f}%"
            )

    # 2. 투자 매니저 및 전략 객체 초기화
    manager = ManagerAgent("manager", portfolio_manager=pm)

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
    pm.export_portfolio_report("manager")

    # 5. 스레드 풀 초기화
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    # Schedule Jobs
    logger.info(f"⏰ 스케줄러 시작")
    # schedule.every(3).minutes.do(execute_trading_cycle, manager=manager)
    for m in [0, 15, 30, 45]:
        schedule.every().hour.at(f"{m:02d}:30").do(execute_trading_cycle, manager=manager)

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
