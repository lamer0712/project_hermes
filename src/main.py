import os
import sys
import json
import math
import glob
from dotenv import load_dotenv
load_dotenv(override=True)
import time
import schedule
import concurrent.futures
from src.agents.manager import ManagerAgent
from src.agents.investor import InvestorAgent
from src.agents.global_risk import GlobalRiskAgent
from src.utils.market_data import UpbitMarketData
from src.utils.broker_api import UpbitBroker
from src.utils.portfolio_manager import PortfolioManager
from src.utils.command_queue import CommandQueue
from src.utils.telegram_notifier import TelegramNotifier
from src.strategies.rsi_momentum import RSIMomentumStrategy
from src.strategies.bollinger_reversion import BollingerReversionStrategy
from src.strategies.breakout import BreakoutStrategy
from src.strategies.aggressive_momentum import AggressiveMomentumStrategy
from src.strategies.ema_scalping import EMAScalpingStrategy
from src.strategies.multi_indicator import MultiIndicatorConvergenceStrategy
from src.strategies.volatility_momentum import VolatilityMomentumStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.pullback_reversal import PullbackReversalStrategy
from src.utils.logger import logger

# 사용 가능한 전략 목록 (순환 배정용)
AVAILABLE_STRATEGIES = [
    ("rsi_momentum", RSIMomentumStrategy, {}),
    ("bollinger_reversion", BollingerReversionStrategy, {}),
    ("aggressive_momentum", AggressiveMomentumStrategy, {}),
    ("ema_scalping", EMAScalpingStrategy, {}),
    ("multi_indicator", MultiIndicatorConvergenceStrategy, {}),
    ("volatility_momentum", VolatilityMomentumStrategy, {}),
    ("mean_reversion", MeanReversionStrategy, {}),
    ("pullback_reversal", PullbackReversalStrategy, {}),
]

# 코인 대상 티커 설정 (동적으로 업데이트됨)
TARGET_COINS = ["KRW-BTC", "KRW-ETH", "KRW-SOL"] # 기본값

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

def refresh_target_coins():
    global TARGET_COINS
    logger.info("--- [Manager] 동적 대상 코인 선정 프로세스 시작 ---")
    new_coins = UpbitMarketData.get_dynamic_target_coins(top_n=10)
    if new_coins:
        TARGET_COINS = new_coins
    logger.info(f"--- [Manager] 현재 타겟 코인: {TARGET_COINS} ---")

def run_high_frequency_loop(risk_agent, investors):
    logger.info("--- [Manager] Running High Frequency Loop (Every 3 min) ---")
    
    # 매수/매도 대상을 추리기 위해 타겟 코인 + 현재 보유 코인 통합
    broker = UpbitBroker()
    balances = broker.get_balances()
    held_coins = [f"KRW-{b.get('currency')}" for b in balances if b.get('currency') != "KRW" and float(b.get("balance", "0")) > 0]
    all_tickers = [t for t in set(TARGET_COINS + held_coins) if t not in UpbitMarketData._blacklisted_markets]
    logger.info(f"--- [Manager] Monitoring {len(all_tickers)} Tickers (블랙리스트 {len(UpbitMarketData._blacklisted_markets)}개 제외) ---")
    
    # 0. Get Advanced Market Data (OHLCV + Indicators) for each Target Coin
    setup_market_data = {}
    entry_market_data = {}
    for ticker in all_tickers:
        # 15분봉 기준 캔들 60개(15시간치) 확보하여 지표 연산
        data_60 = UpbitMarketData.get_ohlcv_with_indicators_new(ticker, count=100, interval="minutes/60")
        data_15 = UpbitMarketData.get_ohlcv_with_indicators_new(ticker, count=20, interval="minutes/15")
        if not data_60.empty and not data_15.empty :
            setup_market_data[ticker] = data_60
            entry_market_data[ticker] = data_15
            
    if not setup_market_data:
        logger.error("[System] Failed to fetch market data from Upbit. Skipping cycle.")
        return

    data_btc = UpbitMarketData.get_ohlcv_with_indicators_new("KRW-BTC", count=200, interval="minutes/240")
    market_is_bullish = UpbitMarketData.is_bullish(data_btc)
    logger.info(f"[Market Indicators] BTC rsi: {data_btc.iloc[-1].rsi_14:.1f} is bullish: {market_is_bullish}")
    
    # 1. Check Global Risk (단순 가격 총합 등 필요시 전송)
    # risk_agent는 현재가를 주로 보므로 단순화 치환
    simple_prices = {t: setup_market_data[t].close.iloc[-1] for t in setup_market_data}
    market_is_bullish = risk_agent.execute_logic(simple_prices, market_is_bullish)


    # 2. Investors evaluate market — 각 investor는 전체 종목을 평가 후 최선의 1종목만 매수
    for investor in investors:
        investor.execute_cycle(setup_market_data, entry_market_data, market_is_bullish)
    
    logger.info("--- [Manager] High Frequency Loop Completed ---")

