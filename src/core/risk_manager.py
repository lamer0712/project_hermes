from src.strategies.base import Signal, SignalType
from src.utils.logger import logger


class RiskManager:
    """
    포트폴리오 리스크 관리를 수행하는 클래스
    (트레일링 스탑, 익절, 분할 손절 평가)
    """

    risk_params = {
        "take_profit_pct": 2.0,  # 기본 익절 앵커 (regime_tp_map에 없을 경우 사용)
        "ratio": 1.5,            # 앵커 대비 손절 비율 (수익:손실 비중)
        "trailing_start_ratio": 0.7, 
        "trailing_stop_ratio": 0.4,
        "breakeven_ratio": 0.5,      # 본절 보호 가동 지점 비율
        "breakeven_exit_ratio": 0.1, # 본절 보호 시 최소 익절 보존 비율 (동적 임계값)
        
        # 장세별 익절 목표 (Regime -> TP%)
        "regime_tp_map": {
            "earlybreakout": 8.0, 
            "bullish": 5.0,        
            "recovery": 4.0,       
            "volatile_ranging": 3.0, 
            "ranging": 2.5,        
            "bearish": 3.0,        
            "panic": 2.5,          
            "stagnant": 1.0,       
        }
    }

    def __init__(self, portfolio_manager):
        self.portfolio_manager = portfolio_manager

    def evaluate_risk(
        self, agent_name: str, ticker: str, current_price: float, market_regime: str = None
    ) -> Signal | None:
        """
        [가변형/장세맞춤형 리스크 매니저]
        보유 종목의 리스크를 평가하고 매도 시그널이 발생하면 Signal 객체를 반환합니다.
        *주의: 이 메서드는 순수하게 시그널만 반환하며, 
         메타데이터(상태) 업데이트는 주문 체결이 완료된 후 PortfolioManager에서 수행해야 합니다.
        """
        if not self.portfolio_manager:
            return None

        holdings = self.portfolio_manager.get_holdings(agent_name)
        if ticker not in holdings or holdings[ticker]["volume"] <= 0:
            return None

        avg_price = holdings[ticker].get("avg_price", 0)
        max_price = max(holdings[ticker].get("max_price", avg_price), avg_price)

        if avg_price <= 0:
            return None

        # [유지] 최고가 갱신: 리스크 평가를 위한 내부 기준점 역할이므로 허용
        if current_price > max_price:
            self.portfolio_manager.update_holding_metadata(
                agent_name, ticker, max_price=current_price
            )
            max_price = current_price

        profit_pct = (current_price - avg_price) / avg_price * 100.0
        max_profit_pct = (max_price - avg_price) / avg_price * 100.0

        # ===============================================
        # 1. 장세 기반 동적 파라미터 계산 (Dynamic Thresholds)
        # ===============================================
        
        regime_map = self.risk_params.get("regime_tp_map", {})
        tp_anchor = regime_map.get(market_regime, self.risk_params["take_profit_pct"])
        ratio = self.risk_params["ratio"]
        
        # 기본 손절선 계산
        base_sl_pct = -(tp_anchor / ratio)
        
        # 분할 익절 목표가
        first_tp_target = tp_anchor
        final_tp_target = tp_anchor * ratio
        
        # 트레일링 및 본절 보호 기준
        trailing_start_pct = tp_anchor * self.risk_params["trailing_start_ratio"]
        trailing_stop_pct = tp_anchor * self.risk_params["trailing_stop_ratio"]
        breakeven_trigger_pct = tp_anchor * self.risk_params["breakeven_ratio"]
        
        # [수정] 조기 청산(Whipsaw) 방지를 위해 본절 보호 탈출선은 고정 수수료/슬리피지 커버 수준(최소 0.2%) 보장
        breakeven_exit_pct = max(0.2, tp_anchor * self.risk_params["breakeven_exit_ratio"])

        # ==========================================
        # 2. 강제 매도 평가 (우선순위 순)
        # ==========================================
        
        # 2-0. 전략 커스텀 지정 손절/익절 (최우선)
        custom_tp_price = holdings[ticker].get("custom_tp_price")
        custom_sl_price = holdings[ticker].get("custom_sl_price")

        if custom_tp_price is not None and current_price >= custom_tp_price:
            reason = f"커스텀 목표가 익절: 수익률 {profit_pct:.2f}%, 목표가({custom_tp_price:,.0f}) 도달"
            return Signal(SignalType.SELL, ticker, reason, 1.0, 1.0)

        if custom_sl_price is not None and current_price <= custom_sl_price:
            reason = f"커스텀 지정가 손절: 수익률 {profit_pct:.2f}%, 손절가({custom_sl_price:,.0f}) 도달"
            return Signal(SignalType.SELL, ticker, reason, 1.0, 1.0)

        # 2-1. 2단계 분할 익절 (Partial Take Profit)
        tp_levels_hit = holdings[ticker].get("tp_levels_hit", [])
        
        # [2단계] 수익률 최종 목표 도달 시 전량 청산
        if profit_pct >= final_tp_target:
            reason = f"🔴 최종 익절 (2단계 1.5x) [{market_regime}]: 수익률 {profit_pct:.2f}%, 목표({final_tp_target:.2f}%) 도달"
            return Signal(SignalType.SELL, ticker, reason, 1.0, 1.0)

        # [1단계] 1차 익절 도달 시 50% 분할 매도
        if profit_pct >= first_tp_target and "First_TP_Stage" not in tp_levels_hit:
            reason = f"🟡 분할 익절 (1단계) [{market_regime}]: 수익률 {profit_pct:.2f}%, 목표({first_tp_target:.2f}%) 도달 (50% 매도)"
            self.portfolio_manager.update_holding_metadata(agent_name, ticker, hit_tp_level="First_TP_Stage") #Todo 실제 매도후 업데이트 하도록 변경
            return Signal(SignalType.SELL, ticker, reason, 0.5, 1.0)

        # 2-2. 트레일링 스탑 (수익 보존)
        if max_profit_pct >= trailing_start_pct:
            drawdown_from_max = ((current_price - max_price) / max_price * 100.0) if max_price > 0 else 0
            if drawdown_from_max <= -abs(trailing_stop_pct):
                reason = f"🔒 트레일링 스탑 [{market_regime}]: 수익률 {profit_pct:.2f}%, 최고점({max_profit_pct:.2f}%) 대비 {drawdown_from_max:.2f}% 하락"
                return Signal(SignalType.SELL, ticker, reason, 1.0, 1.0)

        # 2-3. 본절 보호 (Break-even)
        if max_profit_pct >= breakeven_trigger_pct:
            if profit_pct <= breakeven_exit_pct:
                reason = f"🛡️ 본절 보호 [{market_regime}]: 최고 수익 {max_profit_pct:.2f}% 기록 후 하락 (탈출가 {breakeven_exit_pct:.2f}% 이탈)"
                return Signal(SignalType.SELL, ticker, reason, 1.0, 1.0)

        # 2-4. 동적 손절 + ATR 대응
        atr_14 = holdings[ticker].get("atr_14", 0)
        stop_loss_pct = base_sl_pct
        
        if atr_14 > 0:
            atr_pct = (atr_14 / avg_price) * 100.0
            # [수정] 무제한 손절 방지 (최대 허용 손절폭을 기본 손절선의 1.5배 또는 -7.0%로 하드캡 적용)
            max_allowed_sl = max(-7.0, base_sl_pct * 1.5)
            stop_loss_pct = max(max_allowed_sl, min(base_sl_pct, -atr_pct * ratio))

        if profit_pct <= stop_loss_pct:
            reason = f"🚫 동적 손절 [{market_regime}]: 수익률 {profit_pct:.2f}%, 제한선({stop_loss_pct:.2f}%) 이탈"
            return Signal(SignalType.SELL, ticker, reason, 1.0, 1.0)

        return None