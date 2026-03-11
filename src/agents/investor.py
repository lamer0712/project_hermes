import os
import json
import time
from src.agents.base_agent import BaseAgent
from src.utils.markdown_io import read_markdown, append_markdown, write_markdown
from src.utils.broker_api import UpbitBroker
from src.utils.llm_client import get_llm_client
from src.strategies.base import BaseStrategy, Signal, SignalType
from src.utils.logger import logger
from src.utils.schemas import StrategyUpdateResponse
from src.utils.telegram_notifier import TelegramNotifier


class InvestorAgent(BaseAgent):
    def __init__(
        self,
        name: str,
        regime: str,
        strategy: BaseStrategy,
        portfolio_manager=None,
        prompt_path: str = "rules/prompt_investment_agent.md",
    ):
        super().__init__(name, prompt_path)
        self.agent_dir = f"agents/{self.name}"
        self.strategy_path = os.path.join(self.agent_dir, "strategy.md")
        self.trades_path = os.path.join(self.agent_dir, "trades.md")
        self.performance_path = os.path.join(self.agent_dir, "performance.md")
        self.broker = UpbitBroker()
        self.llm = get_llm_client()
        self.regime = regime

        # 전략 객체 주입
        self.strategy = strategy
        # 포트폴리오 매니저 참조
        self.portfolio_manager = portfolio_manager

        # 초기 strategy.md 생성
        self._initialize_strategy_md()

    def _initialize_strategy_md(self):
        """전략 설명을 strategy.md에 기록합니다."""
        description = self.strategy.get_strategy_description()
        write_markdown(self.strategy_path, description)
        logger.info(f"[{self.name}] 전략 '{self.strategy.name}' 설정 완료")

    def get_state(self) -> str:
        strategy = read_markdown(self.strategy_path)
        trades = read_markdown(self.trades_path)

        # 포트폴리오 정보도 상태에 포함
        portfolio_info = ""
        if self.portfolio_manager:
            summary = self.portfolio_manager.get_summary(self.name)
            if summary:
                portfolio_info = f"\n\n--- Portfolio ---\nCash: {summary['cash']:,.0f} KRW\nTotal Value: {summary['total_value']:,.0f} KRW\nReturn: {summary['return_rate']:+.2f}%"

        state = f"--- Strategy ---\n{strategy}\n\n--- Recent Trades ---\n{trades}{portfolio_info}"
        return state

    # 업비트 최소 주문 금액
    MIN_ORDER_AMOUNT = 5000

    def execute_cycle(self, setup_market_data: dict, entry_market_data: dict) -> None:
        """
        [싸이클 단위 실행]
        전체 종목을 평가하여 매도는 모두 실행하되,
        매수는 가장 강한 시그널의 1종목만 실행합니다.
        """
        if not setup_market_data or not entry_market_data:
            return

        # 중지 상태 체크
        if self.portfolio_manager and self.portfolio_manager.is_halted(self.name):
            return

        best_buy = None  # (signal, market_data)

        portfolio_info = None
        kill_switch_active = False
        if self.portfolio_manager:
            portfolio_info = self.portfolio_manager.get_portfolio_info(self.name)
            available_cash = self.portfolio_manager.get_available_cash(self.name)
            holdings = self.portfolio_manager.get_holdings(self.name)

            # --- Rule-Based Kill Switch ---
            current_prices = {t: d.close.iloc[-1] for t, d in entry_market_data.items()}
            summary = self.portfolio_manager.get_summary(self.name, current_prices)

            if summary.get("total_trades", 0) > 10:
                if (
                    summary.get("win_rate", 100) < 20.0
                    or summary.get("return_rate", 0) < -15.0
                ):
                    kill_switch_active = True
                    if not getattr(self, "_kill_switch_alerted", False):
                        msg = f"[{self.name}] 🛑 킬 스위치 발동! (매매 {summary['total_trades']}회, 승률 {summary['win_rate']:.1f}%, 수익률 {summary['return_rate']:.2f}%). 신규 매수를 중단합니다."
                        logger.info(msg)
                        TelegramNotifier().send_message(msg)
                        self._kill_switch_alerted = True
                    else:
                        logger.info(
                            f"[{self.name}] 🛑 킬 스위치 발동 상태 유지 (신규 매수 중단)"
                        )
                else:
                    self._kill_switch_alerted = False
            else:
                self._kill_switch_alerted = False
        else:
            available_cash = float("inf")
            holdings = {}

        for ticker, market_data in entry_market_data.items():
            current_price = float(market_data.close.iloc[-1])

            # 매도 판단 전에 전역 손절/익절/트레일링 스탑 검사 우선
            if holdings and ticker in holdings and holdings[ticker]["volume"] > 0:
                avg_price = holdings[ticker].get("avg_price", 0)
                max_price = holdings[ticker].get("max_price", avg_price)

                # 최고가 갱신
                if current_price > max_price:
                    self.portfolio_manager.update_holding_metadata(
                        self.name, ticker, max_price=current_price
                    )
                    max_price = current_price

                if avg_price > 0:
                    profit_pct = (current_price - avg_price) / avg_price * 100.0
                    stop_loss_pct = self.strategy.params.get("stop_loss_pct", -5.0)
                    take_profit_pct = self.strategy.params.get("take_profit_pct", 10.0)
                    trailing_stop_pct = self.strategy.params.get(
                        "trailing_stop_pct", None
                    )
                    partial_stop_loss = self.strategy.params.get(
                        "partial_stop_loss", []
                    )

                    # 1. 트레일링 스탑
                    if trailing_stop_pct is not None:
                        drawdown_from_max = (
                            (current_price - max_price) / max_price * 100.0
                            if max_price > 0
                            else 0
                        )
                        if drawdown_from_max <= trailing_stop_pct:
                            logger.info(
                                f"[{self.name}] 📉 트레일링 스탑 발동: {ticker} (최점 대비 {drawdown_from_max:.2f}% <= {trailing_stop_pct}%)"
                            )
                            ts_signal = Signal(
                                type=SignalType.SELL,
                                ticker=ticker,
                                reason=f"트레일링 스탑 (수익률 {profit_pct:.2f}%)",
                                strength=1.0,
                            )
                            self._execute_sell(ticker, current_price, ts_signal)
                            continue

                    # 2. 강제 익절
                    if profit_pct >= take_profit_pct:
                        logger.info(
                            f"[{self.name}] 🎯 강제 익절 발동: {ticker} (수익률 {profit_pct:.2f}% >= {take_profit_pct}%)"
                        )
                        tp_signal = Signal(
                            type=SignalType.SELL,
                            ticker=ticker,
                            reason=f"강제 익절 (수익률 {profit_pct:.2f}%)",
                            strength=1.0,
                        )
                        self._execute_sell(ticker, current_price, tp_signal)
                        continue

                    # 3. 분할 강제 손절
                    sl_triggered = False
                    if partial_stop_loss:
                        for sl_stage in partial_stop_loss:
                            stage_pct = sl_stage.get("pct", stop_loss_pct)
                            stage_strength = sl_stage.get("strength", 1.0)
                            sl_levels_hit = holdings[ticker].get("sl_levels_hit", [])

                            if (
                                profit_pct <= stage_pct
                                and stage_pct not in sl_levels_hit
                            ):
                                logger.error(
                                    f"[{self.name}] 🚨 분할 손절 발동 [{stage_pct}%]: {ticker} (수익률 {profit_pct:.2f}% <= {stage_pct}%) 비율: {stage_strength*100}%"
                                )
                                self.portfolio_manager.update_holding_metadata(
                                    self.name, ticker, hit_sl_level=stage_pct
                                )
                                sl_signal = Signal(
                                    type=SignalType.SELL,
                                    ticker=ticker,
                                    reason=f"분할 손절 단계 {stage_pct}% (현재 {profit_pct:.2f}%)",
                                    strength=stage_strength,
                                )
                                self._execute_sell(ticker, current_price, sl_signal)
                                sl_triggered = True
                                break  # 루프에서 하나만 처리

                    if sl_triggered:
                        continue

                    # 단일 기본 손절
                    if not partial_stop_loss and profit_pct <= stop_loss_pct:
                        logger.warning(
                            f"[{self.name}] 🚨 강제 손절 발동: {ticker} (수익률 {profit_pct:.2f}% <= {stop_loss_pct}%)"
                        )
                        sl_signal = Signal(
                            type=SignalType.SELL,
                            ticker=ticker,
                            reason=f"강제 손절 (수익률 {profit_pct:.2f}%)",
                            strength=1.0,
                        )
                        self._execute_sell(ticker, current_price, sl_signal)
                        continue

            signal = self.strategy.evaluate(
                ticker, setup_market_data[ticker], market_data, portfolio_info
            )

            # SELL 시그널은 즉시 실행 (보유 종목만)
            if signal.type == SignalType.SELL:
                if holdings and ticker in holdings and holdings[ticker]["volume"] > 0:
                    logger.info(f"[{self.name}] {signal}")
                    self._execute_sell(ticker, current_price, signal)

            # BUY 시그널은 후보로 수집 (가장 강한 것만 나중에 실행)
            elif signal.type == SignalType.BUY:
                if kill_switch_active:
                    continue
                # 현금 부족이면 스킵
                if available_cash < self.MIN_ORDER_AMOUNT:
                    continue
                # 이미 보유 중이면 스킵
                if holdings and ticker in holdings and holdings[ticker]["volume"] > 0:
                    continue
                # 더 강한 시그널이면 교체
                if best_buy is None or signal.strength > best_buy[0].strength:
                    best_buy = (signal, market_data)

        # 가장 강한 매수 시그널 1개만 실행
        if best_buy:
            signal, market_data = best_buy
            ticker = market_data.get("ticker", "Unknown")
            current_price = float(market_data.get("current_price", 0.0))
            logger.info(f"[{self.name}] 🏆 싸이클 최선 매수 → {signal}")
            self._execute_buy(ticker, current_price, signal)

    def _execute_buy(self, ticker: str, current_price: float, signal) -> None:
        """매수 실행 (PortfolioManager 연동)"""
        if not self.broker.is_configured():
            return

        # 투자금 계산: 포트폴리오 매니저의 가용 현금 × 시그널 강도
        if self.portfolio_manager:
            available_cash = self.portfolio_manager.get_available_cash(self.name)
            order_amount = available_cash * signal.strength

            # 전략 파라미터에서 손절률 가져옴 (명시적이지 않으면 기본 -5.0%)
            stop_loss_pct = self.strategy.params.get("stop_loss_pct", -5.0)
            loss_ratio = abs(float(stop_loss_pct)) / 100.0
            if loss_ratio >= 1.0:
                loss_ratio = 0.99

            # 손절 발생 시에도 최소 5000원이 남도록 역산. 약간의 슬리피지/마진(1%) 포함
            dynamic_min_amount = (self.MIN_ORDER_AMOUNT / (1.0 - loss_ratio)) * 1.01

            # 시그널 강도 적용 후 최소 주문 금액 미달이지만 현금은 충분한 경우 → 최소 금액으로 보정
            if order_amount < dynamic_min_amount:
                if available_cash >= dynamic_min_amount:
                    order_amount = dynamic_min_amount
                else:
                    order_amount = available_cash
        else:
            # 폴백: 고정 금액
            order_amount = current_price * 0.001

        if order_amount < self.MIN_ORDER_AMOUNT:
            logger.warning(
                f"[{self.name}] ⚠️ 주문 금액({order_amount:,.0f} KRW)이 최소 기준({self.MIN_ORDER_AMOUNT:,} KRW) 미달 → 매수 취소"
            )
            return

        logger.info(
            f"[{self.name}] 🟢 매수 실행: {ticker} | 금액: {order_amount:,.0f} KRW"
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
            order_info = None
            if uuid_str:
                for _ in range(5):
                    time.sleep(0.5)
                    order_info = self.broker.get_order(uuid_str)
                    if "error" not in order_info and order_info.get("state") in (
                        "done",
                        "cancel",
                    ):
                        break

            executed_volume = order_amount / current_price  # fallback
            executed_funds = order_amount
            paid_fee = 0.0

            if order_info and "error" not in order_info:
                try:
                    api_executed_vol = float(order_info.get("executed_volume", 0))
                    if api_executed_vol > 0:
                        executed_volume = api_executed_vol

                    trades = order_info.get("trades", [])
                    funds_sum = sum(float(t.get("funds", 0)) for t in trades)
                    if funds_sum > 0:
                        executed_funds = funds_sum

                    paid_fee = float(order_info.get("paid_fee", 0))
                except Exception as e:
                    logger.error(f"[{self.name}] 체결 내역 파싱 오류: {e}")

                if self.portfolio_manager and order_info.get("state") == "done":
                    self.portfolio_manager.record_buy(
                        agent_name=self.name,
                        ticker=ticker,
                        volume=executed_volume,
                        price=current_price,
                        executed_funds=executed_funds,
                        paid_fee=paid_fee,
                    )

            append_markdown(
                self.trades_path,
                f"- [{self.strategy.name}] 매수: {ticker} | 금액: {order_amount:,.0f} KRW | 사유: {signal.reason} | Res: {order_info or res}",
            )

    def _execute_sell(self, ticker: str, current_price: float, signal) -> None:
        """매도 실행 (PortfolioManager 연동)"""
        if not self.broker.is_configured():
            return

        # 보유 수량 확인
        if self.portfolio_manager:
            holdings = self.portfolio_manager.get_holdings(self.name)
            if ticker not in holdings or holdings[ticker]["volume"] <= 0:
                logger.info(
                    f"[{self.name}] 매도 시그널이나 {ticker} 보유 수량 없음 → 매도 생략"
                )
                return
            held_volume = holdings[ticker]["volume"]
            sell_volume = held_volume * signal.strength
            # 분할 매도 후 잔여 금액이 최소 주문 기준(5,000 KRW) 미만이면 전량 매도
            remaining_volume = held_volume - sell_volume
            remaining_value = remaining_volume * current_price
            if remaining_value < 5000 and remaining_volume > 0:
                logger.warning(
                    f"[{self.name}] ⚠️ 잔여 평가액({remaining_value:,.0f} KRW)이 최소 주문 기준 미달 → 전량 매도로 전환"
                )
                sell_volume = held_volume
        else:
            # 폴백: 실제 잔고 확인
            balances = self.broker.get_balances()
            currency = ticker.split("-")[1] if "-" in ticker else ticker
            held_volume = 0.0
            for b in balances:
                if b.get("currency") == currency:
                    held_volume = float(b.get("balance", "0"))
                    break
            if held_volume <= 0:
                logger.info(f"[{self.name}] 매도 시그널이나 보유 수량 없음 → 매도 생략")
                return
            sell_volume = held_volume * signal.strength
            # 분할 매도 후 잔여 금액이 최소 주문 기준(5,000 KRW) 미만이면 전량 매도
            remaining_volume = held_volume - sell_volume
            remaining_value = remaining_volume * current_price
            if remaining_value < 5000 and remaining_volume > 0:
                logger.warning(
                    f"[{self.name}] ⚠️ 잔여 평가액({remaining_value:,.0f} KRW)이 최소 주문 기준 미달 → 전량 매도로 전환"
                )
                sell_volume = held_volume

        estimated_value = sell_volume * current_price

        if estimated_value < 5000:
            logger.warning(
                f"[{self.name}] ⚠️ 매도 예상 평가액({estimated_value:,.0f} KRW) 미달."
            )

            if self.portfolio_manager:
                logger.info(
                    f"[{self.name}] 🔄 타 에이전트(주식 보유 중)에게 내부 이관 시도 중..."
                )
                success = self.portfolio_manager.transfer_holdings_internally(
                    seller=self.name,
                    ticker=ticker,
                    volume=sell_volume,
                    current_price=current_price,
                )

                if success:
                    logger.info(
                        f"[{self.name}] ✅ 내부 이관 성공! 업비트 매도(place_order) 생략."
                    )
                    # 내부 장부 정산이 이미 끝났으므로 매도 프로세스 즉시 종료
                    append_markdown(
                        self.trades_path,
                        f"- [{self.strategy.name}] 내부 매각(이관): {ticker} | 수량: {sell_volume:.6f} | 사유: {signal.reason}",
                    )
                    return
                else:
                    logger.error(
                        f"[{self.name}] ❌ 이관 실패(여유 에이전트 없음) → 매도 취소"
                    )
            else:
                logger.error(
                    f"[{self.name}] ❌ 포트폴리오 매니저 미설정 → 최소 금액 미달로 매도 취소"
                )

            # PM에 팬텀 보유량이 있을 수 있으므로 실제 잔고 확인 후 정리
            if self.portfolio_manager:
                currency = ticker.split("-")[1] if "-" in ticker else ticker
                actual_balance = 0.0
                try:
                    for b in self.broker.get_balances():
                        if b.get("currency") == currency:
                            actual_balance = float(b.get("balance", "0"))
                            break
                except Exception:
                    pass
                if actual_balance <= 0:
                    # 실제 잔고 없음 → PM 보유량 정리 (팬텀 제거)
                    holdings = self.portfolio_manager.get_holdings(self.name)
                    if ticker in holdings and holdings[ticker]["volume"] > 0:
                        phantom_vol = holdings[ticker]["volume"]
                        logger.info(
                            f"[{self.name}] 🔄 {ticker} 실제 잔고 0 → PM 팬텀 보유량({phantom_vol:.6f}) 정리"
                        )
                        self.portfolio_manager.record_sell(
                            self.name, ticker, phantom_vol, current_price
                        )
            return

        # 실제 Upbit 잔고 확인 → 내부 추적 수량과 불일치 시 보정
        currency = ticker.split("-")[1] if "-" in ticker else ticker
        actual_balance = 0.0
        try:
            balances = self.broker.get_balances()
            for b in balances:
                if b.get("currency") == currency:
                    actual_balance = float(b.get("balance", "0"))
                    break
        except Exception as e:
            logger.error(f"[{self.name}] ⚠️ 실제 잔고 조회 실패: {e}")

        if actual_balance <= 0:
            logger.warning(f"[{self.name}] ⚠️ {ticker} 실제 Upbit 잔고 없음 → 매도 취소")
            # PM 보유량도 정리
            if self.portfolio_manager:
                holdings = self.portfolio_manager.get_holdings(self.name)
                if ticker in holdings and holdings[ticker]["volume"] > 0:
                    phantom_vol = holdings[ticker]["volume"]
                    logger.info(
                        f"[{self.name}] 🔄 {ticker} PM 팬텀 보유량({phantom_vol:.6f}) 정리"
                    )
                    self.portfolio_manager.record_sell(
                        self.name, ticker, phantom_vol, current_price
                    )
            return

        # PM 추적 수량 기억 (매도 후 PM 기록 보정용)
        pm_tracked_volume = sell_volume
        if sell_volume > actual_balance:
            logger.warning(
                f"[{self.name}] ⚠️ 매도 수량({sell_volume:.6f}) > 실제 잔고({actual_balance:.6f}) → 실제 잔고로 보정"
            )
            sell_volume = actual_balance

        logger.info(f"[{self.name}] 🔴 매도 실행: {ticker} | 수량: {sell_volume:.6f}")
        res = self.broker.place_order(
            ticker,
            "ask",
            volume=str(sell_volume),
            ord_type="market",
            current_price=current_price,
        )

        if res and "error" not in res:
            uuid_str = res.get("uuid")
            order_info = None
            if uuid_str:
                for _ in range(5):
                    time.sleep(0.5)
                    order_info = self.broker.get_order(uuid_str)
                    if "error" not in order_info and order_info.get("state") in (
                        "done",
                        "cancel",
                    ):
                        break

            executed_funds = sell_volume * current_price  # fallback
            paid_fee = 0.0

            if order_info and "error" not in order_info:
                try:
                    paid_fee = float(order_info.get("paid_fee", 0))

                    trades = order_info.get("trades", [])
                    funds_sum = sum(float(t.get("funds", 0)) for t in trades)
                    if funds_sum > 0:
                        executed_funds = funds_sum

                except Exception as e:
                    logger.error(f"[{self.name}] 체결 내역 파싱 오류: {e}")

                if self.portfolio_manager and order_info.get("state") == "done":
                    self.portfolio_manager.record_sell(
                        agent_name=self.name,
                        ticker=ticker,
                        volume=pm_tracked_volume,
                        price=current_price,
                        executed_funds=executed_funds,
                        paid_fee=paid_fee,
                    )

            append_markdown(
                self.trades_path,
                f"- [{self.strategy.name}] 매도: {ticker} | 수량: {sell_volume:.6f} | 사유: {signal.reason} | Res: {order_info or res}",
            )
