import os
import pandas as pd
from src.data.market_data import UpbitMarketData

def _run_backtest_worker(args):
    """지정된 파라미터로 백테스트를 수행하는 독립 워커 함수 (병렬화용)"""
    (strategy_name, params, tickers, setup_data, entry_data, timeline, target_regime, regimes) = args
    
    # 함수 내 로컬 임포트 (병렬 가동 시 메모리 효율 및 참조 오류 방지)
    from src.core.portfolio_manager import PortfolioManager
    from src.core.manager import ManagerAgent
    from src.backtest_system import MockBroker, MockNotifier

    temp_db = f"data/opt_{strategy_name}_{os.getpid()}_{hash(str(params)) % 1000}.db"
    pm = PortfolioManager(total_capital=1000000, db_path=temp_db)
    pm.allocate("opt_agent", 1000000)
    
    manager = ManagerAgent("opt_agent", pm)
    manager.broker = MockBroker(pm, agent_name="opt_agent")
    manager.notifier = MockNotifier()
    manager.execution_manager.broker = manager.broker
    manager.execution_manager.notifier = manager.notifier
    
    if target_regime:
        manager.strategy_map = {target_regime: [strategy_name]}
    else:
        manager.strategy_map = {r: [strategy_name] for r in regimes}
        
    # Nested 파라미터 변환
    nested_params = {}
    for k, v in params.items():
        if "." in k:
            p, c = k.split(".")
            if p not in nested_params: nested_params[p] = {}
            nested_params[p][c] = v
        else:
            nested_params[k] = v
    manager.strategy_manager.optimized_params = {strategy_name: nested_params}

    value_history = []
    
    # 타임라인 루프 실행
    for current_time in timeline:
        # 1. BTC 데이터(장세 판단용) 슬라이싱
        btc_setup_full = setup_data.get("KRW-BTC")
        if btc_setup_full is None: continue
        
        btc_setup_slice = btc_setup_full[btc_setup_full["time"] <= current_time]
        if btc_setup_slice.empty or len(btc_setup_slice) < 60: 
            continue
            
        regime = UpbitMarketData.market_regime(btc_setup_slice)
        if target_regime and regime != target_regime:
            continue
            
        # 2. 전체 티커 데이터 슬라이싱
        setup_slice = {t: df[df["time"] <= current_time] for t, df in setup_data.items() 
                       if not df[df["time"] <= current_time].empty}
        entry_slice = {t: df[df["time"] <= current_time] for t, df in entry_data.items() 
                       if not df[df["time"] <= current_time].empty}
        
        if not entry_slice: continue 
        
        manager.execute_cycle(setup_slice, entry_slice, regime)
        value_history.append(pm.get_total_value("opt_agent"))

    summary = pm.get_portfolio_summary("opt_agent")
    roi = summary.get("return_rate", 0)
    pf = summary.get("profit_factor", 0)
    
    mdd = 0
    if value_history:
        df_vals = pd.Series(value_history)
        mdd = ((df_vals - df_vals.cummax()) / df_vals.cummax()).min() * 100

    score = (roi * 0.6) + (min(pf, 5) * 10) - (abs(mdd) * 0.5)
    
    if os.path.exists(temp_db): 
        try: os.remove(temp_db)
        except: pass
    
    return {
        "score": score,
        "roi": roi,
        "pf": pf,
        "mdd": mdd,
        "total_trades": summary.get("total_trades", 0),
        "params": params,
        "strategy_name": strategy_name,
        "target_regime": target_regime
    }