def run_hourly_loop(investors, risk_agent, executor):
    logger.info("--- [Agent] Running Hourly Loop (Ollama Wake-up) ---")
    
    # 시간당 루프에서도 동일하게 풍부한 지표 데이터를 LLM에게 던져주어 판단 근거 부여
    broker = UpbitBroker()
    balances = broker.get_balances()
    held_coins = [f"KRW-{b.get('currency')}" for b in balances if b.get('currency') != "KRW" and float(b.get("balance", "0")) > 0]
    all_tickers = [t for t in set(TARGET_COINS + held_coins) if t not in UpbitMarketData._blacklisted_markets]
    
    advanced_market_data = {}
    for ticker in all_tickers:
        data = UpbitMarketData.get_ohlcv_with_indicators(ticker, count=60, interval="minutes/60") # 15분 봉 기준
        if data:
            advanced_market_data[ticker] = data
            
    simple_prices = {t: advanced_market_data[t]['current_price'] for t in advanced_market_data if 'current_price' in advanced_market_data[t]}
    simple_prices = {t: advanced_market_data[t]['current_price'] for t in advanced_market_data if 'current_price' in advanced_market_data[t]}
    
    # 1. (매니저 재배분 로직은 run_daily_rebalance_loop로 분리됨)
    
    # 2. Risk Agent LLM 기반 심층 리스크 분석 (hourly만)
    executor.submit(risk_agent.execute_logic_llm, simple_prices)
    
    # 3. Investors awake to update their strategy rules (LLM Cost Incurred) 실효성이 없어 보임 잠시 commnet 처리
    # for investor in investors:
    #     executor.submit(investor.review_and_update_strategy, advanced_market_data)

def run_daily_rebalance_loop(manager_agent, executor):
    logger.info("--- [Manager] Running Daily Reallocation Loop (Gemini) ---")
    broker = UpbitBroker()
    balances = broker.get_balances()
    held_coins = [f"KRW-{b.get('currency')}" for b in balances if b.get('currency') != "KRW" and float(b.get("balance", "0")) > 0]
    all_tickers = [t for t in set(TARGET_COINS + held_coins) if t not in UpbitMarketData._blacklisted_markets]
    
    advanced_market_data = {}
    for ticker in all_tickers:
        data = UpbitMarketData.get_ohlcv_with_indicators(ticker, count=60, interval="minutes/60") 
        if data:
            advanced_market_data[ticker] = data
            
    simple_prices = {t: advanced_market_data[t]['current_price'] for t in advanced_market_data if 'current_price' in advanced_market_data[t]}
    
    # Manager Agent updates portfolio (Capital Reallocation)
    executor.submit(manager_agent.execute_logic, simple_prices)

def run_daily_loop():
    logger.info("--- Running Daily Loop (Midnight) ---")
    logger.info("[Shadow Agent] Performing daily audit...")
    refresh_target_coins()

def run_daily_sync(pm, investors, notifier):
    logger.info("--- Running Daily Sync (Midnight) ---")
    try:
        from src.main import sync_real_balances
        sync_result = sync_real_balances(pm, investors, notifier)
        notifier.send_message(sync_result)
    except Exception as e:
        logger.error(f"Daily sync error: {e}")

def restart_main(notifier=None):
    """main.py 프로세스를 자체 재시작합니다."""
    import sys
    logger.info("\n" + "=" * 60)
    logger.info("🔄 [System] main.py 재시작...")
    logger.info("=" * 60)
    
    if notifier:
        notifier.send_message("🔄 *main.py 재시작...*")
    
    os.execv(sys.executable, [sys.executable] + sys.argv)


