# 텔레그램 `/eval` 명령어 기능 구현 결과

텔레그램 봇을 통해 특정 티커의 최신 전략 및 시그널 상태를 조회할 수 있는 `/eval` 명령어를 구현했습니다.

## 변경 사항 요약

1. **[ManagerAgent](file:///Users/lamer/Project/stock/project_hermes/src/utils/manager.py#10-538) 상태 저장 추가 ([src/utils/manager.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/manager.py))**
   - 매 사이클 갱신마다 생성되는 종목별 분석 데이터(`ticker_stats`)를 인스턴스 변수(`self.last_ticker_stats`)에 최신본으로 유지하도록 수정했습니다.

2. **명령어 처리 핸들러 추가 ([src/utils/command_handler.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/command_handler.py))**
   - 큐에 [eval](file:///Users/lamer/Project/stock/project_hermes/src/interfaces/telegram_listener.py#126-145) 명령어가 들어왔을 때 이를 처리하는 [_handle_eval](file:///Users/lamer/Project/stock/project_hermes/src/utils/command_handler.py#141-166) 메서드를 구현했습니다.
   - `self.manager.last_ticker_stats`에 접근하여, 사용자가 요청한 티커의 정보를 추출하고 `ManagerAgent._send_cycle_report`의 `# 3. 전략별 코인 사항` 영역과 동일한 형식(`• {t} [{r}]: {s} → {st}\n  └ {sr}`)으로 포맷팅하여 텔레그램으로 즉시 회신합니다.

3. **텔레그램 리스너 연동 ([src/interfaces/telegram_listener.py](file:///Users/lamer/Project/stock/project_hermes/src/interfaces/telegram_listener.py))**
   - `/eval [티커]` 메시지를 파싱하여 [CommandQueue](file:///Users/lamer/Project/stock/project_hermes/src/utils/command_handler.py#12-342)에 큐잉할 [cmd_eval](file:///Users/lamer/Project/stock/project_hermes/src/interfaces/telegram_listener.py#126-145) 비동기 함수를 추가했습니다.
   - `/help` 메뉴에 명령어에 대한 안내를 추가하고, Telegram Application에 CommandHandler로 등록했습니다.

## 테스트 및 검증 방법
현재 실행 중인 봇에서 다음 명령어를 입력하여 봇을 재가동(`명령 접수 완료 후 텔레그램 내 /restart` 혹은 콘솔 재시작)한 뒤 테스트해 보세요.

```
/eval BTC
```

정상적으로 입력된 경우 `✅ eval 명령 접수 (KRW-BTC)` 메시지가 나타나며 곧이어 해당 티커의 모니터링 분석 결과가 메시지로 회신됩니다.
*(만약 최근 사이클의 분석 결과가 없다면 `최근 분석 데이터가 없습니다` 라고 회신됩니다.)*
