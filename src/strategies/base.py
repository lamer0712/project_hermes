from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """전략이 반환하는 매매 시그널"""

    type: SignalType
    ticker: str
    reason: str
    # 매수 시: 투자금 대비 비율 (0.0 ~ 1.0), 매도 시: 보유 수량 대비 비율
    strength: float = 0.5
    confidence: float = 0.0
    custom_sl_price: Optional[float] = None
    custom_tp_price: Optional[float] = None

    def __str__(self):
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⏸️"}
        base = f"{emoji.get(self.type.value, '❓')}{self.type.value:<4} {self.ticker} | (Conf: {self.confidence:.0%}, Size: {self.strength:.0%} , {self.reason})"
        if self.custom_sl_price or self.custom_tp_price:
            base += f" [SL: {self.custom_sl_price or 'N/A'}, TP: {self.custom_tp_price or 'N/A'}]"
        return base


class BaseStrategy(ABC):
    """
    투자 전략 베이스 클래스.
    각 전략은 이 클래스를 상속받아 evaluate()를 구현합니다.
    """

    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}
        self.regime = params.get("regime", None)

    # --------------------------------------------------
    # 공통 헬퍼 메서드
    # --------------------------------------------------

    def parse_holdings(self, ticker: str, portfolio_info: dict):
        """보유 종목 정보 파싱 — 모든 전략의 evaluate() 첫 줄에서 반복되던 로직"""
        holdings = portfolio_info.get("holdings", {})
        is_held = ticker in holdings and holdings[ticker]["volume"] > 0
        return holdings, is_held

    def validate_entry_data(self, ticker: str, entry_market_data, min_length: int = 20):
        """entry 데이터 유효성 검증 — None이거나 길이 부족 시 HOLD Signal 반환"""
        if entry_market_data is None or len(entry_market_data) < min_length:
            return Signal(SignalType.HOLD, ticker, "데이터 부족", 0, 0.0)
        return None  # 통과

    @staticmethod
    def rsi_tiebreaker(rsi_value: float, mode: str = "oversold") -> float:
        """
        동점자 방지용 RSI 미세가중치 (0.000 ~ 0.099)
        - mode="oversold" : 낮을수록 보너스 (MeanReversion, PullbackTrend 등)
        - mode="momentum" : 높을수록 보너스 (Breakout)
        """
        raw = (100 - rsi_value) if mode == "oversold" else rsi_value
        return min(max(raw, 0), 99) / 1000.0

    # --------------------------------------------------
    # 기존 판단 유틸
    # --------------------------------------------------

    @staticmethod
    def is_bullish_trend_htf(df):
        """상위 타임프레임(1h) 정배열 확인 (EMA 20 > 50 > 200)"""
        import pandas as pd # NaN 체크용
        
        if df is None or df.empty or len(df) < 5:
            return True # 데이터 없으면 필터링 안 함

        current = df.iloc[-1]
        ema20 = current.get("ema_20", 0)
        ema50 = current.get("ema_50", 0)
        ema200 = current.get("ema_200", 0)
        
        if pd.isna(ema20) or pd.isna(ema50) or pd.isna(ema200):
            return True

        if ema20 == 0 or ema50 == 0 or ema200 == 0:
            return True

        return float(ema20) > float(ema50) > float(ema200)

    @staticmethod
    def is_downtrend(df):
        ema20 = df["ema_20"].iloc[-1]
        ema50 = df["ema_50"].iloc[-1]
        ema60 = df["ma_60"].iloc[-1]

        # 단순히 20 < 50이 아니라, 장기 이평선(60)까지 고려하여 더 확실한 하락일 때만 downtrend로 판정
        return ema20 < ema50 and ema50 < ema60

    @staticmethod
    def is_volume_confirmed(df, multiplier=1.5):
        """거래량 컨펌 확인 (현재 거래량 > 평균 거래량 * multiplier)"""
        if df is None or len(df) < 20:
            return True
        
        current_vol = float(df["volume"].iloc[-1])
        vol_ma = float(df.get("volume_ma20", df["volume"].rolling(20).mean()).iloc[-1])
        
        return current_vol > vol_ma * multiplier

    @staticmethod
    def is_not_overbought(df, threshold=70):
        """RSI 과매수 구역 제외 확인 (현재 RSI < threshold)"""
        if df is None or "rsi_14" not in df.columns:
            return True
            
        current_rsi = float(df["rsi_14"].iloc[-1])
        return current_rsi < threshold

    @staticmethod
    def is_bullish_candle(df):
        """15m 종가 양봉 혹은 긴 밑꼬리 컨펌 확인"""
        if df is None or df.empty:
            return False

        current = df.iloc[-1]
        candle_body = abs(current.close - current.open)
        lower_tail = min(current.close, current.open) - current.low
        is_bullish_close = current.close > current.open
        # 몸통이 0보다 크고 밑꼬리가 몸통의 1.5배 이상이면 밑꼬리 긴 망치형 등으로 간주
        has_long_tail = candle_body > 0 and lower_tail > candle_body * 1.5

        return is_bullish_close or has_long_tail

    @staticmethod
    def is_fake_dip(df):
        vol = df["volume"].iloc[-1]
        vol_ma = df["volume_ma20"].iloc[-1]
        rsi_14 = df["rsi_14"]

        close = df["close"].iloc[-1]
        bb_lower = df["bb_lower"].iloc[-1]

        # 1. RSI 하락 지속 (최근 3개) - 여전히 칼날 잡기 방지 위해 유지
        if len(df) >= 3:
            if rsi_14.iloc[-1] < rsi_14.iloc[-2] < rsi_14.iloc[-3]:
                return True, "RSI 하락 지속"

        # 2. 거래량 너무 부족 (0.6배 미만으로 완화)
        if vol < vol_ma * 0.6:
            return True, "거래량 부족"

        # 3. 아직 덜 눌림 (BB 기준)
        if close > bb_lower * 1.03:  # 1.02 -> 1.03으로 약간 완화
            return True, "아직 덜 눌림"

        # 54. 음봉
        if df["close"].iloc[-1] < df["open"].iloc[-1]:
            return True, "음봉"

        return False, ""

    @abstractmethod
    def evaluate(
        self,
        ticker: str,
        setup_market_data: "pd.DataFrame",
        entry_market_data: "pd.DataFrame",
        portfolio_info: dict = None,
    ) -> Signal:
        """
        시장 데이터와 포트폴리오 정보를 바탕으로 매매 시그널을 반환합니다.

        Args:
            ticker: 종목명
            setup_market_data: 환경판단용 일/장기 캔들 혹은 DataFrame
            entry_market_data: 진입/청산용 단기 캔들 DataFrame
            regime: 감지된 체제
            portfolio_info: 포트폴리오 메타데이터

        Returns:
            Signal 객체
        """
        pass

    def get_default_params(self) -> dict:
        """이 전략의 기본 파라미터를 반환합니다."""
        return {}

    def update_params(self, new_params: dict):
        """파라미터를 업데이트합니다."""
        self.deep_update(self.params, new_params)

    @staticmethod
    def deep_update(source: dict, overrides: dict) -> dict:
        """Recursive dict update for nested parameters"""
        for k, v in overrides.items():
            if isinstance(v, dict) and k in source and isinstance(source[k], dict):
                BaseStrategy.deep_update(source[k], v)
            else:
                source[k] = v
        return source