def process_command_queue(pm, investors, manager, notifier):
    """텔레그램에서 들어온 명령을 큐에서 꺼내 실행합니다."""
    commands = CommandQueue.pop_all()
    if not commands:
        return
    
    logger.info(f"\n--- [Command Queue] {len(commands)}개 명령 처리 시작 ---")
    
    should_restart = False
    
    for cmd in commands:
        command = cmd.get("command")
        params = cmd.get("params", {})
        
        try:
            if command == "restart":
                should_restart = True
                continue  # 재시작은 모든 명령 처리 후 마지막에
            
            elif command == "kill":
                if notifier:
                    notifier.send_message("🛑 *시스템 종료 명령이 수신되었습니다. 프로그램을 종료합니다.*")
                logger.info("[System] Kill command received. Exiting...")
                sys.exit(0)
                
            elif command == "add_investor":
                new_inv = add_investor_dynamically(pm, investors, params)
                if new_inv:
                    msg = f"✅ 새 Investor '{new_inv.name}' 추가 완료!\n전략: {new_inv.strategy.name}\n배분 자본: {pm.get_available_cash(new_inv.name):,.0f} KRW"
                else:
                    msg = "❌ Investor 추가 실패"
                notifier.send_message(msg)
                    
            elif command == "rebalance":
                run_rebalance(pm, investors, manager, notifier)
                    
            elif command == "status":
                status_msg = get_status_message(pm, params.get("agent"))
                notifier.send_message(status_msg)
            
            elif command == "liquidate":
                liquidate_result = execute_liquidate(pm, investors, params)
                notifier.send_message(liquidate_result)
                
            elif command == "limit_sell":
                limit_sell_result = execute_limit_sell(pm, investors, params)
                notifier.send_message(limit_sell_result)
                    
            elif command == "sync":
                sync_result = sync_real_balances(pm, investors, notifier)
                notifier.send_message(sync_result)
            
            elif command == "halt":
                agent_name = params.get("agent")
                if pm.set_halt(agent_name, True):
                    notifier.send_message(f"🛑 *[{agent_name}] 거래 중지 설정 완료*")
                else:
                    notifier.send_message(f"❌ *[{agent_name}]* 에이전트를 찾을 수 없습니다.")

            elif command == "resume":
                agent_name = params.get("agent")
                if pm.set_halt(agent_name, False):
                    notifier.send_message(f"✅ *[{agent_name}] 거래 재개 설정 완료*")
                else:
                    notifier.send_message(f"❌ *[{agent_name}]* 에이전트를 찾을 수 없습니다.")

            elif command == "clear":
                # 1. Clear *.log files
                log_files = glob.glob("*.log")
                for log_file in log_files:
                    with open(log_file, 'w') as f:
                        pass
                
                # 2. Clear agents/*/trades.md files
                trade_files = glob.glob("agents/*/trades.md")
                for trade_file in trade_files:
                    with open(trade_file, 'w') as f:
                        f.write("# Trade History\n\n")
                
                notifier.send_message("🧹 *로그 및 거래 내역 정리 완료*")
                logger.info("[System] Logs and trade history cleared.")
            
            elif command == "update_strategy":
                agent_name = params.get("agent")
                # 해당 에이전트 찾기
                investor = next((inv for inv in investors if inv.name == agent_name), None)
                if not investor:
                    notifier.send_message(f"❌ *[{agent_name}]* 에이전트를 찾을 수 없습니다.")
                else:
                    proposal_path = os.path.join("agents", agent_name, "proposed_strategy.json")
                    if os.path.exists(proposal_path):
                        with open(proposal_path, "r", encoding="utf-8") as f:
                            proposal = json.load(f)
                        
                        new_params = proposal.get("new_parameters")
                        investor.strategy.update_params(new_params)
                        # strategy.md 및 관련 파일 갱신 (InvestorAgent 내부 메서드 활용 불가시 직접 호출)
                        investor._initialize_strategy_md()
                        
                        # 제안 파일 삭제
                        os.remove(proposal_path)
                        
                        notifier.send_message(f"✅ *[{agent_name}]* 전략 업데이트 완료!\n새 파라미터: `{json.dumps(new_params)}`")
                        logger.info(f"[System] [{agent_name}] 전략 파라미터 업데이트 완료 및 제안 파일 삭제")
                    else:
                        notifier.send_message(f"❌ *[{agent_name}]* 대기 중인 전략 제안 파일을 찾을 수 없습니다.")
                    
            else:
                logger.info(f"[Command Queue] 알 수 없는 명령: {command}")
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"[Command Queue] 명령 실행 오류: {command} - {e}")
            notifier.send_message(f"❌ 명령 실행 오류: {command}\n{e}")
    
    # 모든 명령 처리 후 재시작
    if should_restart:
        restart_main(notifier)

