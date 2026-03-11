import os
import json
from src.agents.base_agent import BaseAgent
from src.utils.markdown_io import read_markdown, write_markdown
from src.utils.telegram_notifier import TelegramNotifier
from src.utils.logger import logger

from src.utils.llm_client import get_llm_client
from src.utils.gemini_client import get_gemini_client

class ManagerAgent(BaseAgent):
    def __init__(self, portfolio_manager=None, prompt_path: str = "rules/prompt_manager_agent.md"):
        super().__init__("Manager", prompt_path)
        
        # Manager는 llm API 사용
        self.llm = get_llm_client()
        self.notifier = TelegramNotifier()
        
        self.portfolio_path = "manager/current_portfolio.md"
        self.realloc_report_path = "reports/daily_reallocation.md"
        
        # 포트폴리오 매니저 참조
        self.portfolio_manager = portfolio_manager

    def get_state(self) -> str:
        portfolio = read_markdown(self.portfolio_path)
        
        # 각 investor의 성과 정보 취합
        investor_performances = ""
        if self.portfolio_manager:
            investor_performances = "\n\n--- Investor Performances ---"
            for agent_name in self.portfolio_manager.portfolios:
                perf = read_markdown(f"agents/{agent_name}/performance.md")
                strategy = read_markdown(f"agents/{agent_name}/strategy.md")
                investor_performances += f"\n\n### {agent_name}\n{perf}\n\nStrategy:\n{strategy}"
        
        state = f"--- Current Portfolio ---\n{portfolio}\n\n{investor_performances}"
        return state

    def execute_logic(self, market_data: dict) -> None:
        """
        [Capital Reallocation (Daily)]
        LLM(Gemini)을 호출하여 각 투자 에이전트의 성과를 평가하고
        수익률과 승률을 기반으로 자본을 차등 재분배(Penalty/Reward)합니다.
        """
        # 성과 MD 업데이트 (LLM에게 최신 정보 제공)
        if self.portfolio_manager:
            for agent_name in self.portfolio_manager.portfolios:
                self.portfolio_manager.update_performance_md(agent_name, market_data)
            self.portfolio_manager._update_manager_portfolio_md(market_data)
        
        
        current_state = self.get_state()
        user_prompt = f"Current State:\n{current_state}\n\nMarket Prices:\n{json.dumps(market_data)}\n\nReview agent performances. Reallocate capital based on their returns & win-rates. Provide JSON output including 'new_allocations' and 'rebalance_reason'."
        
        # Gemini LLM 호출 (base_agent.py의 _call_llm 사용 - generate_json 방식)
        from src.utils.schemas import ReallocationResponse
        response_str = self._call_llm(self.system_prompt, user_prompt, schema_cls=ReallocationResponse)
        
        try:
            decision = json.loads(response_str)
            
            # 포트폴리오 자본 재배분 (Capital Reallocation)
            new_allocations = decision.get("new_allocations", {})
            reason = decision.get("rebalance_reason", "Routine Rebalance")
            
            if new_allocations:
                if self.portfolio_manager:
                    # 안전을 위해 새 배분액의 합이 원래 총액을 초과하지 않는지 검증 (필요시 보정)
                    # 여기서는 LLM이 잘 맞췄다고 믿되, PortfolioManager가 알아서 처리하게 함.
                    self.portfolio_manager.reallocate(new_allocations)
                    logger.info(f"[{self.name}] 포트폴리오 재배분 완료: {new_allocations}")
                else:
                    new_portfolio_md = f"# Target Allocations\nReason: {reason}\n\n```json\n{json.dumps(new_allocations, indent=2)}\n```\n"
                    write_markdown(self.portfolio_path, new_portfolio_md)
                    logger.info(f"[{self.name}] Reallocation logged: {new_allocations}")
                
                # 재배분 결과 일지 작성
                from datetime import datetime
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                report_content = f"## Reallocation at {timestamp}\n\n**Reason:** {reason}\n\n**New Allocations:**\n```json\n{json.dumps(new_allocations, indent=2, ensure_ascii=False)}\n```\n\n---\n"
                
                # append 방식으로 일일 보고서 누적
                existing_report = read_markdown(self.realloc_report_path)
                write_markdown(self.realloc_report_path, existing_report + report_content)
                
                self.notifier.send_message(f"⚖️ *[Capital Realloc]*\n사유: {reason}\n{json.dumps(new_allocations)}")
            else:
                logger.info(f"[{self.name}] No valid new_allocations found in response.")
                
        except json.JSONDecodeError:
            logger.error(f"[{self.name}] Failed to parse Manager LLM response.")

    def answer_query(self, user_query: str) -> str:
        """
        사용자의 텔레그램 질문(자연어)에 대해 Manager Agent가 상태를 종합해 직접 대답합니다.
        여기서는 포트폴리오 밸런싱용 JSON 강제 로직 대신 자연어 생성을 사용합니다.
        """
        current_state = self.get_state()
        system_prompt = (
            "You are the Manager Agent of an automated investment firm. "
            "You oversee the current portfolio and HR records. "
            "Answer the User's question clearly, concisely, and professionally in Korean based on the current state."
        )
        user_prompt = f"Current State:\n{current_state}\n\nUser Question: {user_query}"
        
        logger.info(f"[{self.name}] 답변 생성 중... (User Query: {user_query})")
        
        # Gemini API 호출 (자연어 응답)
        response = self.llm.generate_text(system_prompt, user_prompt)
        if response:
            return response
        else:
            return "⚠️ AI 응답 생성 실패: Gemini API 연결을 확인해주세요."
