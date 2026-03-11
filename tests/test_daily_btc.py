import os
import sys
import requests

from dotenv import load_dotenv

# Add project root to PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.telegram_notifier import TelegramNotifier
from src.utils.market_data import UpbitMarketData

def fetch_and_notify_daily_btc():
    """
    업비트 API에서 BTC 일봉(days) 데이터 30일치를 가져와서
    텔레그램으로 요약 전송하는 테스트 함수.
    """
    load_dotenv()
    notifier = TelegramNotifier()
    ticker = "KRW-BTC"
    
    print(f"[{ticker}] 최근 30일 일봉 가격 데이터 가져오기...")
    
    url = f"{UpbitMarketData.BASE_URL}/candles/days"
    querystring = {"market": ticker, "count": 30}
    headers = {"accept": "application/json"}

    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json()
        
        if not data:
            print("데이터를 가져오지 못했습니다.")
            return

        # 최신->과거 순으로 오는 데이터를 과거->최신으로 다시 정렬
        data.reverse()
        
        # 간단한 요약 텍스트 구성
        msg_lines = [f"📊 *{ticker} 최근 30일 일봉 가격 요약*"]
        msg_lines.append("`-----------------------------`")
        
        start_date = data[0]['candle_date_time_kst'].split('T')[0]
        end_date = data[-1]['candle_date_time_kst'].split('T')[0]
        
        msg_lines.append(f"기간: {start_date} ~ {end_date}")
        
        start_price = data[0]['trade_price']
        end_price = data[-1]['trade_price']
        
        change_rate = ((end_price - start_price) / start_price) * 100
        sign = "+" if change_rate > 0 else ""
        
        msg_lines.append(f"30일 전 종가: {start_price:,.0f} 원")
        msg_lines.append(f"현재(최신) 종가: {end_price:,.0f} 원")
        msg_lines.append(f"30일 변동률: *{sign}{change_rate:.2f}%*")
        msg_lines.append("`-----------------------------`")
        
        final_message = "\n".join(msg_lines)
        
        print("\n--- 전송될 메시지 내용 ---")
        print(final_message)
        print("--------------------------\n")
        
        print("텔레그램 전송 중...")
        success = notifier.send_message(final_message)
        if success:
            print("텔레그램 전송 성공!")
        else:
            print("텔레그램 전송에 실패했습니다.")
            
    except Exception as e:
        print(f"API 요청 오류: {e}")

if __name__ == "__main__":
    fetch_and_notify_daily_btc()
