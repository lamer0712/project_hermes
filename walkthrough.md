# Phase 2 & 3: 정밀 리스크 관리 및 진화형 최적화 엔진 구현 완료

봇의 리스크 관리 수준을 금융 공학적 수준으로 끌어올리고, 최적화 엔진을 전수 조사 방식(Grid Search)에서 진화형 유전 알고리즘(Genetic Algorithm)으로 업그레이드했습니다.

## ⚖️ Phase 2: 정밀 리스크 관리 (Risk & Capital)

### 1. 켈리 공식 기반 동적 포지션 사이징
- **핵심 로직**: 각 전략의 과거 승률($p$)과 손익비($b$)를 실시간 추적하여, 켈리 공식($f^* = \frac{pb - q}{b}$)에 따른 최적 투자 비중을 산출합니다.
- **안전 장치**: `Fractional Kelly(0.5)` 및 `Max Cap(0.2)`을 적용하여 급격한 비중 변화를 방지하고 안정성을 확보했습니다.
- **위치**: [manager.py:L400-420](file:///Users/home/Project/project_hermes/src/core/manager.py)

### 2. 종목 상관관계 기반 분산 투자 (Diversification)
- **핵심 로직**: 매수 진입 전, 기존 보유 종목들과의 상관관계를 분석합니다. 상관계수가 0.8 이상인(커플링이 강한) 종목은 리스크 분산을 위해 진입을 차단합니다.
- **위치**: [market_data.py:L491-530](file:///Users/home/Project/project_hermes/src/data/market_data.py) 및 [manager.py:L385-395](file:///Users/home/Project/project_hermes/src/core/manager.py)

---

## 🧬 Phase 3: 최적화 엔진의 진화 (Evolutionary Search)

### 1. 유전 알고리즘 최적화 엔진 (Genetic Optimizer)
- **개요**: 방대한 파라미터 조합 중 우수한 개체들을 교차(Crossover) 및 변이(Mutation)시켜 최적해를 효율적으로 찾아냅니다.
- **특징**: 파라미터 공간이 50개 이상일 경우 자동으로 GA 모드가 활성화되어 연산 효율을 극대화합니다.
- **위치**: [genetic_optimizer.py](file:///Users/home/Project/project_hermes/src/optimization/genetic_optimizer.py)

### 2. 전진 분석 검증 (Walk-forward Validation)
- **핵심 로직**: 데이터를 `학습(70%)`과 `검증(30%)` 구간으로 분할합니다. 학습 구간에서 찾은 파라미터가 검증(처음 보는 데이터) 구간에서도 유효한지 테스트하여 '과적합'을 방지합니다.
- **최종 선발 기준**: `학습 점수(40%) + 검증 점수(60%)`의 가중 합산을 통해 실전에 강한 파라미터를 선별합니다.
- **위치**: [optimizer.py:L174-245](file:///Users/home/Project/project_hermes/src/optimization/optimizer.py)

---

## 📊 결과 및 확인 방법

### 1. 로그 확인
최적화 실행 시(`python src/main.py` 후 `/optimize` 명령), 다음과 같은 진화형 로그를 확인하실 수 있습니다:
```text
🧬 [GA Optimizer] 'BreakoutStrategy' 진화형 최적화 시작...
🧬 [Gen 1/4] Best Score: 12.50 | ROI: 5.20%
✅ [Optimizer] 'BreakoutStrategy' 최종 선발 (Train: 14.2, Val: 10.5, Final: 11.98)
```

### 2. DB 확인
`data/portfolio.db`의 `portfolios.strategy_stats` 컬럼을 통해 전략별 누적 성과가 기록되는 것을 확인할 수 있습니다.
