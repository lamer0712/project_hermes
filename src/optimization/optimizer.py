import os
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
from src.backtest_system import fetch_and_prepare_historical_data, MockBroker, MockNotifier
from src.core.manager import ManagerAgent
from src.core.portfolio_manager import PortfolioManager
from src.strategies.strategy_manager import StrategyManager
from src.data.market_data import UpbitMarketData
from src.utils.logger import logger

def _run_backtest_worker(args):
    """지정된 파라미터로 백테스트를 수행하는 독립 워커 함수 (병렬화용)"""
    (strategy_name, params, tickers, setup_data, entry_data, timeline, target_regime, regimes) = args
    
    # 순환 참조 방지를 위해 함수 내에서 필요한 클래스 임포트
    from src.core.portfolio_manager import PortfolioManager
    from src.core.manager import ManagerAgent
    from src.backtest_system import MockBroker, MockNotifier
    from src.data.market_data import UpbitMarketData
    import os
    import pandas as pd

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
        
    # Nested 파라미터 변환 (StrategyOptimizer 메서드를 호출할 수 없으므로 직접 구현)
    nested_params = {}
    for k, v in params.items():
        if "." in k:
            p, c = k.split(".")
            if p not in nested_params: nested_params[p] = {}
            nested_params[p][c] = v
        else:
            nested_params[k] = v
    manager.strategy_manager.optimized_params = {strategy_name: nested_params}

    # 성능 최적화: 딕서너리 조회 최소화를 위해 각 티커별 데이터프레임을 리스트로 변환하고 인덱스 추적
    # 슬라이싱 성능을 극대화하기 위해 미리 필터링된 인덱스를 사용
    value_history = []
    
    # 타임라인 루프 실행
    for current_time in timeline:
        # 1. BTC 데이터(장세 판단용) 슬라이싱 (성능을 위해 인덱스 캐싱 가능하나 기본 필터링 복구)
        btc_setup_full = setup_data.get("KRW-BTC")
        if btc_setup_full is None: continue
        
        btc_setup_slice = btc_setup_full[btc_setup_full["time"] <= current_time]
        if btc_setup_slice.empty or len(btc_setup_slice) < 60: 
            continue
            
        regime = UpbitMarketData.market_regime(btc_setup_slice)
        if target_regime and regime != target_regime:
            continue
            
        # 2. 전체 티커 데이터 슬라이싱 (빈 데이터프레임 방지 필터링 복구)
        setup_slice = {t: df[df["time"] <= current_time] for t, df in setup_data.items() 
                       if not df[df["time"] <= current_time].empty}
        entry_slice = {t: df[df["time"] <= current_time] for t, df in entry_data.items() 
                       if not df[df["time"] <= current_time].empty}
        
        if not entry_slice: continue # 한 종목도 데이터가 없으면 스킵
        
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