def add_investor_dynamically(pm, investors, params: dict):
    """새 investor를 동적으로 추가합니다."""
    # 이름 자동 생성 (현재 investor 수 기반)
    existing_names = [inv.name for inv in investors]
    alphabet = 'gamma delta epsilon zeta eta theta iota kappa'.split()
    new_name = None
    for name in alphabet:
        candidate = f"agent_{name}"
        if candidate not in existing_names:
            new_name = candidate
            break
    
    if not new_name:
        new_name = f"agent_{len(investors) + 1}"
    
    # 전략 선택 (지정된 게 있으면 사용, 없으면 순환 배정)
    strategy_key = params.get("strategy")
    if strategy_key:
        for key, cls, default_params in AVAILABLE_STRATEGIES:
            if key == strategy_key:
                strategy = cls(default_params if default_params else None)
                break
        else:
            strategy = AVAILABLE_STRATEGIES[len(investors) % len(AVAILABLE_STRATEGIES)]
            strategy = strategy[1](strategy[2] if strategy[2] else None)
    else:
        # 순환 배정: 아직 사용하지 않은 전략 우선
        used_strategies = {type(inv.strategy).__name__ for inv in investors}
        chosen = None
        for key, cls, default_params in AVAILABLE_STRATEGIES:
            if cls.__name__ not in used_strategies:
                chosen = (cls, default_params)
                break
        if not chosen:
            idx = len(investors) % len(AVAILABLE_STRATEGIES)
            chosen = (AVAILABLE_STRATEGIES[idx][1], AVAILABLE_STRATEGIES[idx][2])
        strategy = chosen[0](chosen[1] if chosen[1] else None)
    
    # 자본 배분: 기존 투자자들에서 균등하게 가져옴
    total_agents = len(investors) + 1
    target_per_agent = pm.total_capital / total_agents
    
    # 기존 투자자들의 현금에서 조금씩 차감 (가용 현금 범위 내에서만)
    new_capital = 0
    for inv in investors:
        available = pm.get_available_cash(inv.name)
        take = min(available * 0.2, target_per_agent / len(investors))  # 각 20%까지만
        if take > 0:
            pm.portfolios[inv.name]["cash"] -= take
            new_capital += take
    
    if new_capital < 5000:
        # 현금이 부족하면 최소 금액으로
        new_capital = min(pm.total_capital * 0.1, 50000)
    
    pm.allocate(new_name, new_capital)
    
    new_investor = InvestorAgent(new_name, strategy=strategy, portfolio_manager=pm)
    investors.append(new_investor)
    
    logger.info(f"[System] 🆕 새 Investor 추가: {new_name} (전략: {strategy.name}, 배분: {new_capital:,.0f} KRW)")
    pm._update_all_portfolio_md()
    pm._update_manager_portfolio_md()
    pm._save_state()
    return new_investor

def sync_real_balances(pm, investors, notifier) -> str:
    """업비트 실제 잔고를 읽어 포트폴리오(현금, 자산 등)를 1원 단위까지 100% 동기화하고 재배분합니다."""
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
            elif balance * avg_price > 100:  # 에어드랍(100원 미만) 및 원가 없는 코인 제외
                # 제외 목록 보완 (WEMIX 등 상폐) - 일단 WEMIX는 명시적 제외 또는 매입원가 0 처리로 걸러지면 됨
                if currency not in ("WEMIX", "APENFT", "MEETONE", "HORUS", "ADD", "CHL", "BLACK"):
                    ticker = f"KRW-{currency}"
                    coin_holdings[ticker] = {
                        "volume": balance,
                        "avg_price": avg_price,
                        "total_cost": balance * avg_price
                    }
                    
        total_coin_cost = sum(v["total_cost"] for v in coin_holdings.values())
        true_total_capital = total_cash + total_coin_cost
        
        if len(investors) == 0:
            return "❌ 동기화 실패: 투자 에이전트(Investor Agent)가 없습니다."
            
        target_capital_per_agent = true_total_capital / len(investors)
        
        # 2. pm.portfolios 초기화 & 코인 소유권 할당
        # 모든 코인은 agent_alpha에게 몰아주되, 기존 각 에이전트가 어떤 종목을 가졌는지 알 수 없으므로(일괄 리셋용),
        # 여기서는 가장 단순하게 각 에이전트들에게 기존 코인을 최대한 분배 (이전 코드의 로직 유사)
        
        # pm 내부 상태 클리어 (하지만 total_trades, winning_trades는 유지)
        for inv in investors:
            agent_name = inv.name
            if agent_name not in pm.portfolios:
                pm.portfolios[agent_name] = {
                    "cash": 0.0,
                    "holdings": {},
                    "initial_capital": target_capital_per_agent,
                    "total_trades": 0,
                    "winning_trades": 0
                }
            else:
                pm.portfolios[agent_name]["cash"] = 0
                pm.portfolios[agent_name]["holdings"] = {}
                pm.portfolios[agent_name]["initial_capital"] = target_capital_per_agent
        
        # 더 이상 활성화되지 않은 (investors에 없는) 에이전트는 삭제 (또는 잔고 0 처리 후 방치)
        # 삭제하는 것이 깔끔함
        active_agent_names = [inv.name for inv in investors]
        for old_agent in list(pm.portfolios.keys()):
            if old_agent not in active_agent_names:
                del pm.portfolios[old_agent]
        
        pm.total_capital = true_total_capital
        
        # 분배할 에이전트들
        agent_names = [inv.name for inv in investors]
        
        # 코인들을 각 에이전트에 순서대로 배분하되, 배분 금액이 목표(target_capital_per_agent)를 넘지 않도록 노력 (넘으면 현금을 확보해 줘야 함)
        import math
        idx = 0
        allocated_costs = {name: 0.0 for name in agent_names}
        
        for ticker, data in coin_holdings.items():
            assigned_agent = agent_names[idx % len(agent_names)]
            pm.portfolios[assigned_agent]["holdings"][ticker] = data
            allocated_costs[assigned_agent] += data["total_cost"]
            idx += 1
            
        # 3. 각 에이전트별 부족한 현금 채워주기
        for agent_name in agent_names:
            required_cash = target_capital_per_agent - allocated_costs[agent_name]
            pm.portfolios[agent_name]["cash"] = required_cash
            
        # 4. 저장 및 MD 업데이트
        pm._save_state()
        pm._update_all_portfolio_md()
        pm._update_manager_portfolio_md()
        
        msg = (
            f"✅ **실계좌 동기화 100% 완료**\n\n"
            f"💰 총 자본금: {true_total_capital:,.0f} KRW\n"
            f"💳 보유 현금: {total_cash:,.0f} KRW\n"
            f"🪙 코인 원가: {total_coin_cost:,.0f} KRW\n\n"
            f"👥 **{len(investors)}명의 에이전트에게 각 {target_capital_per_agent:,.0f} KRW 씩 균등 배분완료.**"
        )
        logger.info(f"[System] 동기화 성공: {true_total_capital} KRW 분배 완료.")
        return msg
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ 동기화 실패: {e}"

