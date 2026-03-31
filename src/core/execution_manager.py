import time
from typing import Optional
from src.utils.logger import logger


class ExecutionManager:
    """
    ManagerAgent에서 분리된 주문 실행 및 추적 담당자 (비동기/논블로킹 처리).
    실제 Upbit API 호출과 호가창 필터 등을 처리하며, 체결 내역을 백그라운드 추적해 PortfolioManager에 기록합니다.
    """

    MIN_ORDER_AMOUNT = 5000
    MAX_POSITION_RATIO = 0.3
    MAX_POSITIONS = 5

    def __init__(self, broker, portfolio_manager, notifier):
        self.broker = broker
        self.portfolio_manager = portfolio_manager
        self.notifier = notifier
        # pending_orders 구조: { "uuid": {"type": "buy"|"sell", "agent_name": .., "ticker": .., "current_price": .., "atr": .., "strategy_name": .., "volume": ..} }
        self.pending_orders = {}

    def check_pending_orders(self):
        """대기 중인 제출 주문들의 체결 상태를 확인하여 PortfolioManager에 기록합니다."""
        if not self.broker.is_configured():
            return

        if not self.pending_orders:
            return

        completed_uuids = []
        for uuid_str, order_data in list(self.pending_orders.items()):
            order_info = self.broker.get_order(uuid_str)

            if "error" in order_info:
                logger.error(
                    f"[ExecutionManager] 주문 조회 실패 ({uuid_str}): {order_info['error']}"
                )
                continue

            state = order_info.get("state")
            if state in ("done", "cancel"):
                completed_uuids.append((uuid_str, order_info, order_data))

        # 실제 PM 업데이트 처리
        trade_msgs = []
        for uuid_str, order_info, order_data in completed_uuids:
            state = order_info.get("state")
            order_type = order_data["type"]
            agent_name = order_data["agent_name"]
            ticker = order_data["ticker"]
            reason = order_data.get("reason", "")

            if state == "done" or (
                state == "cancel" and float(order_info.get("executed_volume", 0)) > 0
            ):
                try:
                    executed_volume = float(order_info.get("executed_volume", 0))
                    trades = order_info.get("trades", [])
                    executed_funds = sum(float(t.get("funds", 0)) for t in trades)
                    paid_fee = float(order_info.get("paid_fee", 0))

                    if state == "cancel":
                        logger.info(
                            f"[ExecutionManager] 주문 취소되었으나 부분 체결됨: {ticker} ({executed_volume} {order_type})"
                        )
                except Exception as e:
                    logger.error(f"[ExecutionManager] 체결 내역 파싱 오류: {e}")
                    executed_volume = order_data.get("volume", 0)
                    executed_funds = executed_volume * order_data.get(
                        "current_price", 0
                    )
                    paid_fee = 0.0

                current_price = order_data.get("current_price", 0)

                if self.portfolio_manager:
                    if order_type == "buy":
                        strategy_name = order_data.get("strategy_name", "Unknown")
                        atr = order_data.get("atr", 0.0)
                        msg = self.portfolio_manager.record_buy(
                            agent_name=agent_name,
                            ticker=ticker,
                            volume=executed_volume,
                            price=current_price,
                            executed_funds=executed_funds,
                            paid_fee=paid_fee,
                            strategy=strategy_name,
                        )
                        if atr > 0:
                            self.portfolio_manager.update_holding_metadata(
                                agent_name, ticker, atr_14=atr
                            )

                        custom_sl_price = order_data.get("custom_sl_price")
                        custom_tp_price = order_data.get("custom_tp_price")
                        if custom_sl_price is not None or custom_tp_price is not None:
                            self.portfolio_manager.update_holding_metadata(
                                agent_name,
                                ticker,
                                custom_sl_price=custom_sl_price,
                                custom_tp_price=custom_tp_price,
                            )

                        if msg and isinstance(msg, str):
                            trade_msgs.append(f"{msg}\n  └ 사유: {reason}")
                    elif order_type == "sell":
                        target_volume_to_deduct = order_data.get(
                            "pm_tracked_volume", executed_volume
                        )
                        msg = self.portfolio_manager.record_sell(
                            agent_name=agent_name,
                            ticker=ticker,
                            volume=executed_volume,
                            price=current_price,
                            executed_funds=executed_funds,
                            paid_fee=paid_fee,
                        )
                        if msg and isinstance(msg, str):
                            trade_msgs.append(f"{msg}\n  └ 사유: {reason}")

            elif state == "cancel":
                logger.warning(
                    f"[ExecutionManager] 주문 취소됨: {ticker} ({order_type})"
                )

            # 처리 완료된 주문은 딕셔너리에서 제거
            if uuid_str in self.pending_orders:
                del self.pending_orders[uuid_str]

        # 요약 메시지 전송
        if trade_msgs:
            combined_msg = "🔔 **매매 체결 알림**\n" + "\n".join(trade_msgs)
            self.notifier.send_message(combined_msg)

    def execute_buy(
        self,
        agent_name: str,
        ticker: str,
        current_price: float,
        signal,
        risk_manager_params: dict,
        atr: float = 0.0,
        strategy_name: str = "Unknown",
    ) -> bool:
        """비동기 매수 실행 (성공 시 True, 기각 시 False 반환)"""
        if not self.broker.is_configured():
            return False

        # 투자금 계산 로직
        if self.portfolio_manager:
            available_cash = self.portfolio_manager.get_available_cash(agent_name)
            portfolio_value = self.portfolio_manager.get_total_value(agent_name)

            target_risk_pct = 0.02
            trade_risk_pct = 0.05

            if atr > 0:
                atr_pct = atr / current_price
                trade_risk_pct = max(0.03, min(0.15, atr_pct * 2.5))

            base_position_size = portfolio_value * (target_risk_pct / trade_risk_pct)
            max_allowed = portfolio_value / self.MAX_POSITIONS
            base_position_size = min(base_position_size, max_allowed, available_cash)

            strength = max(self.MAX_POSITION_RATIO, min(signal.strength, 1.0))
            order_amount = base_position_size * strength

            stop_loss_pct = risk_manager_params.get("stop_loss_pct", -5.0)
            loss_ratio = abs(float(stop_loss_pct)) / 100.0
            if loss_ratio >= 1.0:
                loss_ratio = 0.99

            dynamic_min_amount = (self.MIN_ORDER_AMOUNT / (1.0 - loss_ratio)) * 1.01

            if order_amount < dynamic_min_amount:
                if available_cash >= dynamic_min_amount:
                    order_amount = dynamic_min_amount
                else:
                    order_amount = available_cash
        else:
            order_amount = current_price * 0.001

        if order_amount < self.MIN_ORDER_AMOUNT:
            logger.warning(
                f"[ExecutionManager] 잔고 부족: 주문 금액({order_amount:,.0f} KRW)이 최소 기준 미달 → 매수 취소"
            )
            return False

        # 매수 전 호가창(Orderbook) 불균형 필터
        orderbooks = self.broker.get_orderbook(ticker)
        if orderbooks and len(orderbooks) > 0:
            ob = orderbooks[0]
            total_ask = ob.get("total_ask_size", 0)
            total_bid = ob.get("total_bid_size", 0)

            # 매도 잔량이 매수 잔량의 0.7배 미만이면 가짜 돌파 혐의
            if total_ask < total_bid * 0.7:
                logger.warning(
                    f"[Orderbook Filter] {ticker} 얇은 매도 잔고(ask: {total_ask:.2f} < bid: {total_bid:.2f}*0.7). 진입 기각."
                )
                return False

        stop_loss_pct = risk_manager_params.get("stop_loss_pct", -5.0)
        if atr > 0:
            atr_pct = (atr / current_price) * 100.0
            stop_loss_pct = -max(3.0, min(15.0, atr_pct * 2.5))

        logger.info(
            f"🟢 비동기 매수 제출: {ticker} | 금액: {order_amount:,.0f} KRW | SL: {stop_loss_pct:.1f}% | CP: {current_price:,.2f}"
        )
        res = self.broker.place_order(
            ticker,
            "bid",
            price=str(int(order_amount)),
            ord_type="price",
            current_price=current_price,
        )

        if res and "error" not in res:
            uuid_str = res.get("uuid")
            if uuid_str:
                self.pending_orders[uuid_str] = {
                    "type": "buy",
                    "agent_name": agent_name,
                    "ticker": ticker,
                    "current_price": current_price,
                    "atr": atr,
                    "strategy_name": strategy_name,
                    "custom_sl_price": (
                        signal.custom_sl_price
                        if hasattr(signal, "custom_sl_price")
                        else None
                    ),
                    "custom_tp_price": (
                        signal.custom_tp_price
                        if hasattr(signal, "custom_tp_price")
                        else None
                    ),
                    "volume": order_amount / current_price,  # fallback obj
                    "reason": signal.reason if hasattr(signal, "reason") else "",
                }
                return True

        return False

    def execute_sell(
        self, agent_name: str, ticker: str, current_price: float, signal
    ) -> None:
        """비동기 매도 실행"""
        if not self.broker.is_configured():
            return

        held_volume = 0.0
        if self.portfolio_manager:
            holdings = self.portfolio_manager.get_holdings(agent_name)
            if ticker not in holdings or holdings[ticker]["volume"] <= 0:
                logger.info(
                    f"[ExecutionManager] {ticker} 매도 시그널이나 보유 수량 없음. 생략."
                )
                return
            held_volume = holdings[ticker]["volume"]
        else:
            balances = self.broker.get_balances()
            currency = ticker.split("-")[1] if "-" in ticker else ticker
            for b in balances:
                if b.get("currency") == currency:
                    held_volume = float(b.get("balance", "0"))
                    break

        if held_volume <= 0:
            return

        sell_volume = held_volume * signal.strength
        remaining_volume = held_volume - sell_volume
        remaining_value = remaining_volume * current_price

        if remaining_value < self.MIN_ORDER_AMOUNT and remaining_volume > 0:
            # logger.warning(
            #     f"[ExecutionManager] 분할 매도 잔여액({remaining_value:,.0f} KRW) 미달 → 전량 매도 전환"
            # )
            sell_volume = held_volume

        estimated_value = sell_volume * current_price

        if estimated_value < self.MIN_ORDER_AMOUNT:
            logger.warning(
                f"[ExecutionManager] 매도 예상액({estimated_value:,.0f} KRW) 미달. 매도 불가."
            )
            return

        # 실제 잔고 확인 및 동기화 무결성 체크
        currency = ticker.split("-")[1] if "-" in ticker else ticker
        actual_balance = 0.0
        try:
            balances = self.broker.get_balances()
            for b in balances:
                if b.get("currency") == currency:
                    actual_balance = float(b.get("balance", "0"))
                    break
        except Exception as e:
            logger.error(f"[ExecutionManager] 실제 잔고 조회 실패: {e}")

        if actual_balance <= 0.00000001:
            logger.warning(
                f"[ExecutionManager] {ticker} 실제 Upbit 잔고가 거의 없음 → 매도 취소"
            )
            # 매도할 잔고가 없는데 PM에만 남은 경우, 차기 synchronize_balances 에서 정리하도록 위임
            return

        pm_tracked_volume = sell_volume
        if sell_volume > actual_balance:
            logger.warning(
                f"[ExecutionManager] PM매도수량({sell_volume:.6f}) > 실제잔고({actual_balance:.6f}) → 실제 잔고 보정"
            )
            sell_volume = actual_balance

        logger.info(f"🔴 비동기 매도 제출: {ticker} | 수량: {sell_volume:.6f}")
        res = self.broker.place_order(
            ticker,
            "ask",
            volume=str(sell_volume),
            ord_type="market",
            current_price=current_price,
        )

        if res and "error" not in res:
            uuid_str = res.get("uuid")
            if uuid_str:
                self.pending_orders[uuid_str] = {
                    "type": "sell",
                    "agent_name": agent_name,
                    "ticker": ticker,
                    "current_price": current_price,
                    "pm_tracked_volume": pm_tracked_volume,
                    "volume": sell_volume,
                    "reason": signal.reason if hasattr(signal, "reason") else "",
                }