class StrategyOptimizer:
    def __init__(self, days=7):
        self.days = days
        self.config_path = "data/optimized_params.json"
        self.search_space = {
            "VWAPReversion": {
                "entry.vwap_distance_pct": [-0.005, -0.01, -0.02],
                "entry.rsi_threshold": [35, 40, 45],
            },
            "Breakout": {
                "volume_multiplier": [1.8, 2.2, 2.5],
                "breakout_period": [10, 20]
            },
            # "MeanReversion": {
            #     "setup.rsi_threshold": [35, 40, 45],
            #     "entry.rsi_threshold": [25, 28, 32],
            #     "entry.volume_multiplier": [1.5, 2.0]
            # },
            # "PullbackTrend": {
            #     "setup.adx_threshold": [20, 25, 30],
            #     "entry.rsi_threshold": [35, 40, 45],
            #     "entry.volume_multiplier": [1.8, 2.2]
            # },
        }
        # 장세 목록
        self.regimes = ["bullish", "weakbullish", "ranging", "volatile", "recovery", "earlybreakout"]

    def run_full_backtest(self, config, tickers, setup_data, entry_data, timeline):
        """전체 장세 매핑과 파라미터를 사용하여 전체 백테스트를 수행합니다. (순차 실행)"""
        temp_db = f"data/opt_full_{os.getpid()}.db"
        pm = PortfolioManager(total_capital=1000000, db_path=temp_db)
        pm.allocate("full_test", 1000000)
        
        manager = ManagerAgent("full_test", pm)
        manager.broker = MockBroker(pm, agent_name="full_test")
        manager.notifier = MockNotifier()
        manager.execution_manager.broker = manager.broker
        manager.execution_manager.notifier = manager.notifier
        
        manager.strategy_map = config.get("strategy_map", manager.DEFAULT_STRATEGY_MAP)
        manager.strategy_manager.optimized_params = config.get("strategy_params", {})

        value_history = []
        for current_time in timeline:
            setup_slice = {t: df[df["time"] <= current_time] for t, df in setup_data.items() 
                           if not df[df["time"] <= current_time].empty}
            entry_slice = {t: df[df["time"] <= current_time] for t, df in entry_data.items() 
                           if not df[df["time"] <= current_time].empty}
            
            btc_setup = setup_slice.get("KRW-BTC")
            if btc_setup is None or len(btc_setup) < 60: continue
            
            regime = UpbitMarketData.market_regime(btc_setup)
            manager.execute_cycle(setup_slice, entry_slice, regime)
            value_history.append(pm.get_total_value("full_test"))

        summary = pm.get_portfolio_summary("full_test")
        mdd = 0
        if value_history:
            df_vals = pd.Series(value_history)
            mdd = ((df_vals - df_vals.cummax()) / df_vals.cummax()).min() * 100
            
        if os.path.exists(temp_db): os.remove(temp_db)
        
        return {
            "roi": summary.get("return_rate", 0),
            "pf": summary.get("profit_factor", 0),
            "mdd": mdd,
            "total_trades": summary.get("total_trades", 0)
        }

    def optimize(self, current_manager=None):
        logger.info(f"🚀 [Optimizer] {self.days}일 데이터기반 병렬 광역 최적화 시작...")
        
        # 1. 데이터 준비
        tickers = UpbitMarketData.get_dynamic_target_coins(20)
        end_time = datetime.now().astimezone()
        
        setup_data = {}
        entry_data = {}
        for t in tickers:
            s_df = fetch_and_prepare_historical_data(t, self.days, "minutes/60", end_time)
            e_df = fetch_and_prepare_historical_data(t, self.days, "minutes/15", end_time)
            if not s_df.empty and not e_df.empty:
                setup_data[t] = s_df
                entry_data[t] = e_df
        
        if "KRW-BTC" not in setup_data:
            logger.error("[Optimizer] BTC 데이터 확보 실패")
            return None

        timeline = entry_data["KRW-BTC"]["time"].dropna().tolist()
        
        # 2. 베이스라인 성과 측정 (현재 설정)
        baseline_performance = {"roi": 0, "pf": 0, "mdd": 0, "total_trades": 0}
        if current_manager:
            current_config = {
                "strategy_params": current_manager.strategy_manager.optimized_params,
                "strategy_map": current_manager.strategy_map
            }
            logger.info("📉 [Optimizer] 현재 설정 베이스라인 측정 중...")
            baseline_performance = self.run_full_backtest(current_config, tickers, setup_data, entry_data, timeline)

        # 3. 전략별 파라미터 최적화 (병렬 실행)
        best_versions = {}
        num_workers = max(1, os.cpu_count() - 1)
        
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            for strategy_name, grid in self.search_space.items():
                logger.info(f"🔍 [Optimizer] '{strategy_name}' 병렬 파라미터 탐색 중 (Workers: {num_workers})...")
                combinations = self._generate_grid(grid)
                
                # 병렬 태스크 구성
                tasks = [
                    (strategy_name, combo, tickers, setup_data, entry_data, timeline, None, self.regimes)
                    for combo in combinations
                ]
                
                best_res = None
                max_score = -9999
                
                for res in executor.map(_run_backtest_worker, tasks):
                    if res["score"] > max_score and res["total_trades"] > 0:
                        max_score = res["score"]
                        best_res = res
                
                if best_res:
                    best_versions[strategy_name] = self._nest_params(best_res["params"])
                    logger.info(f"✅ [Optimizer] '{strategy_name}' 최적 파라미터 확보 (Score: {best_res['score']:.2f})")

        # 4. 장세별 챔피언 선정 (병렬 실행)
        logger.info("🏆 [Optimizer] 장세별 챔피언 선정 병렬 리그 시작...")
        final_strategy_map = {}
        
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            league_tasks = []
            for r in self.regimes:
                for s_name in best_versions.keys():
                    params = self._flatten_params(best_versions[s_name])
                    league_tasks.append((s_name, params, tickers, setup_data, entry_data, timeline, r, self.regimes))
            
            # 장세별 결과 수집
            league_results = {} # {regime: [(s_name, score, roi)]}
            for res in executor.map(_run_backtest_worker, league_tasks):
                regime = res["target_regime"]
                if regime not in league_results: league_results[regime] = []
                league_results[regime].append((res["strategy_name"], res["score"], res["roi"]))
            
            for r, scores in league_results.items():
                scores.sort(key=lambda x: x[1], reverse=True)
                winners = [s[0] for s in scores[:2] if s[2] >= 0]
                if not winners: winners = ["VWAPReversion"]
                final_strategy_map[r] = winners
                logger.info(f"📍 [Regime: {r:12}] 챔피언: {winners}")

        # 5. 최종 제안 설정 및 성과 측정
        proposed_results = {"strategy_params": best_versions, "strategy_map": final_strategy_map}
        logger.info("📈 [Optimizer] 제안된 '전략 믹스' 최종 성과 측정 중...")
        optimized_performance = self.run_full_backtest(proposed_results, tickers, setup_data, entry_data, timeline)

        # 6. 결과 저장
        comparison = {
            "baseline": baseline_performance,
            "optimized": optimized_performance,
            "proposed_config": proposed_results,
            "timestamp": datetime.now().isoformat()
        }
        with open("data/pending_optimized_params.json", "w") as f:
            json.dump(comparison, f, indent=4)
        
        logger.info(f"💾 [Optimizer] 병렬 최적화 결과 저장 완료")
        return comparison

    def _generate_grid(self, grid):
        import itertools
        keys = list(grid.keys())
        values = list(grid.values())
        combinations = []
        for combo in itertools.product(*values):
            combinations.append(dict(zip(keys, combo)))
        return combinations

    def _nest_params(self, flat_params):
        nested = {}
        for k, v in flat_params.items():
            if "." in k:
                p, c = k.split(".")
                if p not in nested: nested[p] = {}
                nested[p][c] = v
            else:
                nested[k] = v
        return nested

    def _flatten_params(self, params, prefix=""):
        flat = {}
        for k, v in params.items():
            new_key = f"{prefix}{k}"
            if isinstance(v, dict):
                flat.update(self._flatten_params(v, f"{new_key}."))
            else:
                flat[new_key] = v
        return flat

if __name__ == "__main__":
    opt = StrategyOptimizer(days=7)
    opt.optimize()
