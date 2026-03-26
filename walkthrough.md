# Hermes Core Architecture 리팩토링 완료 보고서

유지보수 확장성 개선 및 데이터 흐름 명확화를 위한 핵심 리팩토링이 성공적으로 완료되었습니다.

## 주요 변경 사항

### 1. 매니저 파이프라인 구조화 ([ManagerAgent](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#13-482))
[execute_cycle](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#62-97)의 거대 모놀리스 로직을 4단계의 명확한 파이프라인으로 분해했습니다.
- [_build_cycle_context](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#102-140): 사이클에 필요한 모든 상태(현금, 보유량, regime)를 [CycleContext](file:///Users/lamer/Project/stock/project_hermes/src/core/models.py#38-51) DTO에 집약
- [_evaluate_and_execute_sells](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#248-309): 종목별 평가 수행. 리스크/전략 매도 시그널은 즉시 실행, 매수 시그널은 후보군에 수집
- [_select_and_execute_buy](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#310-347): 수집된 후보 중 최적(확신도 기준) 1건만 선별하여 매수
- [_finalize_cycle](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#348-374): 리포트 전송 및 상태 영속화

### 2. 브로커 ↔ 마켓 데이터 책임 분리
- [UpbitBroker](file:///Users/lamer/Project/stock/project_hermes/src/broker/broker_api.py#12-296)는 이제 순수하게 **매매(Execution)** 책임만 가집니다.
- 시세 조회, 지표 계산, Regime 판단은 [UpbitMarketData](file:///Users/lamer/Project/stock/project_hermes/src/data/market_data.py#11-394)를 직접 호출하도록 [main.py](file:///Users/lamer/Project/stock/project_hermes/src/main.py)와 [ManagerAgent](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#13-482)를 수정했습니다.
- 이로써 `KISBroker`(주식) 등 새로운 브로커 추가 시 마켓 데이터 로직을 중복 구현할 필요가 없어졌습니다.

### 3. 시장 확장 대비 리네이밍 (`crypto_manager`)
- 주식 시장 확장을 고려하여 기존 `"manager"` 에이전트 이름을 `"crypto_manager"`로 일괄 변경했습니다.
- **자동 마이그레이션**: DB에 기존 `"manager"` 이름으로 저장된 데이터는 `PortfolioManager.load_state()` 호출 시 자동으로 `"crypto_manager"`로 업그레이드됩니다.
- [command_handler.py](file:///Users/lamer/Project/stock/project_hermes/src/communication/command_handler.py) 등에서 하드코딩된 이름을 제거하고 `self.manager.name`을 참조하도록 개선하여 다중 에이전트 대응이 가능해졌습니다.

### 4. 데이터 흐름 타입 안전화 (DTO 입)
- [src/core/models.py](file:///Users/lamer/Project/stock/project_hermes/src/core/models.py)를 신설하여 [TickerEvaluation](file:///Users/lamer/Project/stock/project_hermes/src/core/models.py#11-36), [CycleContext](file:///Users/lamer/Project/stock/project_hermes/src/core/models.py#38-51) dataclass를 정의했습니다.
- 무타입 딕셔너리(`ticker_stats`) 대신 명확한 속성을 가진 객체를 사용함으로써 개발 편의성과 안정성을 높였습니다.

### 5. 순환 의존 및 결합도 제거
- [PortfolioManager](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py#11-631)에서 [ManagerAgent](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py#13-482) 객체와 `TelegramNotifier`에 대한 직접 참조를 제거했습니다.
- 에이전트 이름(문자열)만 사용하여 동기화하도록 개선하여 패키지 간 순환 참조 문제를 원천 차단했습니다.

---

## 검증 결과

### 1. Import 및 구조 검증
`Python -c` 명령을 통해 모든 핵심 모듈이 정상적으로 import 됨을 확인했습니다.
```bash
python -c "from src.core.manager import ManagerAgent; ..." # All imports OK
```

### 2. 단위 테스트 (Pytest)
기존 [db.py](file:///Users/lamer/Project/stock/project_hermes/src/data/db.py), [portfolio_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py), [broker_api.py](file:///Users/lamer/Project/stock/project_hermes/src/broker/broker_api.py) 기반의 테스트 6건을 모두 통과했습니다.
- [tests/test_portfolio_manager.py](file:///Users/lamer/Project/stock/project_hermes/tests/test_portfolio_manager.py): 4건 통과 (할당, 매수 기록, 매도 기록, DB 지속성)
- [tests/test_broker_api.py](file:///Users/lamer/Project/stock/project_hermes/tests/test_broker_api.py): 2건 통과 (호가 단위 포맷팅, 수량 포맷팅)

### 3. 가상환경(venv) 지원
사용자의 요청에 따라 `venv` 내에서 `pytest`와 `pytest-mock`을 구성하고 검증을 완료했습니다.

---

## 향후 확장 제언
- **Multi-Agent 시스템**: 이제 [main.py](file:///Users/lamer/Project/stock/project_hermes/src/main.py)에서 `stock_manager`를 추가로 인스턴스화하고 동일한 [PortfolioManager](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py#11-631)와 [ExecutionManager](file:///Users/lamer/Project/stock/project_hermes/src/core/execution_manager.py#6-318)를 사용하여 주식 매매를 병행할 수 있는 기반이 마련되었습니다.
- **전략 가상화**: [TickerEvaluation](file:///Users/lamer/Project/stock/project_hermes/src/core/models.py#11-36)을 통해 전략별 성과 측정이 더욱 용이해졌으므로, 실시간 성과에 따른 전략 가중치 동적 조절 기능을 추가할 수 있습니다.