def run_rebalance(pm, investors, manager, notifier):
    """성과 기반 자본 재배분을 실행합니다."""
    logger.info("[System] ⚖️ 수동 리밸런싱 실행...")
    
    # 현재 시장가 조회
    broker = UpbitBroker()
    balances = broker.get_balances()
    held_coins = [f"KRW-{b.get('currency')}" for b in balances if b.get('currency') != 'KRW' and float(b.get('balance', '0')) > 0]
    all_tickers = list(set(TARGET_COINS + held_coins))
    
    simple_prices = {}
    for ticker in all_tickers:
        data = UpbitMarketData.get_ohlcv_with_indicators(ticker, count=5, interval="minutes/15")
        if data and 'current_price' in data:
            simple_prices[ticker] = data['current_price']
    
    # 매니저 에이전트가 LLM으로 성과 평가 후 재배분
    manager.execute_logic(simple_prices)
    
    # 결과 알림
    msg = "⚖️ *리밸런싱 완료*\n"
    for inv in investors:
        s = pm.get_summary(inv.name, simple_prices)
        msg += f"• {inv.name}: {s['cash']:,.0f} KRW ({s['return_rate']:+.2f}%)\n"
    notifier.send_message(msg)

def execute_liquidate(pm, investors, params: dict) -> str:
    """특정 에이전트의 특정 티커를 강제 청산(전량 매도)합니다."""
    agent_name = params.get("agent")
    ticker = params.get("ticker")
    
    if not agent_name or not ticker:
        return f"❌ 청산 실패: 에이전트명과 티커를 지정해주세요.\n예: \"beta ARDR 청산\""
    
    # 에이전트 찾기
    investor = None
    for inv in investors:
        if inv.name == agent_name:
            investor = inv
            break
    
    if not investor:
        available = ", ".join(inv.name for inv in investors)
        return f"❌ 청산 실패: '{agent_name}' 에이전트를 찾을 수 없습니다.\n사용 가능: {available}"
    
    # 실제 Upbit 잔고 확인
    broker = UpbitBroker()
    currency = ticker.split("-")[1] if "-" in ticker else ticker
    actual_balance = 0.0
    try:
        balances = broker.get_balances()
        for b in balances:
            if b.get("currency") == currency:
                actual_balance = float(b.get("balance", "0"))
                break
    except Exception as e:
        return f"❌ 청산 실패: 잔고 조회 오류 - {e}"
    
    if actual_balance <= 0:
        # PM에 팬텀 보유량만 있는 경우 정리
        if pm:
            holdings = pm.get_holdings(agent_name)
            if ticker in holdings and holdings[ticker]["volume"] > 0:
                phantom_vol = holdings[ticker]["volume"]
                pm.record_sell(agent_name, ticker, phantom_vol, 0)
                return f"🔄 {agent_name}: {ticker} 실제 잔고 0 → PM 팬텀 보유량({phantom_vol:.6f}) 정리 완료"
        return f"❌ 청산 실패: {ticker} 실제 보유량이 없습니다."
    
    # PM 추적 수량 기준으로 매도 (다른 에이전트 보유분 보호)
    sell_volume = actual_balance  # 폴백: 실제 잔고 전량
    if pm:
        holdings = pm.get_holdings(agent_name)
        if ticker in holdings and holdings[ticker]["volume"] > 0:
            sell_volume = min(holdings[ticker]["volume"], actual_balance)
        else:
            return f"❌ 청산 실패: {agent_name}의 {ticker} PM 보유 기록이 없습니다."
    
    # 시장가 매도
    logger.info(f"[청산] {agent_name}: {ticker} 매도 실행 | 수량: {sell_volume:.6f} (실제잔고: {actual_balance:.6f})")
    res = broker.place_order(ticker, "ask", volume=str(sell_volume), ord_type="market")
    logger.info(res.json())

    if res and "error" not in res:
        # PM 업데이트 (추적 수량 전체 제거)
        if pm:
            holdings = pm.get_holdings(agent_name)
            pm_volume = holdings.get(ticker, {}).get("volume", actual_balance)
            # 현재가 조회 (Upbit ticker API)
            current_price = 0
            logger.info(res.json())
            # pm.record_sell(agent_name, ticker, pm_volume, current_price)
        
        return f"✅ 청산 완료\n에이전트: {agent_name}\n종목: {ticker}\n수량: {actual_balance:.6f}\n결과: 시장가 전량 매도 성공"
    else:
        err_val = res.get("error", {}) if isinstance(res, dict) else {}
        if isinstance(err_val, dict):
            error_msg = err_val.get("message", str(res))
        else:
            error_msg = str(err_val)
            if isinstance(res, dict) and "details" in res and res["details"]:
                import json
                try:
                    parsed = json.loads(res["details"])
                    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                        error_msg = parsed["error"].get("message", error_msg)
                except Exception:
                    pass
        return f"❌ 청산 실패: {ticker} 매도 주문 오류\n{error_msg}"

