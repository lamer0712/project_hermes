import os
import json
from src.utils.markdown_io import read_markdown
from src.utils.llm_client import LocalLLMClient
from src.utils.logger import logger

class BaseAgent:
    def __init__(self, name: str, prompt_path: str):
        self.name = name
        self.prompt_path = prompt_path
        self.system_prompt = read_markdown(self.prompt_path)
        
        # 실제 Ollama 통신 모듈 연결
        self.llm = LocalLLMClient()
    
    def get_state(self) -> str:
        """Override this method to load agent-specific markdown states."""
        raise NotImplementedError
        
    def execute_logic(self, market_data: dict) -> None:
        """Override this method to execute the core logic of the agent."""
        raise NotImplementedError
        
    def _call_llm(self, system_prompt: str, user_prompt: str, schema_cls=None) -> str:
        """
        Ollama 서버를 호출하여 반환받은 JSON Dictionary를
        기존 코드 호환성을 위해 문자열(String) 형태로 반환합니다.
        (기존 investor/manager.py에서 json.loads(response)를 하고 있으므로)
        """
        if not self.llm.is_available():
            logger.info(f"[{self.name}] LLM (Ollama) is unavailable or offline.")
            return "{}"
            
        result_dict = self.llm.generate_json(system_prompt, user_prompt, schema_cls=schema_cls)
        return json.dumps(result_dict)
