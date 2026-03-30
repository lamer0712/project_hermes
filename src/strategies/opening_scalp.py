import pandas as pd
from datetime import datetime
from src.strategies.base import BaseStrategy, Signal, SignalType
from src.utils.logger import logger


class OpeningScalpStrategy(BaseStrategy):
    """
    9:30-10:30 AM KST 5-minute candle breakout & pullback scalping strategy.
    Runs strictly once a day per coin.
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

        # If we currently hold it, let RiskManager / 15m cycle handle it via our custom SL/TP
        if is_held:
            return Signal(
                SignalType.HOLD, ticker, "보유 중 (RiskManager가 청산 담당)", 0, 0.0
            )

        # entry_market_data must be 5-minute candles
        if entry_market_data is None or len(entry_market_data) < 2:
            return Signal(SignalType.HOLD, ticker, "데이터 부족", 0, 0.0)

        df = entry_market_data.copy()

        # Get candles that match 00:30 UTC
        target_candles = df[(df["time"].dt.hour == 0) & (df["time"].dt.minute == 30)]

        if target_candles.empty:
            return Signal(SignalType.HOLD, ticker, "9:30 캔들 없음", 0, 0.0)

        ref_candle = target_candles.iloc[-1]
        ref_time = ref_candle["time"]

        # Only evaluate if the reference candle is from today
        time_diff = df["time"].iloc[-1] - ref_time
        if time_diff.total_seconds() > 12 * 3600:
            return Signal(SignalType.HOLD, ticker, "당일 9:30 캔들 아님", 0, 0.0)

        high = float(ref_candle["high"])
        low = float(ref_candle["low"])
        open_price = float(ref_candle["open"])
        close_price = float(ref_candle["close"])

        midpoint = (high + low) / 2.0

        # 음봉 필터
        if close_price < open_price:
            return Signal(SignalType.HOLD, ticker, "음봉 발생", 0, 0.0)

        # 꼬리 유무
        if not (low < open_price and close_price < high):
            return Signal(SignalType.HOLD, ticker, "꼬리 없음", 0, 0.0)

        # Only evaluate candles AFTER the reference candle
        subsequent_df = df[df["time"] > ref_time]

        if subsequent_df.empty:
            return Signal(SignalType.HOLD, ticker, "9:30 캔들 이후 데이터 없음", 0, 0.0)

        conf = (high - low) / low + 0.3

        # Step 1: Breakout
        # Did any candle close above the high?
        breakouts = subsequent_df[subsequent_df["close"] > high]
        if breakouts.empty:
            return Signal(SignalType.HOLD, ticker, "돌파 발생 전", 0, conf)

        first_breakout_time = breakouts.iloc[0]["time"]

        # Step 2: Pullback
        # After the breakout, did any candle test the high? (low <= high)
        post_breakout_df = subsequent_df[subsequent_df["time"] > first_breakout_time]
        if post_breakout_df.empty:
            return Signal(SignalType.HOLD, ticker, "돌파 후 리테스트 대기 중", 0, conf)

        post_breakout_df = post_breakout_df.iloc[-1]
        if not (post_breakout_df["low"] < high and post_breakout_df["close"] > high):
            return Signal(SignalType.HOLD, ticker, "돌파 후 리테스트 대기 중", 0, conf)

        # Step 3: Entry
        close_price = float(post_breakout_df["close"])
        risk = close_price - midpoint
        if risk <= 0:
            return Signal(
                SignalType.HOLD,
                ticker,
                "리스크 계산 오류 (손절가 > 현재가)",
                0,
                conf,
            )

        reward = risk * 2.0
        target_price = close_price + reward

        sig = Signal(
            type=SignalType.BUY,
            ticker=ticker,
            reason="9:30 고가 돌파 및 리테스트 지지 확인",
            strength=1.0,  # Scalping usually full strength
            confidence=conf,
        )
        sig.custom_sl_price = midpoint
        sig.custom_tp_price = target_price
        return sig

        # return Signal(
        #     SignalType.HOLD, ticker, "리테스트 중 (종가 고가 돌파 전)", 0, 0.0
        # )
