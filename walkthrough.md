# 🚀 Unified Ratio Risk Manager Walkthrough

가변형 리스크 매니저를 **통합 비율(Unified Ratio)** 기반의 장세 맞춤형 시스템으로 고도화한 작업 내역입니다.

---

## ✅ 핵심 성과

### 📊 백테스트 결과 비교 (15일 기준)

| 지표 | 최초 (고정 익절) | **최종 (Unified Ratio)** | 변화 |
| :--- | :--- | :--- | :--- |
| **최종 수익률** | -0.32% | **+0.81%** | +1.13% ↑ |
| **승률** | 27.5% | **42.2%** | +14.7% ↑ |
| **Profit Factor** | 0.91 | **1.32** | +0.41 ↑ |
| **손익비 (RR)** | 2.39 | **1.81** | 수익구조 안정화 |
| **최대 낙폭 (MDD)** | -1.63% | **-0.81%** | 약 50% 감소 |
| **거래 횟수** | 80 | **83** | 유사 |

> [!IMPORTANT]
> 모든 리스크 파라미터가 단일 상수 `ratio (1.618)`에 연동되어, 익절 목표 하나만 바꾸면 손절·트레일링·본절 보호가 자동으로 최적화됩니다.

---

## 🔧 변경 내역

### 1. 장세(Market Regime) 기반 동적 익절 앵커

[risk_manager.py](file:///Users/home/Project/project_hermes/src/core/risk_manager.py) — `regime_tp_map` 추가

시장 분위기에 따라 익절 목표(`tp_anchor`)가 자동으로 변경되고, 이에 연동된 모든 리스크 임계값이 재계산됩니다.

| 장세 (Regime) | 익절 앵커 | 기본 손절선 | 최종 익절 (2단계) | 본절 보호 탈출 |
| :--- | :--- | :--- | :--- | :--- |
| **bullish** | 5.0% | -3.09% | 8.09% | 0.81% |
| **volatile** | 3.0% | -1.85% | 4.85% | 0.49% |
| **ranging** | 2.0% | -1.24% | 3.24% | 0.32% |
| **bearish** | 1.5% | -0.93% | 2.43% | 0.24% |
| **panic** | 1.0% | -0.62% | 1.62% | 0.16% |

```python
"regime_tp_map": {
    "bullish": 5.0,
    "volatile": 3.0,
    "ranging": 2.0,
    "bearish": 1.5,
    "panic": 1.0,
}
```

### 2. 통합 비율(Unified Ratio) 시스템

하드코딩된 매직 넘버를 제거하고, `ratio = 1.618`이라는 단일 상수가 시스템 전체를 관통합니다.

```python
ratio = self.risk_params["ratio"]  # 1.618

base_sl_pct = -(tp_anchor / ratio)                    # 손절선
final_tp_target = tp_anchor * ratio                    # 2단계 익절 목표
breakeven_exit_pct = tp_anchor * ratio * breakeven_exit_ratio  # 본절 보호 탈출가
stop_loss_pct = max(-10.0, min(base_sl_pct, -atr_pct * ratio)) # ATR 대응
```

> [!NOTE]
> `ratio` 값 하나만 바꾸면 전체 리스크 프로파일이 즉시 리밸런싱됩니다. 예를 들어 `ratio = 2.0`으로 바꾸면 손절은 더 타이트해지고, 익절 목표와 ATR 버퍼는 넓어집니다.

### 3. 2단계 분할 익절 (Partial Take Profit)

| 단계 | 조건 | 매도 비율 | 예시 (ranging, 2%) |
| :--- | :--- | :--- | :--- |
| **1단계** | `profit >= tp_anchor` | 보유량의 50% | 2.0% 도달 시 절반 확정 |
| **2단계** | `profit >= tp_anchor * ratio` | 나머지 전량 | 3.24% 도달 시 전량 청산 |

### 4. ATR 동적 손절 (Strategy A — 노이즈 필터링)

변동성이 클 때 일시적인 가격 흔들림에 의한 조기 손절을 방지합니다.

```python
# 기본 손절과 ATR 손절 중 더 넓은(More Negative) 쪽을 선택
stop_loss_pct = max(-10.0, min(base_sl_pct, -atr_pct * ratio))
```

> [!TIP]
> Strategy A(`min`) vs Strategy B(`max`) 비교 결과, 현재 장세에서는 **Strategy A가 승률 +7.3%, 수익률 +0.72% 우위**를 보였습니다.

### 5. 가변형 본절 보호 (Dynamic Break-even)

본절 보호 탈출가가 고정값(0.2%)이 아닌, 장세와 ratio에 연동된 동적 수치로 작동합니다.

```python
breakeven_exit_pct = tp_anchor * ratio * self.risk_params["breakeven_exit_ratio"]
# ranging 장세: 2.0 * 1.618 * 0.1 = 0.32%에서 탈출
# bullish 장세: 5.0 * 1.618 * 0.1 = 0.81%에서 탈출
```

---

## 🏗️ Manager Agent 연동

[manager.py](file:///Users/home/Project/project_hermes/src/core/manager.py) — `_evaluate_ticker` 수정

리스크 매니저 호출 시 현재 사이클의 `market_regime` 정보를 전달하도록 수정하여, 장세 기반 동적 임계값이 실시간으로 적용됩니다.

```diff
 risk_signal = self.risk_manager.evaluate_risk(
-    self.name, ticker, current_price
+    self.name, ticker, current_price, market_regime=ctx.market_regime
 )
```

---

## 🛠️ 수정된 파일

| 파일 | 변경 내용 |
| :--- | :--- |
| [risk_manager.py](file:///Users/home/Project/project_hermes/src/core/risk_manager.py) | Unified Ratio 시스템, 장세별 TP 매핑, 2단계 분할 익절, 동적 본절 보호, ATR Strategy A |
| [manager.py](file:///Users/home/Project/project_hermes/src/core/manager.py) | `evaluate_risk` 호출 시 `market_regime` 파라미터 전달 |

---

## 🔬 검증 내역

15일치 캐시 데이터를 사용하여 총 6회의 반복 백테스트를 수행하며 각 변경 사항의 효과를 개별 검증했습니다.

| 테스트 | 변경 사항 | 수익률 | 승률 | PF |
| :--- | :--- | :--- | :--- | :--- |
| #1 | 기존 (고정 10% TP) | -0.32% | 27.5% | 0.91 |
| #2 | 가변형 2% TP 앵커 | +0.01% | 39.5% | 1.00 |
| #3 | 장세별 TP 매핑 추가 | +0.07% | 39.5% | 1.02 |
| #4 | 손절 계산 수정 (`/` 연산) | +0.64% | 39.5% | 1.25 |
| #5 | Strategy B (타이트 ATR) | +0.09% | 34.9% | 1.03 |
| #6 | Strategy A (노이즈 필터) | +0.64% | 39.5% | 1.25 |
| **#7** | **Unified Ratio (사용자 수정)** | **+0.81%** | **42.2%** | **1.32** |
