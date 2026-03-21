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
            "bullish": ["Breakout", "PullbackTrend"],
            "ranging": ["VWAPReversion", "MeanReversion"],
            "volatile": ["Breakout"],
            "bearish": ["Bearish"],
            "panic": ["Panic"],
        }

        # 마지막 싸이클의 종목별 평가 결과 저장
        self.last_ticker_stats = {}

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
        current_prices = {}
        if self.portfolio_manager:
            # 보유 종목들의 현재가를 수집하기 위함
            holdings = self.portfolio_manager.get_holdings(self.name)
            for ticker in holdings:
                if ticker in entry_market_data:
                    current_prices[ticker] = float(
                        entry_market_data[ticker].close.iloc[-1]
                    )

            portfolio_info = self.portfolio_manager.get_portfolio_summary(
                self.name, current_prices=current_prices
            )
            available_cash = self.portfolio_manager.get_available_cash(self.name)
            holdings = self.portfolio_manager.get_holdings(self.name)
        else:
            available_cash = float("inf")
            holdings = {}

        ticker_stats = {}
        for ticker, market_data in entry_market_data.items():
            current_price = float(market_data.close.iloc[-1])
            is_held = ticker in holdings and holdings[ticker]["volume"] > 0

            # 매도 판단 전에 전역 손절/익절/트레일링 스탑 검사 우선
            if is_held:
                risk_signal = self.risk_manager.evaluate_risk(
                    self.name, ticker, current_price
                )
                if risk_signal:
                    self._execute_sell(
                        "RiskManager", ticker, current_price, risk_signal
                    )
                    continue

            # 종목별 Regime 판독 및 전략 할당
            ticker_regime = None
            if market_data is not None and not market_data.empty:
                try:
                    ticker_regime = self.broker.regime_detect(ticker, market_data)
                except Exception as e:
                    print(ticker, e)

            target_strategy_names = self.strategy_map.get(ticker_regime, None)
            if target_strategy_names is None:
                ticker_stats[ticker] = {
                    "ticker": ticker,
                    "regime": ticker_regime,
                    "strategy": "N/A",
                    "signal_type": "HOLD",
                    "signal_reason": "N/A",
                    "signal_strength": 0,
                    "current_price": current_price,
                }
                continue

            best_signal = None
            for strategy_name in target_strategy_names:
                strategy = self.strategy_manager.get_strategy(strategy_name)

                signal = strategy.evaluate(
                    ticker,
                    setup_market_data.get(ticker),
                    market_data,
                    portfolio_info,
                )

                if best_signal is None or signal.strength > best_signal.strength:
                    best_signal = signal
                    target_strategy_name = strategy_name
            signal = best_signal

            # 통계 수집
            ticker_stats[ticker] = {
                "ticker": ticker,
                "regime": ticker_regime,
                "strategy": target_strategy_name,
                "signal_type": signal.type.value if signal else "HOLD",
                "signal_reason": signal.reason if signal else "N/A",
                "signal_strength": signal.strength if signal else 0,
                "current_price": current_price,
            }

            # SELL 시그널은 즉시 실행 (보유 종목만)
            if signal and signal.type == SignalType.SELL:
                if is_held:
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
                if is_held:
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
            atr = (
                float(market_data_best["atr_14"].iloc[-1])
                if "atr_14" in market_data_best
                else 0.0
            )

            logger.info(f"🏆 Best Buy | {best_buy_strategy.name} → {signal_best}")
            sig_str = signal_best.__str__()
            self.notifier.send_message(f"BUY | {best_buy_strategy.name} → {sig_str}")
            self._execute_buy(
                best_buy_strategy.name, ticker, current_price, signal_best, atr=atr
            )

        # /eval 조회 등을 위해 최근 평가결과를 저장
        self.last_ticker_stats = ticker_stats

        # 리포트 전송
        try:
            self._send_cycle_report(btc_regime, ticker_stats)
            # 포트폴리오 상태 파일(portfolio.md) 업데이트
            if self.portfolio_manager:
                current_prices = {
                    t: s["current_price"] for t, s in ticker_stats.items()
                }
                self.portfolio_manager.export_portfolio_report(
                    self.name, current_prices=current_prices
                )
        except Exception as e:
            logger.error(f"Failed to send cycle report or export portfolio: {e}")

    def _send_cycle_report(self, btc_regime: str, ticker_stats: dict) -> None:
        """
        매 싸이클 결과 요약 리포트를 텔레그램으로 전송합니다.
        """
        if not self.portfolio_manager:
            return

        # 최신 가격 정보를 반영한 요약 정보 가져오기
        current_prices = {t: s["current_price"] for t, s in ticker_stats.items()}
        summary = self.portfolio_manager.get_portfolio_summary(
            self.name, current_prices=current_prices
        )
        if not summary:
            return

        # 1. BTC Regime & 기본 자산 정보
        msg = f"📊 **Hermes Investment Report**\n"
        msg += f"🌐 BTC Regime: {btc_regime.upper()}\n"
        msg += f"💰 총 자산: {summary['total_value']:,.0f} KRW\n"
        msg += f"💵 현금 자산: {summary['cash']:,.0f} KRW ({summary['return_rate']:+.2f}%)\n\n"

        # 2. 보유 종목 투자금, 수익률, 수익금
        holdings = summary.get("holdings", {})
        if holdings:
            msg += "📦 **보유 종목 현황**\n"
            for ticker, h in holdings.items():
                price = ticker_stats.get(ticker, {}).get(
                    "current_price", h["avg_price"]
                )
                cost = h["total_cost"]
                val = h["volume"] * price
                pnl = val - cost
                roi = (pnl / cost * 100) if cost > 0 else 0

                msg += f"• {ticker}: {cost:,.0f} → {roi:+.2f}% ({pnl:+,.0f})\n"
            msg += "\n"

        # 3. 전략별 코인 사항 (Regime, Signal)
        if ticker_stats:
            msg += "⚙️ **전략별 모니터링 (주요)**\n"
            # 시그널이 발생한 종목(BUY/SELL) 우선, 그 다음은 강도순
            sorted_stats = sorted(
                ticker_stats.values(),
                key=lambda x: (x["signal_type"] != "HOLD", x["signal_strength"]),
                reverse=True,
            )

            for stat in sorted_stats[:5]:  # 상위 5개만
                t = stat["ticker"]
                r = stat["regime"]
                s = stat["strategy"]
                st = stat["signal_type"]
                sr = stat["signal_reason"]
                msg += f"• {t} [{r}]: {s} → {st}\n  └ {sr}\n"
            msg += "\n"

        # 4. 추가 추천 내용
        msg += "💡 **AI 추천 & 인사이트**\n"
        if btc_regime in ["bearish", "panic"]:
            msg += (
                "⚠️ 시장이 침체기입니다. 현금 비중을 유지하며 보수적으로 접근하세요.\n"
            )
        elif btc_regime == "bullish":
            msg += "🚀 시장이 강세입니다. 추세 추종 전략이 유효할 가능성이 큽니다.\n"
        else:
            msg += (
                "⏸️ 시장이 횡보 중입니다. 박스권 매매나 돌파를 기다리는 것이 좋습니다.\n"
            )

        # 가장 높은 강도의 매수 시그널 추천
        top_buys = sorted(
            [v for v in ticker_stats.values() if v.get("signal_type") == "BUY"],
            key=lambda x: x.get("signal_strength", 0),
            reverse=True,
        )
        if top_buys:
            msg += f"🎯 관심 종목: {top_buys[0]['ticker']} (강도: {top_buys[0]['signal_strength']:.0%})\n"

        self.notifier.send_message(msg)

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
        self,
        strategy_name: str,
        ticker: str,
        current_price: float,
        signal,
        atr: float = 0.0,
    ) -> None:
        """매수 실행 (PortfolioManager 연동)"""
        if not self.broker.is_configured():
            return

        # 투자금 계산: 포트폴리오 매니저의 가용 현금 × 시그널 강도
        if self.portfolio_manager:
            available_cash = self.portfolio_manager.get_available_cash(self.name)
            # order_amount = available_cash * signal.strength

            portfolio_value = self.portfolio_manager.get_total_value(self.name)

            # =========== (Phase 5) 변동성 포지션 사이징 ===========
            # 타겟 리스크 = 전체 자산의 2% (한 번 매매에서 감수할 최대 손실 원금 비중)
            target_risk_pct = 0.02
            trade_risk_pct = 0.05  # 기본 5% 리스크

            if atr > 0:
                atr_pct = atr / current_price
                # dynamic_sl은 ATR의 2.5배. 즉 이 거래의 퍼센트 손실폭은 atr*2.5
                trade_risk_pct = max(0.03, min(0.15, atr_pct * 2.5))

            # 투입 기준 자금 = 전체 자산 * (타겟 리스크 / 본 거래의 손실폭)
            base_position_size = portfolio_value * (target_risk_pct / trade_risk_pct)

            # 단일 코인 집중 투자(몰빵)를 막기 위해 상한선(MAX_POSITIONS) 적용
            max_allowed = portfolio_value / self.MAX_POSITIONS
            base_position_size = min(base_position_size, max_allowed, available_cash)

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

        stop_loss_pct = self.risk_manager.risk_params.get("stop_loss_pct", -5.0)
        if atr > 0:
            atr_pct = (atr / current_price) * 100.0
            stop_loss_pct = -max(3.0, min(15.0, atr_pct * 2.5))

        # =========== (Phase 5) 호가창 불균형(Orderbook Imbalance) 필터 ===========
        orderbooks = self.broker.get_orderbook(ticker)
        if orderbooks and len(orderbooks) > 0:
            ob = orderbooks[0]
            total_ask = ob.get("total_ask_size", 0)
            total_bid = ob.get("total_bid_size", 0)

            # 매도잔량이 매수잔량의 0.7배 미만이면 (위에 뚫을 매물벽이 없이 얇으면)
            # 마켓메이커가 물량을 아래(Bid)에 깔고 위에서 패대기(Dump)를 칠 확률이 높음. 가짜 돌파 혐의.
            if total_ask < total_bid * 0.7:
                logger.warning(
                    f"🚫 [Orderbook Filter] {ticker} 매도 잔고({total_ask:.2f})가 매수 잔고({total_bid:.2f})에 비해 너무 얇습니다. 가짜 돌파 혐의 진입 기각."
                )
                return

        logger.info(
            f"🟢 매수 실행: {ticker} | 금액: {order_amount:,.0f} KRW | SL: {stop_loss_pct:.1f}% | Target Price: CP {current_price:,.2f} | ATR: {atr:.4f}"
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
                for _ in range(20):
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

                    if atr > 0:
                        self.portfolio_manager.update_holding_metadata(
                            self.name, ticker, atr_14=atr
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
            if remaining_value < self.MIN_ORDER_AMOUNT and remaining_volume > 0:
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
            if remaining_value < self.MIN_ORDER_AMOUNT and remaining_volume > 0:
                logger.warning(
                    f"⚠️ 잔여 평가액({remaining_value:,.0f} KRW)이 최소 주문 기준 미달 → 전량 매도로 전환"
                )
                sell_volume = held_volume

        estimated_value = sell_volume * current_price

        if estimated_value < self.MIN_ORDER_AMOUNT:
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
                for _ in range(20):
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