def execute_limit_sell(pm, investors, params: dict) -> str:
    """특정 에이전트의 지정가 전량 매도를 실행합니다."""
    agent_name = params.get("agent")
    ticker = params.get("ticker")
    price = params.get("price")
    
    if not agent_name or not ticker or not price:
        return "❌ 지정가 매도 실패: 에이전트명, 종목, 또는 가격 누락"
        
    investor = None
    for inv in investors:
        if inv.name == agent_name:
            investor = inv
            break
            
    if not investor:
        return f"❌ 지정가 매도 실패: '{agent_name}' 에이전트를 찾을 수 없습니다."
        
    # 실제 Upbit 잔고 확인
    broker = UpbitBroker()
    currency = ticker.split("-")[1] if "-" in ticker else ticker
    actual_balance = 0.0
    try:
        balances = broker.get_balances()
        for b in balances:
            if b.get("currency") == currency:
                actual_balance = float(b.get("balance", "0"))
                break
    except Exception as e:
        return f"❌ 지정가 매도 실패: 잔고 조회 오류 - {e}"
        
    if actual_balance <= 0:
        return f"❌ 지정가 매도 실패: {ticker} 실제 보유량이 없습니다."
        
    # PM 추적 수량 기준으로 매도 (다른 에이전트 보유분 보호)
    sell_volume = actual_balance
    if pm:
        holdings = pm.get_holdings(agent_name)
        if ticker in holdings and holdings[ticker]["volume"] > 0:
            sell_volume = min(holdings[ticker]["volume"], actual_balance)
        else:
            return f"❌ 지정가 매도 실패: {agent_name}의 {ticker} 장부상 보유 기록이 없습니다."
            
    price = int(math.ceil(price * 1.001 / sell_volume * 0.01) * 100)
    logger.info(f"[지정가 매도] {agent_name}: {ticker} 매도 실행 | 수량: {sell_volume:.6f} | 지정가: {price} KRW" )
    res = broker.place_order(ticker, "ask", volume=str(sell_volume), price=str(price), ord_type="limit")
    
    if res and "error" not in res:
        # 지정가 매도는 즉시 체결되지 않지만, 편의상 PM 장부에서는 즉시 차감함
        if pm:
            # 지정가 체결이 다를 수 있으나 현재 구조상 record_sell로 장부상 즉시 차감
            pm.record_sell(agent_name, ticker, sell_volume, price)
        
        from src.utils.markdown_io import append_markdown
        append_markdown(investor.trades_path,
            f"- [수동 명령] 지정가 매도: {ticker} | 수량: {sell_volume:.6f} | 가격: {price} | Res: {res}")
            
        uuid = res.get("uuid", "N/A")
        return f"✅ 지정가 매도 주문 접수 완료\n에이전트: {agent_name}\n종목: {ticker}\n수량: {sell_volume:.6f}\n지정가: {price} KRW\n주문 UUID: {uuid}"
    else:
        err_val = res.get("error", {}) if isinstance(res, dict) else {}
        if isinstance(err_val, dict):
            error_msg = err_val.get("message", str(res))
        else:
            error_msg = str(err_val)
            if isinstance(res, dict) and "details" in res and res["details"]:
                import json
                try:
                    parsed = json.loads(res["details"])
                    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                        error_msg = parsed["error"].get("message", error_msg)
                except Exception:
                    pass
        return f"❌ 지정가 매도 주문 실패: {ticker}\n{error_msg}"

