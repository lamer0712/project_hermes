import random
import copy
import numpy as np
from typing import List, Dict, Any, Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from src.utils.logger import logger
from src.optimization.backtest_worker import _run_backtest_worker

class Individual:
    def __init__(self, params: Dict[str, Any]):
        self.params = params
        self.fitness = -float('inf')
        self.stats = {}

    def __repr__(self):
        return f"Individual(score={self.fitness:.2f}, params={self.params})"

class GeneticOptimizer:
    """유전 알고리즘 기반 파라미터 최적화 엔진"""
    
    def __init__(self, 
                 search_space: Dict[str, List[Any]], 
                 pop_size: int = 20, 
                 generations: int = 5,
                 mutation_rate: float = 0.2,
                 crossover_rate: float = 0.7,
                 elitism_count: int = 2):
        self.search_space = search_space
        self.pop_size = pop_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elitism_count = elitism_count
        self.population: List[Individual] = []

    def _create_random_individual(self) -> Individual:
        """무작위 유전자(파라미터)를 가진 개체 생성"""
        params = {}
        for param, values in self.search_space.items():
            params[param] = random.choice(values)
        return Individual(params)

    def _initialize_population(self):
        """초기 인구 구성"""
        self.population = [self._create_random_individual() for _ in range(self.pop_size)]

    def _mutate(self, individual: Individual) -> Individual:
        """변이 연산: 일정 확률로 특정 유전자를 다른 값으로 변경"""
        new_params = copy.deepcopy(individual.params)
        for param in new_params:
            if random.random() < self.mutation_rate:
                new_params[param] = random.choice(self.search_space[param])
        return Individual(new_params)

    def _crossover(self, parent1: Individual, parent2: Individual) -> Individual:
        """교차 연산: 두 부모의 유전자를 조합하여 자손 생성 (Uniform Crossover)"""
        if random.random() > self.crossover_rate:
            return copy.deepcopy(random.choice([parent1, parent2]))
        
        child_params = {}
        for param in self.search_space:
            child_params[param] = parent1.params[param] if random.random() < 0.5 else parent2.params[param]
        return Individual(child_params)

    def _select_parent(self) -> Individual:
        """토너먼트 선택 방식"""
        tournament_size = 3
        participants = random.sample(self.population, tournament_size)
        return max(participants, key=lambda x: x.fitness)

    def evolve(self, 
               strategy_name: str, 
               tickers: list, 
               setup_data: dict, 
               entry_data: dict, 
               timeline: list, 
               target_regime: str, 
               regimes: list) -> Individual:
        """메인 유전 알고리즘 실행 루프"""
        
        logger.info(f"🧬 [GA Optimizer] '{strategy_name}' 진화형 최적화 시작 (Pop:{self.pop_size}, Gen:{self.generations})")
        self._initialize_population()

        for gen in range(self.generations):
            # 1. 적합도 평가 (병렬 처리)
            with ProcessPoolExecutor(max_workers=max(1, os.cpu_count() - 1)) as executor:
                futures = {}
                for ind in self.population:
                    if ind.fitness == -float('inf'): # 아직 평가되지 않은 개체만
                        args = (strategy_name, ind.params, tickers, setup_data, entry_data, timeline, target_regime, regimes)
                        futures[executor.submit(_run_backtest_worker, args)] = ind
                
                for future in as_completed(futures):
                    ind = futures[future]
                    try:
                        res = future.result()
                        ind.fitness = res["score"]
                        ind.stats = res
                    except Exception as e:
                        logger.error(f"[GA Error] 평가 오류: {e}")
                        ind.fitness = -100 # 페널티

            # 인구 정렬 (우수 개체 순)
            self.population.sort(key=lambda x: x.fitness, reverse=True)
            best_in_gen = self.population[0]
            logger.info(f"🧬 [Gen {gen+1}/{self.generations}] Best Score: {best_in_gen.fitness:.2f} | ROI: {best_in_gen.stats.get('roi',0):.2f}%")

            if gen == self.generations - 1:
                break

            # 2. 다음 세대 인구 생성
            new_population = []
            
            # Elitism: 상위 개체 보존
            new_population.extend(copy.deepcopy(self.population[:self.elitism_count]))
            
            # 나머지는 선택/교차/변이로 채움
            while len(new_population) < self.pop_size:
                p1 = self._select_parent()
                p2 = self._select_parent()
                child = self._crossover(p1, p2)
                child = self._mutate(child)
                new_population.append(child)
                
            self.population = new_population

        return self.population[0]

import os # os.cpu_count 사용 위해 추가
