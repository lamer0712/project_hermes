from .base import BaseStrategy, Signal, SignalType


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
                "vwap_distance_pct": -0.01,  # VWAP 대비 최소 1% 이탈
                "rsi_threshold": 38,  # RSI 38 이하 (과매도)
            },
            "exit": {
                "rsi_threshold": 65,  # RSI 65 도달 시 청산 (반등 완료)
                "vwap_buffer": 0.002,  # VWAP 도달 부근에서 청산 (0.2%)
            },
            "position_size_ratio": 1.0,  # 과대낙폭(안전한 자리)이므로 최대 투입 승수
        }

    def evaluate(
        self,
        ticker: str,
        setup_market_data,
        entry_market_data,
        portfolio_info: dict = None,
    ) -> Signal:
        holdings, is_held = self.parse_holdings(ticker, portfolio_info)

        df = entry_market_data
        if len(df) < 5 or "vwap" not in df.columns:
            return Signal(SignalType.HOLD, ticker, "데이터 부족(VWAP 없음)", 0, 0.0)

        current_price = df.close.iloc[-1]
        vwap = df.vwap.iloc[-1]

        # 0 나누기 방지
        if vwap <= 0:
            return Signal(SignalType.HOLD, ticker, "VWAP < 0", 0, 0.0)

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
                reason = "VWAP선 도달" if vwap_touch else f"RSI 1차회복({rsi:.0f})"
                avg_price = holdings[ticker].get("avg_price", 0)
                tag = "[익절]" if current_price > avg_price else "[손절]"
                return Signal(SignalType.SELL, ticker, f"{tag} {reason}", 1.0, 1.0)

            return Signal(
                SignalType.HOLD,
                ticker,
                f"보유유지 - VWAP 이격: {((current_price-vwap)/vwap)*100:.1f}%",
                0,
                0.0,
            )

        # =========================
        # ENTRY (15m)
        # =========================
        if self.is_downtrend(entry_market_data):
            return Signal(SignalType.HOLD, ticker, "진입대기 - 하락세", 0, 0.1)

        distance_to_vwap = (current_price - vwap) / vwap
        distance = abs(distance_to_vwap)

        # 1. VWAP score (0~1)
        vwap_score = min(distance / 0.03, 1.0)  # -3% 기준 cap

        # 2. RSI score (0~1)
        rsi_score = max(0, (50 - rsi) / 20)  # RSI 30이면 1

        # 3. BB score (0~1)
        bb_dist = (current_price - bb_lower) / bb_lower
        bb_score = max(0, 1 - (bb_dist / 0.03))  # BB에서 가까울수록 1

        # 최종 score (가중합)
        score = 0.5 * vwap_score + 0.3 * rsi_score + 0.2 * bb_score

        # 진입 1. 가격이 VWAP 대비 충분히 폭락(-1.5% 이상)
        if (
            distance_to_vwap <= self.params["entry"]["vwap_distance_pct"]
            and score >= 0.5
        ):
            is_fake_dip, reason = self.is_fake_dip(df)
            if is_fake_dip:
                return Signal(
                    SignalType.HOLD,
                    ticker,
                    f"진입대기 - 가짜 눌림목: {reason}",
                    0,
                    score,
                )
            # 진입 2. RSI 과매도 구간
            if (
                rsi < self.params["entry"]["rsi_threshold"]  # 38
                and current_price < bb_lower * 1.02
            ):
                # 진입 컨펌: 캔들 종가 양봉 혹은 강한 밑꼬리 확인
                candle_body = abs(df.close.iloc[-1] - df.open.iloc[-1])
                lower_tail = min(df.close.iloc[-1], df.open.iloc[-1]) - df.low.iloc[-1]
                
                is_bullish_close = df.close.iloc[-1] > df.open.iloc[-1]
                has_long_tail = candle_body > 0 and lower_tail > candle_body * 1.5
                
                if not (is_bullish_close or has_long_tail):
                    return Signal(
                        SignalType.HOLD, 
                        ticker, 
                        "진입대기 - 양봉 지지선(몸통/밑꼬리) 컨펌 대기", 
                        0, 
                        score
                    )
                    
                strength = score * self.params["position_size_ratio"]

                rsi_bonus = self.rsi_tiebreaker(rsi, mode="oversold")
                final_conf = min(score + rsi_bonus, 1.0)

                return Signal(
                    SignalType.BUY,
                    ticker,
                    f"VWAP 이격: {distance_to_vwap*100:.1f}%, RSI침체: {rsi:.0f}",
                    strength,
                    final_conf,
                )

        return Signal(
            SignalType.HOLD,
            ticker,
            f"진입대기 - VWAP 이격: {distance_to_vwap*100:.1f}%",
            0,
            score,
        )
