import pandas as pd
from datetime import datetime
from src.strategies.base import BaseStrategy, Signal, SignalType
from src.utils.logger import logger


class OpeningScalpStrategy(BaseStrategy):
    """
    Dynamic 5/10/15-Minute Cumulative ORB Strategy.
    Runs strictly once a day per coin from 09:30 KST.
    """

    def __init__(self, params: dict = None):
        super().__init__("OpeningScalp", params or {})

    def evaluate(
        self,
        ticker: str,
        setup_market_data: pd.DataFrame,
        entry_market_data: pd.DataFrame,
        portfolio_info: dict = None,
    ) -> Signal:

        holdings, is_held = self.parse_holdings(ticker, portfolio_info)

        if is_held:
            return Signal(SignalType.HOLD, ticker, "보유 중 (RiskManager가 청산 담당)", 0, 0.0)

        if entry_market_data is None or len(entry_market_data) < 2:
            return Signal(SignalType.HOLD, ticker, "데이터 부족", 0, 0.0)

        df = entry_market_data.copy()

        # 09:30 KST (00:30 UTC) 기준 캔들 찾기
        start_candle_idx = None
        for i in range(len(df) - 1, -1, -1):
            if df["time"].iloc[i].hour == 0 and df["time"].iloc[i].minute == 30:
                start_candle_idx = i
                break

        if start_candle_idx is None:
            return Signal(SignalType.HOLD, ticker, "9:30 캔들 없음", 0, 0.0)

        ref_time = df["time"].iloc[start_candle_idx]
        time_diff = df["time"].iloc[-1] - ref_time
        if time_diff.total_seconds() > 12 * 3600:
            return Signal(SignalType.HOLD, ticker, "당일 9:30 캔들 아님", 0, 0.0)

        setup_triggered = False
        reference_high = -1.0
        reference_low = 9999999999.0
        breakout_close = 0.0
        conf = 0.0
        breakout_time = None

        # 시계열 순서대로 09:35 캔들부터 순차 확인
        # k는 기준 시간(09:30)으로부터 몇 번째 이후 캔들인지 나타냄 (k=1: 09:35~09:40 완성 캔들)
        for k in range(1, len(df) - start_candle_idx):
            eval_idx = start_candle_idx + k
            eval_candle = df.iloc[eval_idx]
            
            # 기준 Base Candles 선택: 최소 1개(09:30), 최대 3개(09:30, 09:35, 09:40)
            base_count = min(k, 3)
            base_df = df.iloc[start_candle_idx : start_candle_idx + base_count]
            
            current_ref_high = float(base_df["high"].max())
            current_ref_low = float(base_df["low"].min())
            
            # 돌파 발생: 종가가 누적 베이스 최고점 돌파
            if float(eval_candle["close"]) > current_ref_high:
                setup_triggered = True
                reference_high = current_ref_high
                reference_low = current_ref_low
                breakout_close = float(eval_candle["close"])
                breakout_time = eval_candle["time"]
                breakout_idx = eval_idx
                conf = (current_ref_high - current_ref_low) / current_ref_low + 0.3
                break

        if not setup_triggered:
            return Signal(SignalType.HOLD, ticker, "5/10/15분 누적 다이내믹 레인지 돌파 대기 중", 0, conf)

        # 돌파 이후 리테스트(Pullback) 검증
        post_breakout_df = df.iloc[breakout_idx + 1:]
        if post_breakout_df.empty:
            return Signal(SignalType.HOLD, ticker, f"돌파({breakout_time}) 후 리테스트 대기 중", 0, conf)
            
        latest_post_candle = post_breakout_df.iloc[-1]
        
        # 리테스트 지지 조건 완화 (얕은 눌림목 허용):
        # 1. 저가가 (돌파종가 - 돌파선)의 70% 위치 이하로 터치할 것 (즉, 최소 30%의 눌림 발생)
        # 2. 종가는 여전히 돌파선(reference_high) 위를 유지할 것
        max_retest_level = reference_high + (breakout_close - reference_high) * 0.7
        if not (float(latest_post_candle["low"]) <= max_retest_level and float(latest_post_candle["close"]) > reference_high):
            return Signal(SignalType.HOLD, ticker, f"돌파({breakout_time}) 후 얕은 눌림목 대기 중", 0, conf)

        # 진입 확정 시 가격
        entry_price = float(latest_post_candle["close"])
        midpoint = (reference_high + reference_low) / 2.0

        # 상위 타임프레임(1h) 정배열 필터링 추가
        if not self.is_bullish_trend_htf(setup_market_data):
            return Signal(SignalType.HOLD, ticker, "진입대기 - 상위 타임프레임(1h) 역배열 필터링", 0, conf)

        # 거래량 필터링 (1.5배)
        if not self.is_volume_confirmed(entry_market_data, multiplier=1.5):
            return Signal(SignalType.HOLD, ticker, "진입대기 - 거래량 컨펌 부족", 0, conf)

        # RSI 과매수 필터링 (70 이상 제외)
        if not self.is_not_overbought(entry_market_data, threshold=70):
            return Signal(SignalType.HOLD, ticker, "진입대기 - RSI 과매수 구역", 0, conf)

        sig = Signal(
            type=SignalType.BUY,
            ticker=ticker,
            reason=f"동적 오프닝 레인지 돌파 및 지지(리테스트) 완료",
            strength=1.0,
            confidence=conf,
        )
        sig.custom_sl_price = midpoint
        sig.custom_tp_price = None  # 이익 구간 캡 삭제 (RiskManager 트레일링 스탑 활용)
        return sig
