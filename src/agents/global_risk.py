import os
import json
import time
from src.agents.base_agent import BaseAgent
from src.utils.markdown_io import read_markdown, write_markdown
from src.utils.telegram_notifier import TelegramNotifier
from src.utils.logger import logger
from src.utils.schemas import RiskEvalResponse


class GlobalRiskAgent(BaseAgent):
    def __init__(self, prompt_path: str = "rules/prompt_global_risk_agent.md"):
        super().__init__("GlobalRisk", prompt_path)
        self.portfolio_path = "manager/current_portfolio.md"
        self.portfolio_state_path = "manager/portfolio_state.json"
        self.risk_report_path = "reports/risk_status.md"
        self.notifier = TelegramNotifier()
        # 규칙 기반 리스크 파라미터
        self._price_history = {}  # {ticker: [prices...]} 최근 가격 추적
        self._alert_cooldown = {}  # 알림 중복 방지
        self.CRASH_THRESHOLD = -0.05  # -5% 급락 감지
        self.PORTFOLIO_LOSS_LIMIT = -0.10  # 총 자본 대비 -10% 손실 한도

    def get_state(self) -> str:
        portfolio = read_markdown(self.portfolio_path)
        state = f"--- Firm Portfolio Data ---\n{portfolio}"
        return state

    def get_holdings(self) -> dict:
        holdings = {}
        with open(self.portfolio_state_path, 'r') as f:
            holdings = json.load(f)
        holdings = {agent: holdings['portfolios'][agent]['holdings'] for agent in holdings['portfolios']}
        return holdings

    def execute_logic(self, market_data: dict, market_is_bullish: bool) -> bool:
        """
        [고빈도 실행 - LLM 미사용]
        규칙 기반으로 빠르게 리스크를 판단합니다:
        1. BTC 급락 감지 (-5% 이상)
        2. 개별 코인 급락 감지
        """
        if not market_data:
            return

        alerts = []
        current_state = self.get_state()
        holdings = self.get_holdings()
        holdings_set = set()
        for agent in holdings:
            for ticker in holdings[agent]:
                holdings_set.add(ticker)

        for ticker, price in market_data.items():
            if ticker not in holdings_set:
                continue
            price = float(price)
            # 가격 이력 추적 (최근 60개 = 1시간치 @1분 주기)
            if ticker not in self._price_history:
                self._price_history[ticker] = []
            self._price_history[ticker].append(price)
            if len(self._price_history[ticker]) > 60:
                self._price_history[ticker] = self._price_history[ticker][-60:]

            history = self._price_history[ticker]
            if len(history) < 2:
                continue

            # 최근 고점 대비 하락률 계산
            recent_high = max(history)
            drop_rate = (price - recent_high) / recent_high if recent_high > 0 else 0

            if drop_rate <= self.CRASH_THRESHOLD:
                # 중복 알림 방지 (같은 티커 5분 이내 재알림 안 함)
                
                last_alert = self._alert_cooldown.get(ticker, 0)
                if time.time() - last_alert > 300:
                    alert_msg = f"🚨 {ticker} 급락 감지: 최근 고점 대비 {drop_rate:.1%} 하락 (현재가: {price:,.0f})"
                    alerts.append(alert_msg)
                    self._alert_cooldown[ticker] = time.time()
                    logger.info(f"[{self.name}] {alert_msg}")

        # BTC 급락 시 KILL SWITCH 경고
        btc_price = market_data.get("KRW-BTC", 0)
        if btc_price and "KRW-BTC" in self._price_history:
            btc_history = self._price_history["KRW-BTC"]
            if len(btc_history) >= 10:
                btc_high = max(btc_history)
                btc_drop = (float(btc_price) - btc_high) / btc_high if btc_high > 0 else 0
                if btc_drop <= -0.08:  # BTC -8% 이상이면 킬스위치급
                    kill_msg = f"🚨🚨 *[KILL SWITCH 경고]* BTC 급락 {btc_drop:.1%} | 전체 매매 주의 필요!"
                    logger.info(f"[{self.name}] {kill_msg}")
                    self.notifier.send_message(kill_msg)
                    market_is_bullish = False

        if alerts:
            alert_text = "\n".join(alerts)
            self.notifier.send_message(f"⚠️ *리스크 경고*\n{alert_text}")
        else:
            logger.info(f"[{self.name}] ✅ 리스크 정상")
        return market_is_bullish

    def execute_logic_llm(self, market_data: dict) -> None:
        """
        [저빈도 실행 - LLM 사용, hourly 루프용]
        LLM을 호출하여 종합적인 리스크 분석을 수행합니다.
        """
        current_state = self.get_state()
        user_prompt = f"Current State:\n{current_state}\n\nMarket Data:\n{json.dumps(market_data)}\n\nEvaluate risk and determine if HALT is required. Output JSON."

        response = self._call_llm(self.system_prompt, user_prompt, schema_cls=RiskEvalResponse)

        try:
            decision = json.loads(response)
            if decision.get("trigger_kill_switch"):
                reason = decision.get("reason", "Unknown severe risk")
                logger.info(f"[KILL SWITCH ACTIVATED] Reason: {reason}")
                self.notifier.send_message(f"🚨🚨 *[KILL SWITCH ACTIVATED]* 🚨🚨\n\n모든 거래가 강제 중단되었습니다.\n*원인:* {reason}")
            else:
                if "risk_summary" in decision:
                    summary = decision["risk_summary"]
                    write_markdown(self.risk_report_path, summary)
                    logger.info(f"[{self.name}] Risk status logged.")
        except json.JSONDecodeError:
            logger.error(f"[{self.name}] Failed to parse LLM response.")

