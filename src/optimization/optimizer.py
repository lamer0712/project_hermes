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
from src.optimization.genetic_optimizer import GeneticOptimizer

from src.optimization.backtest_worker import _run_backtest_worker

class StrategyOptimizer:
    def __init__(self, days=7):
        self.days = days
        self.config_path = "data/optimized_params.json"
        self.search_space = {
            "VWAPReversion": {
                "entry.vwap_distance_pct": [-0.005, -0.007, -0.01],
                "entry.rsi_threshold": [38, 42, 45],
            },
            "Breakout": {
                "entry.volume_multiplier": [1.8, 2.2, 2.5],
            },
            "MeanReversion": {
                "setup.rsi_threshold": [35, 40, 45],
                "entry.rsi_threshold": [25, 28, 30],
                "entry.volume_multiplier": [1.5, 1.8, 2.0]
            },
            "PullbackTrend": {
                "setup.adx_threshold": [25, 28, 30],
                "entry.rsi_threshold": [40, 45],
            },
            "BollingerSqueeze": {
                "setup.bw_threshold": [0.05, 0.08],
                "entry.volume_multiplier": [1.5, 2.0],
            }
        }
        # 장세 목록 (매수 허용: 최적화 진행)
        self.regimes = ["bullish", "weakbullish", "ranging", "volatile", "recovery", "earlybreakout"]
        # 매수 차단 장세 (최적화 생략)
        self.no_trade_regimes = [] #"bearish", "panic", "stagnant"]

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

    def _split_data_walk_forward(self, timeline: list, train_ratio: float = 0.7):
        """데이터를 상호 배타적인 학습/검증 구간으로 분할합니다."""
        split_idx = int(len(timeline) * train_ratio)
        train_timeline = timeline[:split_idx]
        val_timeline = timeline[split_idx:]
        return train_timeline, val_timeline

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
                combinations = self._generate_grid(grid)
                
                # 🚀 [추가] 데이터 분할 (Walk-forward 준비)
                train_timeline, val_timeline = self._split_data_walk_forward(timeline)
                
                # 파라미터 공간이 크면 유전 알고리즘 사용, 작으면 그리드 서치
                if len(combinations) > 50:
                    logger.info(f"🧬 [Optimizer] '{strategy_name}' 공간이 큼({len(combinations)}). 유전 알고리즘 모드 가동...")
                    ga = GeneticOptimizer(grid, pop_size=max(10, min(30, len(combinations)//2)), generations=4)
                    best_ind = ga.evolve(strategy_name, tickers, setup_data, entry_data, train_timeline, None, self.regimes)
                    best_params = best_ind.params
                    best_score = best_ind.fitness
                else:
                    logger.info(f"🔍 [Optimizer] '{strategy_name}' 병렬 파라미터 그리드 탐색 중...")
                    tasks = [(strategy_name, combo, tickers, setup_data, entry_data, train_timeline, None, self.regimes)
                             for combo in combinations]
                    
                    best_res = None
                    max_score = -9999
                    for res in executor.map(_run_backtest_worker, tasks):
                        if res["score"] > max_score and res["total_trades"] > 0:
                            max_score = res["score"]
                            best_res = res
                    
                    if best_res:
                        best_params = best_res["params"]
                        best_score = max_score
                    else:
                        continue

                # 🚀 [추가] 검증 단계 (Validation / Forward Test)
                logger.info(f"🧪 [Optimizer] '{strategy_name}' 검증 구간(Forward) 테스트 중...")
                val_res = _run_backtest_worker((strategy_name, best_params, tickers, setup_data, entry_data, val_timeline, None, self.regimes))
                
                # 최종 선발: 학습과 검증 점수의 가중 합산 (강건성 확보)
                final_score = (best_score * 0.4) + (val_res["score"] * 0.6)
                
                best_versions[strategy_name] = self._nest_params(best_params)
                logger.info(f"✅ [Optimizer] '{strategy_name}' 최종 선발 (Train: {best_score:.2f}, Val: {val_res['score']:.2f}, Final: {final_score:.2f})")

        # 4. 매수 허용 장세별 챔피언 선정 (병렬 실행)
        logger.info("🏆 [Optimizer] 장세별 챔피언 선정 병렬 리그 시작...")
        final_strategy_map = {r: [] for r in self.no_trade_regimes} # 매수 금지 장세는 빈 배열 할당
        
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
