import os
import requests
from dotenv import load_dotenv

load_dotenv()

class TelegramNotifier:
    """텔레그램 봇 API를 이용하여 메시지를 동기 방식으로 전송하는 유틸리티 클래스"""
    
    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def is_configured(self) -> bool:
        """텔레그램 봇 토큰 및 채팅방 ID가 설정되어 있는지 확인합니다."""
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> bool:
        """
        주어진 텍스트를 설정된 텔레그램 채팅방으로 전송합니다.
        스레드 블로킹을 최소화하기 위해 짧은 타임아웃을 사용합니다.
        
        Args:
            text (str): 전송할 메시지 내용 (Markdown 지원 형식)
            
        Returns:
            bool: 전송 성공 여부
        """
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
