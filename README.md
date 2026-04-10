# 🏛️ Project Hermes (프로젝트 헤르메스)

[![Python Support](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Exchange](https://img.shields.io/badge/Exchange-Upbit-informational)](https://upbit.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Project Hermes**는 업비트(Upbit) 거래소를 위한 AI 기반 고성능 자동 매매 시스템입니다. 시장의 흐름(Market Regime)을 분석하여 최적의 전략을 선택하고, ~~LLM(Gemini/Ollama)을 통한 2차 검증을 통해 신중하고 정교한~~ 트레이딩을 수행합니다.

---

## ✨ 핵심 기능 (Core Features)


- 📉 **Multi-Strategy Engine**: 시장 상황에 따라 VWAP Reversion, Breakout, Mean Reversion 등 다양한 기술적 전략을 유연하게 전환합니다.
- 🧠 **Dynamic Regime Detection**: 현재 시장이 상승장(Bullish), 하락장(Bearish), 횡보장(Ranging) 혹은 변동성 장세(Volatile)인지 실시간으로 판단합니다.
- 🛡️ **Advanced Risk Management**: ATR 기반의 동적 손절(Stop Loss), 목표가(Take Profit) 설정 및 웹소켓(WebSocket)을 통한 실시간 리스크 훅(Real-time Risk Hook)을 지원합니다.
- 📱 **Telegram Control Center**: 텔레그램 봇을 통해 실시간 리포트 수신 및 원격 명령(Sync, Status 확인 등)이 가능합니다.
- 📊 **Visual Dashboard**: Flask 기반의 차트 서버를 통해 현재 전략의 타점과 시장 데이터를 시각적으로 확인합니다.


---

## 🛠️ 지원 전략 (Supported Strategies)

| 전략명 | 설명 | 추천 장세 |
| :--- | :--- | :--- |
| **VWAP Reversion** | 거래량 가중 평균 가격(VWAP) 기반의 회귀 전략 | Recovery, Ranging |
| **Breakout** | 주요 저항선을 돌파하는 모멘텀 활용 전략 | Bullish, Early Breakout |
| **Mean Reversion** | 볼린저 밴드 등을 활용한 과매수/과매도 기반 회귀 전략 | Ranging |
| **Opening Scalp** | 장 초반 변동성을 활용한 스캘핑 전략 | Volatile |
| **Pullback Trend** | 추세 상승 중 눌림목을 공략하는 전략 | Bullish |

---

## 🚀 시작하기 (Quick Start)

### 1. 환경 준비
- **Python 3.12+** 가 설치되어 있어야 합니다.
- `TA-Lib` 라이브러리가 필요합니다. ([설치 가이드](https://github.com/mrjbq7/ta-lib))

### 2. 설치
```bash
# 저장소 클론
git clone https://github.com/your-repo/project_hermes.git
cd project_hermes

# 가상환경 생성 및 활성화
python3.12 -m venv venv
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

### 3. 설정 (`.env`)
`.env.example` 파일을 복사하여 `.env` 파일을 생성하고 필요한 API 키를 입력합니다.

```env
UPBIT_OPEN_API_ACCESS_KEY=your_access_key
UPBIT_OPEN_API_SECRET_KEY=your_secret_key
GEMINI_API_KEY=your_gemini_api_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. 실행
```bash
# 메인 시스템 실행
python src/main.py

# (선택) 차트 서버 실행
python chart_server.py
```

---

## 📱 텔레그램 명령 (Telegram Commands)

- `/sync`: 현재 계좌 잔고와 포트폴리오를 동기화합니다.
- `/status`: 현재 홀딩 중인 종목 수익률과 시스템 상태를 확인합니다.
- `/report`: 마지막 투자 주기의 상세 리포트를 출력합니다.
- `/eval [Ticker]`: 특정 종목에 대한 즉시 분석을 수행합니다.

---

## ⚠️ 면책 조항 (Disclaimer)

이 소프트웨어는 정보 제공 목적으로만 제공됩니다. 암호화폐 투자는 높은 리스크를 수반하며, 모든 투자 결정과 그에 따른 결과는 사용자 본인에게 책임이 있습니다. 개발자는 이 소프트웨어의 사용으로 인해 발생하는 어떠한 금전적 손실에 대해서도 책임을 지지 않습니다.

---

**Project Hermes** - *Steer your trading with AI intelligence.*
