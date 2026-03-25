# 작업 완료 보고서: KRW-ATH 매도 주문 취소 문제 해결

`KRW-ATH` 매도 주문이 슬리피지 제한으로 인해 취소되었던 문제를 분석하고, 이를 방지하기 위한 개선 작업을 완료했습니다.

## 변경 사항 요약

### 1. 슬리피지 허용 한도 상향 (1.0%)
- **[broker_api.py](file:///Users/home/Project/project_hermes/src/broker/broker_api.py)**
- 기본 슬리피지 허용치를 **0.5%에서 1.0%로 상향**했습니다.
- 변동성이 큰 코인이나 호가가 얇은 종목에서도 시장가 주문이 `Limit IOC`로 변환될 때 체결 확률이 높아지도록 개선했습니다.
- 주문 시 계산된 제한 가격(`P`)과 허용 비율(`Tol`)을 로그에 기록하여 추후 분석이 용이하게 했습니다.

### 2. 주문 취소 시 부분 체결 처리 개선
- **[execution_manager.py](file:///Users/home/Project/project_hermes/src/core/execution_manager.py)**
- 주문 상태가 [cancel](file:///tmp/test_order_logic.py#20-51)이더라도 **이미 체결된 수량(`executed_volume`)이 있다면 이를 무시하지 않고 포트폴리오에 기록**하도록 수정했습니다.
- 이전에는 [cancel](file:///tmp/test_order_logic.py#20-51) 상태인 경우 모든 체결 내역을 무시했으나, 이제는 부분적으로라도 수익을 확보할 수 있도록 보장합니다.

## 검증 결과

### 모의 테스트(Mock Test) 통과
[/tmp/test_order_logic.py](file:///tmp/test_order_logic.py)를 통해 다음 두 가지 시나리오를 검증 완료했습니다.
- **부분 체결 시나리오**: [cancel](file:///tmp/test_order_logic.py#20-51) 상태의 주문에서 체결된 400개 수량이 `PortfolioManager`에 정상 기록됨을 확인.
- **슬리피지 계산 시나리오**: 현재가 11.9일 때 1.0% 슬리피지가 적용되어 제한 가격 11.8로 주문이 생성됨을 확인.

```text
[2026-03-25 21:17:08] [INFO] [ExecutionManager] 주문 취소되었으나 부분 체결됨: KRW-ATH (400.0 sell)
[2026-03-25 21:17:08] [INFO] [Broker API] 슬리피지 보호 활성화: ask KRW-ATH (Limit IOC, P: 11.8, V: 1000), CP: 11.9, Tol: 1.0%
OK (2 tests passed)
```

## 향후 모니터링
- 향후 "강제 익절" 또는 "손절" 시그널 발생 시, 로그에서 `Tol: 1.0%`가 적용된 `Limit IOC` 주문이 원활하게 체결되는지 확인 바랍니다.
