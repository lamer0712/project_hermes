from .base import BaseStrategy, Signal, SignalType
from src.utils.logger import logger


class VWAPReversionStrategy(BaseStrategy):
    """
    VWAP Reversion Strategy (기관급 눌림목 매매)

    일일 누적 VWAP(세력 평단가) 아래로 주가가 이탈했을 때,
    하락 파동이 멈추고 반등하려는 시그널(RSI 과매도 + 볼린저 밴드 하단)에서 진입하여
    VWAP 복귀까지의 단기 반등 수익을 노립니다. (Ranging 시장에 특화)
    """

    def __init__(self, params: dict = None):
        default = self.get_default_params()
        if params:
            default.update(params)
        super().__init__("VWAPReversion", default)

    def get_default_params(self) -> dict:
        return {
            "entry": {
                "vwap_distance_pct": -0.008,  # VWAP 대비 최소 0.8% 이탈
                "rsi_threshold": 38,  # RSI 38 이하 (과매도)
            },
            "exit": {
                "rsi_threshold": 65,  # RSI 65 도달 시 청산 (반등 완료)
                "vwap_buffer": 0.002,  # VWAP 도달 부근에서 청산 (0.2%)
            },
            "position_size_ratio": 0.777,  # 과대낙폭(안전한 자리)이므로 최대 투입 승수
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data,
        entry_market_data,
        portfolio_info: dict = None,
    ) -> Signal:
        holdings = portfolio_info.get("holdings", {})
        is_held = ticker in holdings and holdings[ticker]["volume"] > 0

        df = entry_market_data
        if len(df) < 5 or "vwap" not in df.columns:
            return Signal(SignalType.HOLD, ticker, "데이터 부족(VWAP 없음)", 0)

        current_price = df.close.iloc[-1]
        vwap = df.vwap.iloc[-1]

        # 0 나누기 방지
        if vwap <= 0:
            return Signal(SignalType.HOLD, ticker, "VWAP < 0", 0)

        rsi = df.rsi_14.iloc[-1]
        bb_lower = df.bb_lower.iloc[-1]

        # =========================
        # EXIT (15m)
        # =========================
        if is_held:
            vwap_touch = current_price >= vwap * (
                1.0 - self.params["exit"]["vwap_buffer"]
            )
            rsi_overbought = rsi >= self.params["exit"]["rsi_threshold"]

            if vwap_touch or rsi_overbought:
                reason = "VWAP Touch" if vwap_touch else f"RSI {rsi:.1f} Exit"
                return Signal(SignalType.SELL, ticker, f"Exit: {reason}", 1.0)

            return Signal(
                SignalType.HOLD,
                ticker,
                f"VWAP 회귀 대기 (Dist: {((current_price-vwap)/vwap)*100:.1f}%)",
                0,
            )

        # =========================
        # ENTRY (15m)
        # =========================
        distance_to_vwap = (current_price - vwap) / vwap

        # 진입 1. 가격이 VWAP 대비 충분히 폭락(-1.5% 이상)
        if distance_to_vwap <= self.params["entry"]["vwap_distance_pct"]:
            # 진입 2. RSI 과매도 구간
            if (
                rsi < self.params["entry"]["rsi_threshold"]
                or current_price < bb_lower * 1.02
            ):
                strength = self.params["position_size_ratio"]

                # logger.info(
                #     f"🎯 [VWAP Reversion] {ticker} 진입 포착! "
                #     f"(VWAP 이격: {distance_to_vwap*100:.1f}%, RSI: {rsi:.1f}, CP: {current_price}, BB_Low: {bb_lower})"
                # )

                return Signal(
                    SignalType.BUY,
                    ticker,
                    f"VWAP Dip (Dist: {distance_to_vwap*100:.1f}%, RSI: {rsi:.1f}), CP: {current_price}, BB_Low: {bb_lower})",
                    strength,
                )

        return Signal(SignalType.HOLD, ticker, "대기", 0)