def get_status_message(pm, target_agent: str = None) -> str:
    """포트폴리오 상태 메시지를 생성합니다."""
    
    if target_agent and target_agent in pm.portfolios:
        s = pm.get_summary(target_agent)
        msg = f"📊 *포트폴리오 상세 현황 ({target_agent})*\n\n"
        
        # 상태 확인 (Halt / Kill Switch)
        is_halted = pm.is_halted(target_agent)
        total_trades = s.get("total_trades", 0)
        win_rate = s.get("win_rate", 100)
        return_rate = s.get("return_rate", 0)
        
        if is_halted:
            msg += "🛑 *거래 중지됨 (Halted)*\n"
        elif total_trades > 10 and (win_rate < 20.0 or return_rate < -15.0):
            msg += "🛑 *매수 차단됨 (Kill Switch 발동)*\n"
        
        msg += "\n"
            
        msg += f"현금: {s['cash']:,.0f} KRW\n"
        msg += f"총액: {s['total_value']:,.0f} KRW\n"
        msg += f"수익률: {s['return_rate']:+.2f}%\n"
        msg += f"매매: {s['total_trades']}회 (승률 {s['win_rate']:.0f}%)\n\n"
        
        msg += "*보유 종목*\n"
        holdings = s.get('holdings', {})
        if not holdings:
            msg += "없음\n"
        else:
            for ticker, data in holdings.items():
                cost = data.get('total_cost', 0)
                vol = data.get('volume', 0)
                avg = data.get('avg_price', 0)
                msg += f"• {ticker}: {vol:.6f} (평단 {avg:,.2f}, 매입가 {cost:,.0f}원)\n"
                
        return msg

    msg = "📊 *포트폴리오 현황*\n\n"
    for agent_name in sorted(pm.portfolios.keys()):
        s = pm.get_summary(agent_name)
        msg += f"*{agent_name}*\n"
        
        # 상태 확인 (Halt / Kill Switch)
        is_halted = pm.is_halted(agent_name)
        total_trades = s.get("total_trades", 0)
        win_rate = s.get("win_rate", 100)
        return_rate = s.get("return_rate", 0)
        
        if is_halted:
            msg += "  🛑 *거래 중지됨 (Halted)*\n"
        elif total_trades > 10 and (win_rate < 20.0 or return_rate < -15.0):
            msg += "  🛑 *매수 차단됨 (Kill Switch 발동)*\n"
            
        msg += f"  현금: {s['cash']:,.0f} KRW\n"
        msg += f"  총액: {s['total_value']:,.0f} KRW\n"
        msg += f"  수익률: {s['return_rate']:+.2f}%\n"
        msg += f"  매매: {s['total_trades']}회 (승률 {s['win_rate']:.0f}%)\n\n"
    msg += f"총 자본: {pm.total_capital:,.0f} KRW"
    return msg

