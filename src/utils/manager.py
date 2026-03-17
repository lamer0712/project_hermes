import time
from src.utils.broker_api import UpbitBroker
from src.strategies.base import SignalType
from src.utils.logger import logger
from src.strategies.strategy_manager import StrategyManager
from src.utils.risk_manager import RiskManager
from src.utils.telegram_notifier import TelegramNotifier


class ManagerAgent:
    def __init__(
        self,
        name: str = "manager",
        portfolio_manager=None,
    ):
        self.name = name
        self.portfolio_manager = portfolio_manager
        self.agent_dir = f"manager"
        self.broker = UpbitBroker()
        self.strategy_manager = StrategyManager()
        self.risk_manager = RiskManager(self.portfolio_manager)
        self.notifier = TelegramNotifier()

        # 시장 Regime에 따른 매핑 (기본값)
        self.strategy_map = {
            "bullish": "PullbackTrend",
            "ranging": "MeanReversion",
            "volatile": "Breakout",
        }

    # 업비트 최소 주문 금액
    MIN_ORDER_AMOUNT = 5000
    MAX_POSITION_RATIO = 0.3
    MAX_POSITIONS = 5

    def execute_cycle(
        self,
        setup_market_data: dict,
        entry_market_data: dict,
        btc_regime: str = "ranging",
    ) -> None:
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

        # 거시 시장 매수 필터 (하락/패닉일 경우 신규 매수 차단)
        buy_filter_passed = btc_regime not in ["bearish", "panic"]
        if not buy_filter_passed:
            logger.info(
                f"⚠️ 거시 시장 침체({btc_regime}): 신규 매수 차단, 매도만 수행합니다."
            )

        best_buy = None  # (signal, market_data)
        best_buy_strategy = None

        portfolio_info = None
        if self.portfolio_manager:
            portfolio_info = self.portfolio_manager.get_portfolio_summary(self.name)
            available_cash = self.portfolio_manager.get_available_cash(self.name)
            holdings = self.portfolio_manager.get_holdings(self.name)
        else:
            available_cash = float("inf")
            holdings = {}

        msg_cycle = {
            "SELL": {k: [] for k in self.strategy_manager.list_strategies()},
            "BUY": {k: [] for k in self.strategy_manager.list_strategies()},
        }
        for ticker, market_data in entry_market_data.items():
            current_price = float(market_data.close.iloc[-1])

            # 종목별 Regime 판독 및 전략 할당
            ticker_regime = None
            if market_data is not None and not market_data.empty:
                ticker_regime = self.broker.regime_detect(ticker, market_data)

            target_strategy_name = self.strategy_map.get(ticker_regime, None)
            if target_strategy_name is None:
                continue

            strategy = self.strategy_manager.get_strategy(target_strategy_name)
            is_hold = holdings and ticker in holdings and holdings[ticker]["volume"] > 0

            # 매도 판단 전에 전역 손절/익절/트레일링 스탑 검사 우선
            if is_hold:
                risk_signal = self.risk_manager.evaluate_risk(
                    self.name, ticker, current_price
                )
                if risk_signal:
                    self._execute_sell(
                        "RiskManager", ticker, current_price, risk_signal
                    )
                    continue

            signal = strategy.evaluate(
                ticker,
                setup_market_data.get(ticker),
                market_data,
                ticker_regime,
                portfolio_info,
            )

            if is_hold:
                msg_cycle["SELL"][target_strategy_name].append(signal.__str__())
            else:
                msg_cycle["BUY"][target_strategy_name].append(signal.__str__())

            # SELL 시그널은 즉시 실행 (보유 종목만)
            if signal and signal.type == SignalType.SELL:
                if is_hold:
                    sig_str = signal.__str__()
                    self.notifier.send_message(f"SELL | {strategy.name} → {sig_str}")
                    self._execute_sell(strategy.name, ticker, current_price, signal)

            # BUY 시그널은 후보로 수집 (가장 강한 것만 나중에 실행)
            elif signal and signal.type == SignalType.BUY:
                if not buy_filter_passed:
                    continue
                # 현금 부족이면 스킵
                if available_cash < self.MIN_ORDER_AMOUNT:
                    continue
                # 이미 보유 중이면 스킵
                if is_hold:
                    continue
                # 더 강한 시그널이면 교체
                if best_buy is None or signal.strength > best_buy[0].strength:
                    best_buy = (signal, market_data)
                    best_buy_strategy = strategy

        # for k, m in msg_cycle["SELL"].items():
        #     if m:
        #         msg = f"SELL | {k}\n"
        #         msg += "\n".join(m)
        #         self.notifier.send_message(msg)
        # for k, m in msg_cycle["BUY"].items():
        #     if m:
        #         msg = f"BUY | {k}\n"
        #         msg += "\n".join(m)
        #         self.notifier.send_message(msg)

        # 가장 강한 매수 시그널 1개만 실행
        if best_buy and best_buy_strategy:
            signal_best, market_data_best = best_buy
            ticker = signal_best.ticker
            current_price = float(market_data_best.close.iloc[-1])
            logger.info(f"🏆 Best Buy | {best_buy_strategy.name} → {signal_best}")
            sig_str = signal_best.__str__()
            self.notifier.send_message(f"BUY | {best_buy_strategy.name} → {sig_str}")
            self._execute_buy(
                best_buy_strategy.name, ticker, current_price, signal_best
            )

    def handle_realtime_tick(self, ticker: str, current_price: float) -> None:
        """웹소켓에서 수신한 실시간 틱 데이터를 바탕으로 긴급 손절/익절을 검사합니다."""
        if getattr(self, "portfolio_manager", None) is None:
            return

        holdings = self.portfolio_manager.get_holdings(self.name)
        if ticker not in holdings or holdings[ticker].get("volume", 0) <= 0:
            return

        risk_signal = self.risk_manager.evaluate_risk(self.name, ticker, current_price)
        if risk_signal:
            logger.warning(
                f"⚡ [Realtime Risk Hook] 즉각적인 리스크 조건 충족: {ticker} @ {current_price}"
            )
            self._execute_sell("RiskManager", ticker, current_price, risk_signal)

    def _execute_buy(
        self, strategy_name: str, ticker: str, current_price: float, signal
    ) -> None:
        """매수 실행 (PortfolioManager 연동)"""
        if not self.broker.is_configured():
            return

        # 투자금 계산: 포트폴리오 매니저의 가용 현금 × 시그널 강도
        if self.portfolio_manager:
            available_cash = self.portfolio_manager.get_available_cash(self.name)
            # order_amount = available_cash * signal.strength

            portfolio_value = self.portfolio_manager.get_total_value(self.name)

            base_position_size = portfolio_value / self.MAX_POSITIONS
            base_position_size = min(base_position_size, available_cash)

            strength = max(self.MAX_POSITION_RATIO, min(signal.strength, 1.0))

            order_amount = base_position_size * strength

            # 전략 파라미터에서 손절률 가져옴 (명시적이지 않으면 기본 -5.0%)
            stop_loss_pct = self.risk_manager.risk_params.get("stop_loss_pct", -5.0)
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
                f"⚠️ 주문 금액({order_amount:,.0f} KRW)이 최소 기준({self.MIN_ORDER_AMOUNT:,} KRW) 미달 → 매수 취소"
            )
            return

        logger.info(f"🟢 매수 실행: {ticker} | 금액: {order_amount:,.0f} KRW")
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
                    logger.error(f"체결 내역 파싱 오류: {e}")

                if self.portfolio_manager and order_info.get("state") == "done":
                    self.portfolio_manager.record_buy(
                        agent_name=self.name,
                        ticker=ticker,
                        volume=executed_volume,
                        price=current_price,
                        executed_funds=executed_funds,
                        paid_fee=paid_fee,
                    )

    def _execute_sell(
        self, strategy_name: str, ticker: str, current_price: float, signal
    ) -> None:
        """매도 실행 (PortfolioManager 연동)"""
        if not self.broker.is_configured():
            return

        # 보유 수량 확인
        if self.portfolio_manager:
            holdings = self.portfolio_manager.get_holdings(self.name)
            if ticker not in holdings or holdings[ticker]["volume"] <= 0:
                logger.info(f"매도 시그널이나 {ticker} 보유 수량 없음 → 매도 생략")
                return
            held_volume = holdings[ticker]["volume"]
            sell_volume = held_volume * signal.strength
            # 분할 매도 후 잔여 금액이 최소 주문 기준(5,000 KRW) 미만이면 전량 매도
            remaining_volume = held_volume - sell_volume
            remaining_value = remaining_volume * current_price
            if remaining_value < 5000 and remaining_volume > 0:
                logger.warning(
                    f"⚠️ 잔여 평가액({remaining_value:,.0f} KRW)이 최소 주문 기준 미달 → 전량 매도로 전환"
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
                logger.info(f"매도 시그널이나 보유 수량 없음 → 매도 생략")
                return
            sell_volume = held_volume * signal.strength
            # 분할 매도 후 잔여 금액이 최소 주문 기준(5,000 KRW) 미만이면 전량 매도
            remaining_volume = held_volume - sell_volume
            remaining_value = remaining_volume * current_price
            if remaining_value < 5000 and remaining_volume > 0:
                logger.warning(
                    f"⚠️ 잔여 평가액({remaining_value:,.0f} KRW)이 최소 주문 기준 미달 → 전량 매도로 전환"
                )
                sell_volume = held_volume

        estimated_value = sell_volume * current_price

        if estimated_value < 5000:
            logger.warning(f"⚠️ 매도 예상 평가액({estimated_value:,.0f} KRW) 미달.")

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
                            f"🔄 {ticker} 실제 잔고 0 → PM 팬텀 보유량({phantom_vol:.6f}) 정리"
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
            logger.error(f"⚠️ 실제 잔고 조회 실패: {e}")

        if actual_balance <= 0:
            logger.warning(f"⚠️ {ticker} 실제 Upbit 잔고 없음 → 매도 취소")
            # PM 보유량도 정리
            if self.portfolio_manager:
                holdings = self.portfolio_manager.get_holdings(self.name)
                if ticker in holdings and holdings[ticker]["volume"] > 0:
                    phantom_vol = holdings[ticker]["volume"]
                    logger.info(f"🔄 {ticker} PM 팬텀 보유량({phantom_vol:.6f}) 정리")
                    self.portfolio_manager.record_sell(
                        self.name, ticker, phantom_vol, current_price
                    )
            return

        # PM 추적 수량 기억 (매도 후 PM 기록 보정용)
        pm_tracked_volume = sell_volume
        if sell_volume > actual_balance:
            logger.warning(
                f"⚠️ 매도 수량({sell_volume:.6f}) > 실제 잔고({actual_balance:.6f}) → 실제 잔고로 보정"
            )
            sell_volume = actual_balance

        logger.info(f"🔴 매도 실행: {ticker} | 수량: {sell_volume:.6f}")
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
                    logger.error(f"체결 내역 파싱 오류: {e}")

                if self.portfolio_manager and order_info.get("state") == "done":
                    self.portfolio_manager.record_sell(
                        agent_name=self.name,
                        ticker=ticker,
                        volume=pm_tracked_volume,
                        price=current_price,
                        executed_funds=executed_funds,
                        paid_fee=paid_fee,
                    )
