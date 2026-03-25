import os
import requests
from dotenv import load_dotenv

load_dotenv()

class TelegramNotifier:
    """텔레그램 봇 API를 이용하여 메시지를 동기 방식으로 전송하는 유틸리티 클래스"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TelegramNotifier, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
        
    def __init__(self):
        if self._initialized:
            return
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self.is_buffering = False
        self.message_buffer = []
        self._initialized = True

    def is_configured(self) -> bool:
        """텔레그램 봇 토큰 및 채팅방 ID가 설정되어 있는지 확인합니다."""
        return bool(self.bot_token and self.chat_id)

    def start_buffering(self):
        self.is_buffering = True
        self.message_buffer = []

    def flush_buffer(self) -> bool:
        self.is_buffering = False
        if not self.message_buffer:
            return True
        
        combined_text = "\n\n".join(self.message_buffer)
        self.message_buffer = []
        
        # 텔레그램 메시지 길이 제한(약 4096자) 대비 긴급 분할 로직
        if len(combined_text) > 4000:
            chunks = [combined_text[i:i+4000] for i in range(0, len(combined_text), 4000)]
            success = True
            for chunk in chunks:
                if not self._send_http(chunk):
                    success = False
            return success
        else:
            return self._send_http(combined_text)

    def send_message(self, text: str) -> bool:
        """
        주어진 텍스트를 설정된 텔레그램 채팅방으로 전송합니다.
        버퍼링 모드(is_buffering) 동작 시 리스트에 담아둡니다.
        """
        if self.is_buffering:
            self.message_buffer.append(text)
            return True
            
        return self._send_http(text)

    def _send_http(self, text: str) -> bool:
        if not self.is_configured():
            print("[Telegram Warning] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않았습니다. 알림을 생략합니다.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown" # 마크다운 포맷 지원
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=5)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 400:
                # Markdown 파싱 실패 시 parse_mode 없이 재전송
                payload_plain = {
                    "chat_id": self.chat_id,
                    "text": text,
                }
                try:
                    response2 = requests.post(self.api_url, json=payload_plain, timeout=5)
                    response2.raise_for_status()
                    return True
                except requests.exceptions.RequestException as e2:
                    print(f"[Telegram Error] plain text 재전송도 실패: {e2}")
                    return False
            else:
                print(f"[Telegram Error] 메시지 전송 실패: {e}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"[Telegram Error] 메시지 전송 실패: {e}")
            return False

if __name__ == "__main__":
    # 테스트 구동 코드 (직접 실행 시)
    notifier = TelegramNotifier()
    if notifier.is_configured():
        print("텔레그램 전송 테스트 중...")
        success = notifier.send_message("🚀 *Project Hermes* - 텔레그램 연동 테스트 메시지입니다.")
        print(f"전송 완료: {success}")
    else:
        print("토큰 또는 채팅방 ID가 설정되지 않아 테스트할 수 없습니다.")