def main():
    logger.info("=" * 60)
    logger.info("🏢 Multi-Agent Automated Investment Firm 시작")
    logger.info("=" * 60)
    refresh_target_coins()
    
    # 1. 실제 Upbit KRW 잔고 기반 포트폴리오 매니저 초기화
    total_capital = get_upbit_krw_balance()
    pm = PortfolioManager(total_capital=total_capital)
    
    # 기존 상태가 없으면 초기 배분
    if not pm.portfolios:
        logger.info(f"\n💰 초기 자본 배분: 총 {total_capital:,.0f} KRW (Upbit 실잔고)")
        pm.allocate("agent_alpha", total_capital * 0.4)  # 40%
        pm.allocate("agent_epsilon", total_capital * 0.4)   # 40%
        pm.allocate("agent_gamma", total_capital * 0.2)  # 20%
    else:
        logger.info(f"\n💰 기존 포트폴리오 상태 복원 완료")
        for name in pm.portfolios:
            s = pm.get_summary(name)
            logger.info(f"  - {name}: 현금 {s['cash']:,.0f} KRW, 수익률 {s['return_rate']:+.2f}%")
    
    # 2. 전략 객체 및 Investor 초기화 (agents 폴더 내 동적 로딩)
    investors = []
    agents_dir = "agents"
    if os.path.exists(agents_dir):
        for agent_name in sorted(os.listdir(agents_dir)):
            agent_path = os.path.join(agents_dir, agent_name)
            if os.path.isdir(agent_path) and agent_name.startswith("agent_"):
                strategy_file = os.path.join(agent_path, "strategy.md")
                params = {}
                strategy = None
                
                # strategy.md 파싱 시도
                if os.path.exists(strategy_file):
                    try:
                        with open(strategy_file, "r") as f:
                            content = f.read()
                            
                            # JSON 파라미터 추출
                            import re
                            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                            if json_match:
                                params = json.loads(json_match.group(1))
                            # 전략 식별
                            for key, StrategyClass, default_params in AVAILABLE_STRATEGIES:
                                temp_strategy = StrategyClass(default_params)
                                if temp_strategy.name in content:
                                    strategy = StrategyClass(params if params else default_params)
                                    break
                    except Exception as e:
                        logger.error(f"[System] ⚠️ {agent_name}의 strategy.md 파싱 실패: {e}")
                        print(json_match)
                        dfsdfsx
                
                # 식별 실패시 순환 배정
                if not strategy:
                    idx = len(investors) % len(AVAILABLE_STRATEGIES)
                    strategy_cls = AVAILABLE_STRATEGIES[idx][1]
                    default_p = AVAILABLE_STRATEGIES[idx][2]
                    strategy = strategy_cls(default_p)
                
                investor = InvestorAgent(agent_name, strategy=strategy, portfolio_manager=pm)
                investors.append(investor)
                
    # 3. 폴더가 비어있거나 없으면(초기 세팅) 기본 agent_alpha, agent_beta 생성
    if not investors:
        alpha_strategy = RSIMomentumStrategy({
            "buy_rsi_threshold": 35,
            "sell_rsi_threshold": 70,
            "require_bullish_trend": True,
            "position_size_ratio": 0.3,
        })
        
        epsilon_strategy = VolatilityMomentumStrategy({
            "score_threshold_buy": 7.0,
            "take_profit_pct": 1.5,
            "stop_loss_pct": -2.0,
            "position_size_ratio": 0.25,
            "trailing_stop_pct": -0.8 # 이익 보존 추가
        })
        
        investors = [
            InvestorAgent("agent_alpha", strategy=alpha_strategy, portfolio_manager=pm),
            InvestorAgent("agent_epsilon", strategy=epsilon_strategy, portfolio_manager=pm),
        ]
    
    logger.info(f"\n👥 Investor 에이전트 {len(investors)}명 초기화 완료:")
    for inv in investors:
        logger.info(f"  - {inv.name}: 전략 '{inv.strategy.name}'")
    
    # 4. Manager 에이전트 초기화 (포트폴리오 매니저 연동)
    manager = ManagerAgent(portfolio_manager=pm)
    risk = GlobalRiskAgent()
    
    logger.info(f"\n📋 초기 포트폴리오 MD 파일 생성 완료")
    pm._update_all_portfolio_md()
    pm._update_manager_portfolio_md()
    
    # 5. 텔레그램 알림 초기화
    notifier = TelegramNotifier()
    logger.info(f"\n📱 텔레그램 알림 초기화 완료")
    
    # 6. 스레드 풀 초기화
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    
    # Schedule Jobs
    logger.info(f"\n⏰ 스케줄러 시작")
    schedule.every(3).minutes.do(run_high_frequency_loop, risk_agent=risk, investors=investors)
    schedule.every().hour.at(":30").do(run_hourly_loop, investors=investors, risk_agent=risk, executor=executor)
    
    # 하루 2번 (08:00, 20:00) 매니저 리밸런싱 (Gemini API 한도 고려)
    schedule.every().day.at("08:00").do(run_daily_rebalance_loop, manager_agent=manager, executor=executor)
    schedule.every().day.at("20:00").do(run_daily_rebalance_loop, manager_agent=manager, executor=executor)
    
    schedule.every().day.at("00:00").do(run_daily_sync, pm=pm, investors=investors, notifier=notifier)
    schedule.every().day.at("00:00").do(run_daily_loop)
    # 텔레그램 명령 큐 확인 (30초마다)
    schedule.every(30).seconds.do(process_command_queue, pm=pm, investors=investors, manager=manager, notifier=notifier)
    
    logger.info("\n" + "=" * 60)
    logger.info("⏰ Scheduler started. Press Ctrl+C to exit.")
    logger.info("📱 텔레그램 명령 큐 모니터링 활성화 (30초 주기)")
    logger.info("=" * 60)
    
    # 첫 사이클 즉시 실행 (스케줄러 대기 없이)
    run_high_frequency_loop(risk_agent=risk, investors=investors)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
