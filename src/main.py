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
from src.agents.manager import ManagerAgent
from src.utils.broker_api import UpbitBroker
from src.utils.portfolio_manager import PortfolioManager
from src.utils.telegram_notifier import TelegramNotifier
from src.interfaces.telegram_listener import run_telegram_listener

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
    setup_market_data = {}
    entry_market_data = {}
    for ticker in all_tickers:
        data_60 = broker.get_ohlcv_with_indicators_new(
            ticker, count=100, interval="minutes/60"
        )
        data_15 = broker.get_ohlcv_with_indicators_new(
            ticker, count=20, interval="minutes/15"
        )
        if not data_60.empty and not data_15.empty:
            setup_market_data[ticker] = data_60
            entry_market_data[ticker] = data_15

    if not setup_market_data:
        logger.error("[System] Failed to fetch market data from Upbit. Skipping cycle.")
        return

    btc_regime = broker.btc_regime()
    logger.info(f"[Market Indicators] BTC regime: {btc_regime}")

    # 2. Manager evalutes market
    manager.execute_cycle(setup_market_data, entry_market_data, btc_regime)

    logger.info("--- [System] High Frequency Loop Completed ---")


def execute_daily_sync(pm, manager, notifier):
    logger.info("--- Running Daily Sync Loop (Midnight) ---")
    logger.info("[Manager] Performing daily audit...")
    update_target_coins()
    try:
        sync_result = synchronize_balances(pm, manager, notifier)
        notifier.send_message(sync_result)
    except Exception as e:
        logger.error(f"Daily sync error: {e}")


def synchronize_balances(pm, manager, notifier) -> str:
    """업비트 실잔고 기반 포트폴리오 100% 동기화 및 재배분 실행"""
    logger.info("[System] 🔄 업비트 실잔고 기반 포트폴리오 동기화 실행 중...")

    try:
        broker = UpbitBroker()
        balances = broker.get_balances()

        # 1. 실제 보유 잔고 및 코인 원가 파악
        total_cash = 0.0
        coin_holdings = {}

        for b in balances:
            currency = b.get("currency")
            balance = float(b.get("balance", "0"))
            avg_price = float(b.get("avg_buy_price", "0"))

            if balance <= 0:
                continue

            if currency == "KRW":
                total_cash = balance
            elif (
                balance * avg_price > 100
            ):  # 에어드랍(100원 미만) 및 원가 없는 코인 제외
                # 제외 목록 보완 (WEMIX 등 상폐) - 일단 WEMIX는 명시적 제외 또는 매입원가 0 처리로 걸러지면 됨
                if currency not in (
                    "WEMIX",
                    "APENFT",
                    "MEETONE",
                    "HORUS",
                    "ADD",
                    "CHL",
                    "BLACK",
                ):
                    ticker = f"KRW-{currency}"
                    coin_holdings[ticker] = {
                        "volume": balance,
                        "avg_price": avg_price,
                        "total_cost": balance * avg_price,
                    }

        total_coin_cost = sum(v["total_cost"] for v in coin_holdings.values())
        true_total_capital = total_cash + total_coin_cost

        if not manager:
            return "❌ 동기화 실패: 매니저가 없습니다."

        target_capital_per_agent = true_total_capital

        # 2. pm.portfolios 초기화 & 코인 소유권 할당
        # 모든 코인은 manager에게 몰아줌
        agent_name = manager.name
        pm.portfolios[agent_name] = {
            "cash": 0.0,
            "holdings": {},
            "initial_capital": target_capital_per_agent,
            "total_trades": pm.portfolios.get(agent_name, {}).get("total_trades", 0),
            "winning_trades": pm.portfolios.get(agent_name, {}).get(
                "winning_trades", 0
            ),
        }

        # 더 이상 활성화되지 않은 에이전트는 삭제
        for old_agent in list(pm.portfolios.keys()):
            if old_agent != agent_name:
                del pm.portfolios[old_agent]

        pm.total_capital = true_total_capital

        allocated_costs = 0.0

        for ticker, data in coin_holdings.items():
            pm.portfolios[agent_name]["holdings"][ticker] = data
            allocated_costs += data["total_cost"]

        # 3. 부족한 현금 채워주기
        required_cash = target_capital_per_agent - allocated_costs
        pm.portfolios[agent_name]["cash"] = required_cash

        # 4. 저장 및 MD 업데이트
        pm.save_state()
        pm.export_portfolio_report(agent_name)

        msg = (
            f"✅ **실계좌 동기화 100% 완료**\n\n"
            f"💰 총 자본금: {true_total_capital:,.0f} KRW\n"
            f"💳 보유 현금: {total_cash:,.0f} KRW\n"
            f"🪙 코인 원가: {total_coin_cost:,.0f} KRW\n\n"
            f"👥 **매니저에게 {target_capital_per_agent:,.0f} KRW 배분완료.**"
        )
        logger.info(f"[System] 동기화 성공: {true_total_capital} KRW 분배 완료.")
        return msg

    except Exception as e:
        traceback.print_exc()
        return f"❌ 동기화 실패: {e}"


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
    pm.export_portfolio_report("manager")

    # 5. 스레드 풀 초기화
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    # Schedule Jobs
    logger.info(f"⏰ 스케줄러 시작")
    schedule.every(3).minutes.do(execute_trading_cycle, manager=manager)

    schedule.every().day.at("00:00").do(
        execute_daily_sync, pm=pm, manager=manager, notifier=notifier
    )

    # 텔레그램 명령 큐 처리 (30초마다)
    command_handler = CommandQueueHandler(pm, manager, notifier)
    schedule.every(30).seconds.do(command_handler.process)

    logger.info("\n" + "=" * 60)
    logger.info("⏰ Scheduler started. Press Ctrl+C to exit.")
    logger.info("📱 텔레그램 명령 큐 모니터링 활성화 (30초 주기)")
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
