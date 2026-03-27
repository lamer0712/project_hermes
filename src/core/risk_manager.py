from src.strategies.base import Signal, SignalType
from src.utils.logger import logger


class RiskManager:
    """
    포트폴리오 리스크 관리를 수행하는 클래스
    (트레일링 스탑, 익절, 분할 손절 평가)
    """

    risk_params = {
        "stop_loss_pct": -5.5,
        "take_profit_pct": 10.0,
        "trailing_start_pct": 5.0,
        "trailing_stop_pct": 3.5,
        "partial_stop_loss": [
            {"pct": -6, "strength": 0.5},
            {"pct": -12, "strength": 1.0},
        ],
    }

    def __init__(self, portfolio_manager):
        self.portfolio_manager = portfolio_manager

    def evaluate_risk(
        self, agent_name: str, ticker: str, current_price: float
    ) -> Signal | None:
        """
        보유 종목의 리스크를 평가하고 매도 시그널이 발생하면 Signal 객체를 반환합니다.
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

        # 최고가 갱신
        if current_price > max_price:
            self.portfolio_manager.update_holding_metadata(
                agent_name, ticker, max_price=current_price
            )
            max_price = current_price

        profit_pct = (current_price - avg_price) / avg_price * 100.0

        # 기본 Parameter 로드
        base_stop_loss_pct = self.risk_params.get("stop_loss_pct", -5.5)
        base_partial_sl = self.risk_params.get("partial_stop_loss", [])

        take_profit_pct = self.risk_params.get("take_profit_pct", 10.0)
        trailing_stop_pct = self.risk_params.get("trailing_stop_pct", None)
        trailing_start_pct = self.risk_params.get("trailing_start_pct", 1.0)

        atr_14 = holdings[ticker].get("atr_14", 0)
        if atr_14 > 0 and avg_price > 0:
            atr_pct = (atr_14 / avg_price) * 100.0
            # 동적 스탑로스: ATR의 2.5배 (최소 3%, 최대 15%)
            stop_loss_pct = -max(3.0, min(15.0, atr_pct * 2.5))

            partial_stop_loss_list = []
            for item in base_partial_sl:
                multiplier = (
                    item["pct"] / base_stop_loss_pct if base_stop_loss_pct != 0 else 1.0
                )
                dynamic_pct = round(stop_loss_pct * multiplier, 2)
                partial_stop_loss_list.append(
                    {"pct": dynamic_pct, "strength": item["strength"]}
                )
        else:
            stop_loss_pct = base_stop_loss_pct
            partial_stop_loss_list = base_partial_sl.copy()

        partial_stop_loss = sorted(
            partial_stop_loss_list,
            key=lambda x: x["pct"],
            reverse=True,
        )

        # 1. 트레일링 스탑
        if trailing_stop_pct is not None and profit_pct >= trailing_start_pct:
            drawdown_from_max = (
                (current_price - max_price) / max_price * 100.0 if max_price > 0 else 0
            )
            if drawdown_from_max <= -abs(trailing_stop_pct):
                profit = (current_price - avg_price) * holdings[ticker]["volume"]
                reason = f"트레일링 스탑: 수익률 {profit_pct:.2f}%, {profit:,.0f}원, 최고점 대비 {drawdown_from_max:.2f}%"
                return Signal(
                    type=SignalType.SELL,
                    ticker=ticker,
                    reason=reason,
                    strength=1.0,
                    confidence=1.0,
                )

        # 2. 강제 익절
        tp_levels_hit = holdings[ticker].get("tp_levels_hit", [])
        if profit_pct >= take_profit_pct and take_profit_pct not in tp_levels_hit:
            profit = (current_price - avg_price) * holdings[ticker]["volume"]
            reason = f"강제 익절: 수익률 {profit_pct:.2f}%, {profit:,.0f}원, >={take_profit_pct}%"
            self.portfolio_manager.update_holding_metadata(
                agent_name, ticker, hit_tp_level=take_profit_pct
            )
            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=reason,
                strength=0.5,
                confidence=1.0,
            )

        # 3. 분할 강제 손절
        sl_triggered = False
        if partial_stop_loss:
            for sl_stage in partial_stop_loss:
                stage_pct = sl_stage.get("pct", stop_loss_pct)
                stage_strength = sl_stage.get("strength", 1.0)
                sl_levels_hit = holdings[ticker].get("sl_levels_hit", [])

                if profit_pct <= stage_pct and stage_pct not in sl_levels_hit:
                    profit = (current_price - avg_price) * holdings[ticker]["volume"]
                    reason = f"분할 손절: 수익률 {profit_pct:.2f}%, {profit:,.0f}원, <={stage_pct}%"
                    self.portfolio_manager.update_holding_metadata(
                        agent_name, ticker, hit_sl_level=stage_pct
                    )
                    return Signal(
                        type=SignalType.SELL,
                        ticker=ticker,
                        reason=reason,
                        strength=stage_strength,
                        confidence=1.0,
                    )

        # 4. 단일 기본 손절
        if not partial_stop_loss and profit_pct <= stop_loss_pct:
            profit = (current_price - avg_price) * holdings[ticker]["volume"]
            reason = f"강제 손절: 수익률 {profit_pct:.2f}%, {profit:,.0f}원, <={stop_loss_pct}%"

            return Signal(
                type=SignalType.SELL,
                ticker=ticker,
                reason=reason,
                strength=1.0,
                confidence=1.0,
            )

        return None
