import os
import json
import time
from google import genai
from google.genai import types
from src.utils.logger import logger

class GeminiClient:
    """Gemini API를 통해 LLM 추론을 수행하는 클라이언트"""
    
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.error("[Gemini Error] GEMINI_API_KEY가 환경변수에 설정되어 있지 않습니다.")
        
        # Initialize the new google-genai client
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        # manager agent에 적합한 추론 성능을 위해 gemini-2.5-flash 추천
        self.model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")

    def is_available(self) -> bool:
        """API 키가 존재하는지 확인합니다."""
        return bool(self.api_key and self.client)

    def generate_json(self, system_prompt: str, user_prompt: str, max_retries: int = 3, schema_cls=None) -> dict:
        """
        Gemini에 프롬프트를 전송하고, 유효한 JSON 객체를 반환합니다.
        schema_cls가 주어지면 Pydantic 모델 검증을 수행합니다.
        """
        if not self.is_available():
            logger.error("[Gemini Error] API 키가 없습니다.")
            return {}

        full_prompt = f"System Instruction:\n{system_prompt}\n\nUser Input:\n{user_prompt}"

        logger.info(f"[Gemini Request] System: {system_prompt}")
        logger.info(f"[Gemini Request] User: {user_prompt}")

        # Setup configuration to enforce JSON output
        config_kwargs = {"response_mime_type": "application/json"}
        # Some models support response_schema with Pydantic model
        if schema_cls:
            config_kwargs["response_schema"] = schema_cls
            
        config = types.GenerateContentConfig(**config_kwargs)

        for attempt in range(max_retries):
            try:
                logger.info(f"[Gemini] 요청 전송 중... ({self.model_name}) / Attempt {attempt + 1}")
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt,
                    config=config
                )
                
                result_text = response.text.strip()
                logger.info(f"[Gemini Response] Raw: {result_text}")

                # markdown 코드 블록 제거
                if result_text.startswith("```json"):
                    result_text = result_text[7:]
                elif result_text.startswith("```"):
                    result_text = result_text[3:]
                if result_text.endswith("```"):
                    result_text = result_text[:-3]
                    
                result_text = result_text.strip()
                if schema_cls:
                    try:
                        parsed_obj = schema_cls.model_validate_json(result_text)
                        return parsed_obj.model_dump()
                    except Exception as e:
                        logger.warning(f"Pydantic Validation Error: {e}")
                        raise
                else:
                    parsed_json = json.loads(result_text)
                    return parsed_json
                
            except Exception as e:
                logger.error(f"[Gemini Warning] 실행 오류 또는 파싱 실패: {e}")
                time.sleep(2)
                
        logger.error("[Gemini Error] 최대 재시도 횟수를 초과했습니다.")
        return {}

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        """일반 텍스트 응답을 생성합니다."""
        if not self.is_available():
            return ""
        
        full_prompt = f"System Instruction:\n{system_prompt}\n\nUser Input:\n{user_prompt}"
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=full_prompt
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"[Gemini Error] 텍스트 생성 실패: {e}")
            return ""

def get_gemini_client():
    logger.info(f"[LLM] 🧠 Gemini API 사용 (모델: {os.environ.get('GEMINI_MODEL_NAME', 'gemini-2.5-flash')})")
    return GeminiClient()
