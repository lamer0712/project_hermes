import os
import json
import time
import requests
from src.utils.logger import logger

class LocalLLMClient:
    """Ollama 로컬 서버를 통해 LLM 추론을 수행하는 클라이언트"""
    
    def __init__(self):
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.model = os.environ.get("OLLAMA_MODEL_NAME", "llama3")
        self.api_url = f"{self.host}/api/chat"

    def is_available(self) -> bool:
        """Ollama 서버가 구동 중인지 확인합니다."""
        try:
            response = requests.get(self.host, timeout=3)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def generate_json(self, system_prompt: str, user_prompt: str, max_retries: int = 3, schema_cls=None) -> dict:
        """
        Ollama /api/chat 에 프롬프트를 전송하고, 반드시 유효한 JSON 객체를 반환하도록 강제합니다.
        """
        if not self.is_available():
            logger.error("[LLM Error] Ollama 서버를 찾을 수 없습니다. 백그라운드 구동을 확인하세요.")
            return {}

        schema_instruction = ""
        if schema_cls:
            schema_json = json.dumps(schema_cls.model_json_schema())
            schema_instruction = (
                f"\n\nYour response MUST be a JSON object that strictly adheres to this schema:\n{schema_json}\n\n"
                "Example response (update needed):\n"
                "{\n"
                '  "update_strategy": true,\n'
                '  "new_parameters": {"rsi_buy": 30, "rsi_sell": 70},\n'
                '  "reason": "Market volatile"\n'
                "}\n"
                "Example response (no update):\n"
                "{\n"
                '  "update_strategy": false,\n'
                '  "new_parameters": null,\n'
                '  "reason": "Stable"\n'
                "}"
            )

        messages = [
            {
                "role": "system", 
                "content": f"{system_prompt}{schema_instruction}\n\nStrictly respond with ONLY JSON. No thinking, no intro, no and extra keys."
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]

        logger.info(f"[LLM Request] System: {system_prompt}")
        logger.info(f"[LLM Request] User: {user_prompt}")

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json"
        }

        for attempt in range(max_retries):
            try:
                logger.info(f"[LLM] Chat 요청 중... ({self.model}) / Attempt {attempt + 1}")
                response = requests.post(self.api_url, json=payload, timeout=300)
                response.raise_for_status()
                
                # Chat API response has "message": {"content": "..."}
                result_text = response.json().get('message', {}).get('content', '').strip()
                
                logger.info(f"[LLM Response] Raw: {result_text}")

                if not result_text:
                    raise ValueError("Empty response from LLM")

                if schema_cls:
                    try:
                        parsed_obj = schema_cls.model_validate_json(result_text)
                        return parsed_obj.model_dump()
                    except Exception as e:
                        # If simple fix possible (e.g. wrapped in list)
                        if result_text.startswith("[") and result_text.endswith("]"):
                            # some models wrap json in a list
                            inner_text = result_text[1:-1].strip()
                            parsed_obj = schema_cls.model_validate_json(inner_text)
                            return parsed_obj.model_dump()
                        raise
                else:
                    return json.loads(result_text)
                
            except Exception as e:
                time.sleep(2)
                
        return {}

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        """일반 텍스트 응답을 생성합니다 (Chat API)."""
        if not self.is_available():
            return ""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        logger.info(f"[LLM Request] System: {system_prompt}")
        logger.info(f"[LLM Request] User: {user_prompt}")
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False
        }
        try:
            response = requests.post(self.api_url, json=payload, timeout=300)
            response.raise_for_status()
            result_text = response.json().get('message', {}).get('content', '').strip()
            logger.info(f"[LLM Response] Text: {result_text}")
            return result_text
        except Exception as e:
            logger.error(f"[LLM Error] 텍스트 생성 실패: {e}")
            return ""


def get_llm_client():
    """Ollama 로컬 LLM 클라이언트를 반환합니다."""
    logger.info(f"[LLM] 🏠 Ollama 로컬 LLM Chat 사용 (모델: {os.environ.get('OLLAMA_MODEL_NAME', 'llama3')})")
    return LocalLLMClient()
