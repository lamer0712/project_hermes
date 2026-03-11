# 자동화 실행 스크립트 아키텍처 (Execution Script Architecture)

본 시스템은 Markdown 문서들을 **Single Source of Truth (SSOT)**로 활용하며, 이를 읽고 각각의 Agent(LLM)에게 프롬프트를 전달하여 그 결과를 다시 Markdown으로 업데이트하는 Python 기반의 실행 스크립트로 동작합니다.

## 디렉토리/모듈 구조 (제안)

```text
/investment_firm
 ├── /src
 │   ├── main.py                # 전체 시스템 진입점 (Entry Point), 스케줄러 실행
 │   ├── /agents                # Agent 클래스 모음
 │   │   ├── base_agent.py      # LLM API 호출 래퍼, MD 읽기/쓰기 공통 기능
 │   │   ├── manager.py         # Manager Agent 로직
 │   │   ├── investor.py        # Investment Agent 로직
 │   │   └── global_risk.py     # Global Risk (+Kill Switch) 로직
 │   ├── /utils
 │   │   ├── markdown_io.py     # 마크다운 파싱 및 생성 유틸
 │   │   ├── market_data.py     # 외부 시세 데이터(Yahoo Finance, Binance 코인 등) 패치 플러그인
 │   │   └── broker_api.py      # 실제 매매 체결용 브로커 및 암호화폐 거래소 API 연동
 ├── /rules                     # 시스템 프롬프트 및 가이드 문서들
 ├── /manager                   # 매니저 상태 파일
 ├── /agents                    # 개별 투자 에이전트 폴더들
 └── /reports                   # 보고서 파일들
```

## 핵심 컴포넌트 동작 방식

### 1. `markdown_io.py`
- Markdown 문서를 파싱(Parsing)하여 Python 객체(Dict/List)로 변환하거나, 시스템의 실행 결과를 Markdown 형식으로 디스크에 저장(Dump)합니다.
- 예: `trades.md`의 최하단에 새로운 매매 기록을 Append 방식으로 추가.

### 2. `base_agent.py`
- LangChain이나 OpenAI API를 래핑한 Base Class.
- **System Prompt**(`rules/prompt_*.md`), **State**(`agents/*/strategy.md`, `trades.md`), **Market Data** 세 가지를 조합하여 LLM에 프롬프트를 전송합니다.
- LLM의 응답(JSON 포맷 선호)을 받아 파싱 후, `utils`를 통해 실제 액션(파일 쓰기, 매매)을 수행합니다.

### 3. 스케줄러 (`main.py`)
- Python 내장 `schedule` 라이브러리 또는 `cron`을 활용.
- **High Frequency Loop (매 1분/5분) - $0 비용 파이썬 전용**:
  - `global_risk.py` 실행 -> 시장 데이터 확인, VaR/MDD 한도 계산 -> Kill Switch 조건 검사.
  - `investor.py` 실행 -> `execute_trade_by_rule()`: 마크다운 파일의 파라미터를 파이썬 코드가 읽어 기계적으로 주문 실행. LLM 호출 전혀 없음.
- **Hourly Loop (매시 30분) - LLM 기상 주기**:
  - `manager.py` 실행 -> 투자 에이전트 성과 취합, HR 의사결정(해고/채용), 포트폴리오 변경.
  - `investor.py` 실행 -> `review_and_update_strategy()`: LLM이 지난 1시간의 타격 성과를 분석하고 `strategy.md` 내부의 JSON 파라미터를 수정함.
- **Daily Loop (매일 자정)**:
  - Shadow Agent (Audit) 배치 스크립트 실행, 성과 요약 보고서 작성.

## 에이전트 통신 브릿지 (Agent-to-Agent Communication)
- 이 시스템에서 에이전트 간의 **직접적인 메모리 공유 통신은 없습니다.**
- 모든 통신은 **공용 마크다운 파일**을 매개로 하는 비동기적(Asynchronous) 읽기/쓰기로만 이루어집니다.
  - Manager가 Investment Agent를 평가할 때, API로 묻는 것이 아니라 Agent가 쓴 `performance.md`와 `trades.md`를 열어봄으로써 소통합니다.
  - 이 방식이 이 시스템의 최고 장점인 **"영속성 확보"**와 **"환각 방지"**의 핵심입니다.
