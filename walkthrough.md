# 5-Minute Opening Scalping Strategy

## 개요
오전 9시 30분~35분 KST 시가를 기준으로 고가/저가를 파악한 뒤, 5분봉 기준 돌파와 리테스트가 발생하면 진입하는 전용 스캘핑 전략을 구현했습니다. 이를 위해 09:35 ~ 10:30 구간에만 작동하는 전용 5분 간격 스케줄러를 추가하고, 포트폴리오 매니저와 리스크 매니저가 고정된 커스텀 지정가 익절/손절을 실시간으로 추적하도록 안전장치를 마련했습니다.

## 구현 상세 내역

### 1. [OpeningScalpStrategy](file:///Users/lamer/Project/stock/project_hermes/src/strategies/opening_scalp.py#6-109) 구현
- 파일: [src/strategies/opening_scalp.py](file:///Users/lamer/Project/stock/project_hermes/src/strategies/opening_scalp.py) 
- 매 전략 사이클마다 과거 12시간 내 가장 최근의 00:30 UTC(KST 09:30) 5분 캔들을 탐색하여 기준봉으로 설정합니다.
- 기준봉의 고가(High), 저가(Low), 중간값(Midpoint)을 산출합니다.
- 5분 캔들 데이터에서 **고가 돌파(Breakout) -> 리테스트(Pullback) -> 고가 위 종가 마감(Confirmation)** 3단계의 패턴이 모두 확인되었을 때만 100% 비중으로 `BUY` 시그널을 발생시킵니다.
- 당일 이미 진입한 코인은 다시 진입하지 않도록 클래스 내부 로직(`_traded_today`)으로 방어합니다.
- BUY 시그널 반환 시, 커스텀 프로퍼티 `custom_sl_price`(중간값)와 `custom_tp_price`(진입가 + 2 * 리스크 폭)를 계산해 스윙/스캘핑용 고정 지정가로 전달합니다.

### 2. 고정가(Absolute Price) 손익절 파이프라인 연계
- 기존 시스템은 비율(%) 기반의 트레일링 스탑과 일반 익절/손절만 지원했습니다. 이번 작업을 통해 코인 보유 내역(Holding Metadata)에 특정 절대 가격(`custom_sl_price`, `custom_tp_price`)을 저장할 수 있도록 파이프라인을 확장했습니다.
- [portfolio_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py): 메타데이터에 커스텀 가격을 기록/복원할 수 있도록 DB 싱크 및 로드 로직을 개선했습니다.
- [risk_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/risk_manager.py): 실시간 WebSocket 틱이 반영될 때나 15분 정규 사이클이 돌 때, 보유 코인에 지정 손익절가가 있다면 기존 비율 로직보다 **최우선수위로 검사하여 즉시 즉각적인 시장가 매도**(`SELL`)를 수행합니다.

### 3. 메인 스케줄러(5분 루프) 추가 및 1일 1종목 제한
- 파일: [src/main.py](file:///Users/lamer/Project/stock/project_hermes/src/main.py)
- 기존의 매 정각, 15분, 30분, 45분 마다 도는 [execute_trading_cycle](file:///Users/lamer/Project/stock/project_hermes/src/main.py#61-107) 메인 루프는 그대로 유지합니다.
- 반면, 오전 9시 35분부터 10시 30분까지는 **5분 간격 단위**로 오직 스캘핑 조건만 빠르고 정확하게 파악하는 [execute_scalp_cycle](file:///Users/lamer/Project/stock/project_hermes/src/main.py#108-168) 함수가 백그라운드 스케줄러로 동작합니다.
- **1일 1종목 진입 제한**: 포트폴리오 매니저가 당일 KST 기준으로 [OpeningScalp](file:///Users/lamer/Project/stock/project_hermes/src/strategies/opening_scalp.py#6-109) 전략으로 구매 기록이 단 한 건이라도 발생했는지 실시간 데이터베이스([has_traded_strategy_today](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py#651-666))를 통해 감시합니다. 하나라도 매수 체결이 이루어지면 당일 남은 스캘핑 스케줄은 즉시 패스되도록 통제하여 시스템 재시작과 상관없이 **철저하게 1일 1코인 제한**을 수행합니다.

## Validation Results
- 빌드/실행 테스트 진행: [main.py](file:///Users/lamer/Project/stock/project_hermes/src/main.py)를 실행하여 문법 오류 확인을 마쳤으며, 텔레그램 봇 리스너 확장과 5분루프 인젝션, 메인 루프와의 데이터 간섭 없이 시스템이 정상적으로 초기화(& WebSocket HookING)되는 것을 검증했습니다.
- 이후 KST 오전 9시 35분이 되면 [execute_scalp_cycle](file:///Users/lamer/Project/stock/project_hermes/src/main.py#108-168)이 스스로 5분 캔들 데이터를 불러와 스캘핑 기회를 찾고 진입합니다.
