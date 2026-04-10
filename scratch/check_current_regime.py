import sys
import os
import pandas as pd
sys.path.append(os.getcwd())

from src.data.market_data import UpbitMarketData
from src.utils.logger import logger
import logging

# 로깅 억제
logging.getLogger().setLevel(logging.ERROR)

def check():
    print("🔍 현재 시장 장세 실시간 분석 중...")
    try:
        # 비트코인 60분봉 데이터 가져오기 (전체 시장 앵커)
        df = UpbitMarketData.get_ohlcv_with_indicators_new("KRW-BTC", count=100, interval="minutes/60")
        if df.empty:
            print("데이터를 가져오지 못했습니다.")
            return

        # 1. 글로벌 장세 판별
        global_regime = UpbitMarketData.market_regime(df)
        
        # 2. 상세 지표 추출
        price = df.close.iloc[-1]
        adx = df.adx_14.iloc[-1]
        rsi = df.rsi_14.iloc[-1]
        atr = df.atr_14.iloc[-1]
        volatility = (atr / price * 100)
        ma20 = df.ma_20.iloc[-1]
        trend_dist = (price - ma20) / ma20 * 100

        print(f"\n✅ [분석 결과: {global_regime.upper()}]")
        print(f"• 현재가: {price:,.0f} KRW")
        print(f"• 이평선 이격: {trend_dist:+.2f}% (20 MA 대비)")
        print(f"• 추세 강도(ADX): {adx:.1f}")
        print(f"• 심리 지수(RSI): {rsi:.1f}")
        print(f"• 변동성(ATR%): {volatility:.2f}%")
        
        # 장세별 설명
        descriptions = {
            "earlybreakout": "🚀 새로운 상승 추세의 시작(돌파)이 감지되었습니다! 공격적인 진입이 유리한 구간입니다.",
            "bullish": "📈 안정적인 상승 추세가 유지되고 있습니다. 수익을 길게 가져가기 좋은 장세입니다.",
            "recovery": "🩹 하락세가 멈추고 반등이 시작되었습니다. 낙폭 과대 종목의 저점 매수가 유효합니다.",
            "volatile_ranging": "🔀 방향성 없이 출렁이는 횡보장입니다. 짧은 단타나 VWAP 박스권 매매가 유리합니다.",
            "ranging": "⚖️ 평온한 횡보장입니다. 큰 수익보다는 보수적인 접근이 필요합니다.",
            "bearish": "📉 하락 추세입니다. 현금 비중을 높이고 관망하는 것이 현명합니다.",
            "panic": "😱 투매가 발생하는 위험 구간입니다! 모든 매수를 멈추고 자산을 보호해야 합니다.",
            "stagnant": "💤 거래와 변동성이 죽은 시장입니다. 수수료만 낭비될 수 있으니 거래를 쉬어주세요.",
            "neutral": "😐 특징 없는 중립 상태입니다."
        }
        print(f"\n💡 가이드: {descriptions.get(global_regime, '분석 중...')}")

    except Exception as e:
        print(f"오류 발생: {e}")

if __name__ == "__main__":
    check()
