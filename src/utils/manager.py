import time
from src.utils.broker_api import UpbitBroker
from src.strategies.base import SignalType
from src.utils.logger import logger
from src.strategies.strategy_manager import StrategyManager
from src.utils.risk_manager import RiskManager
from src.utils.telegram_notifier import TelegramNotifier
from src.utils.execution_manager import ExecutionManager


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
        self.execution_manager = ExecutionManager(
            self.broker, self.portfolio_manager, self.notifier
        )

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
        market_regime: str = "ranging",
    ) -> None:
        """
        [싸이클 단위 실행]
        전체 종목을 평가하여 매도는 모두 실행하되,
        매수는 가장 강한 시그널의 1종목만 실행합니다.
        """
        self.notifier.start_buffering()
        self.notifier.send_message("[log]")
        self.execution_manager.check_pending_orders()
        if not setup_market_data or not entry_market_data:
            self.notifier.flush_buffer()
            return

        # 중지 상태 체크
        if self.portfolio_manager and self.portfolio_manager.is_halted(self.name):
            self.notifier.flush_buffer()
            return

        # 거시 시장 매수 필터 (하락/패닉일 경우 신규 매수 차단)
        buy_filter_passed = market_regime not in ["bearish", "panic"]
        if not buy_filter_passed:
            logger.info(
                f"⚠️ 거시 시장 침체({market_regime}): 신규 매수 차단, 매도만 수행합니다."
            )

        buy_candidates = []

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
                    risk_signal_str = risk_signal.__str__()
                    log = f"⚡[Risk Manager]\n{risk_signal_str}"
                    logger.warning(log)
                    # self.notifier.send_message(log)
                    self.execution_manager.execute_sell(
                        self.name, ticker, current_price, risk_signal
                    )
                    continue

            # 종목별 Regime 판독 및 전략 할당
            ticker_regime = None
            if market_data is not None and not market_data.empty:
                try:
                    ticker_regime = self.broker.regime_detect(ticker, market_data)
                except Exception as e:
                    print(ticker, e)

            target_strategy_names = None
            if is_held:
                saved_strategy = holdings[ticker].get("strategy")
                if saved_strategy and saved_strategy != "Unknown":
                    target_strategy_names = [saved_strategy]

            if target_strategy_names is None:
                target_strategy_names = self.strategy_map.get(ticker_regime, None)

            if target_strategy_names is None:
                ticker_stats[ticker] = {
                    "ticker": ticker,
                    "regime": ticker_regime,
                    "strategy": "N/A",
                    "signal_type": "HOLD",
                    "signal_reason": "N/A",
                    "signal_strength": 0,
                    "signal_confidence": -1,
                    "current_price": current_price,
                }
                continue

            signal = None
            strategy = None
            for strategy_name in target_strategy_names:
                strategy_tmp = self.strategy_manager.get_strategy(strategy_name)

                signal_tmp = strategy_tmp.evaluate(
                    ticker,
                    setup_market_data.get(ticker),
                    market_data,
                    portfolio_info,
                )

                if signal is None or signal_tmp.strength > signal.strength:
                    signal = signal_tmp
                    strategy = strategy_tmp

            # 통계 수집
            ticker_stats[ticker] = {
                "ticker": ticker,
                "regime": ticker_regime,
                "strategy": strategy.name,
                "signal_type": signal.type.value if signal else "HOLD",
                "signal_reason": signal.reason if signal else "N/A",
                "signal_strength": signal.strength if signal else 0,
                "signal_confidence": signal.confidence if signal else 0,
                "current_price": current_price,
            }

            # SELL 시그널은 즉시 실행 (보유 종목만)
            if signal and signal.type == SignalType.SELL:
                if is_held:
                    sig_str = signal.__str__()
                    self.notifier.send_message(
                        f" - Stretegy : {strategy.name}\n\t{sig_str}"
                    )
                    self.execution_manager.execute_sell(
                        self.name, ticker, current_price, signal
                    )

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
                # 통과한 시그널을 후보 리스트에 수집
                buy_candidates.append((signal, strategy, market_data))

        # 후보들을 confidence 기준 내림차순 정렬
        buy_candidates.sort(key=lambda x: x[0].confidence, reverse=True)

        # 가장 강한 매수 시그널부터 순차 실행 시도 (1개 체결/접수 성공 시 즉시 루프 탈출)
        for cand_signal, cand_strategy, cand_market_data in buy_candidates:
            if cand_signal.confidence <= 0.3:
                break

            ticker = cand_signal.ticker
            current_price = float(cand_market_data.close.iloc[-1])
            atr = (
                float(cand_market_data["atr_14"].iloc[-1])
                if "atr_14" in cand_market_data
                else 0.0
            )

            sig_str = cand_signal.__str__()
            log = f" - Stretegy : {cand_strategy.name}\n\t{sig_str}"
            logger.info(log)
            self.notifier.send_message(log)

            success = self.execution_manager.execute_buy(
                self.name,
                ticker,
                current_price,
                cand_signal,
                self.risk_manager.risk_params,
                atr=atr,
                strategy_name=cand_strategy.name,
            )

            # 성공 시 루프 중단 (단일 종목 매매)
            if success:
                logger.info(
                    f"[ManagerAgent] {ticker} 매수 접수 성공. 후순위 매수 후보 기각."
                )
                break

        # /eval 조회 등을 위해 최근 평가결과를 저장
        self.last_ticker_stats = ticker_stats

        # 이번 사이클 내에서 방금 발주한 거래가 체결 완료됐는지 0.5초 대기 후 마지막 확인
        import time

        time.sleep(0.5)
        self.execution_manager.check_pending_orders()

        # 리포트 전송
        try:
            self._send_cycle_report(market_regime, ticker_stats)
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

        self.notifier.flush_buffer()

    def _send_cycle_report(self, market_regime: str, ticker_stats: dict) -> None:
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

        # 1. Market Regime & 기본 자산 정보
        msg = f"📊 **Hermes Investment Report**\n"
        msg += f"🌐 Market Regime: {market_regime.upper()}\n"
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
            # 시그널이 발생한 종목(BUY/SELL) 우선, 그 다음은 확신도순
            sorted_stats = sorted(
                ticker_stats.values(),
                key=lambda x: (x["signal_type"] != "HOLD", x["signal_confidence"]),
                reverse=True,
            )

            for stat in sorted_stats[:3]:  # 상위 3개만
                t = stat["ticker"]
                r = stat["regime"]
                s = stat["strategy"]
                st = stat["signal_type"]
                sr = stat["signal_reason"]
                ss = stat["signal_strength"]
                sc = stat["signal_confidence"]
                msg += f"• {t} [{r}]: {s} → {st}_{sc:.0%} (비중:{ss:.0%})\n  └ {sr}\n"
            msg += "\n"

        # 4. 추가 추천 내용
        msg += "💡 **AI 추천 & 인사이트**\n"
        if market_regime in ["bearish", "panic"]:
            msg += (
                "⚠️ 시장이 침체기입니다. 현금 비중을 유지하며 보수적으로 접근하세요.\n"
            )
        elif market_regime == "bullish":
            msg += "🚀 시장이 강세입니다. 추세 추종 전략이 유효할 가능성이 큽니다.\n"
        else:
            msg += (
                "⏸️ 시장이 횡보 중입니다. 박스권 매매나 돌파를 기다리는 것이 좋습니다.\n"
            )

        # 가장 높은 확신도의 매수 시그널 추천
        top_buys = sorted(
            [v for v in ticker_stats.values() if v.get("signal_type") == "BUY"],
            key=lambda x: x.get("signal_confidence", 0),
            reverse=True,
        )
        if top_buys:
            msg += f"🎯 관심 종목: {top_buys[0]['ticker']} (확신도: {top_buys[0]['signal_confidence']:.0%})\n"

        self.notifier.send_message(msg)

    def handle_realtime_tick(self, ticker: str, current_price: float) -> None:
        """웹소켓에서 수신한 실시간 틱 데이터를 바탕으로 긴급 손절/익절을 검사합니다."""
        self.execution_manager.check_pending_orders()
        if getattr(self, "portfolio_manager", None) is None:
            return

        holdings = self.portfolio_manager.get_holdings(self.name)
        if ticker not in holdings or holdings[ticker].get("volume", 0) <= 0:
            return

        risk_signal = self.risk_manager.evaluate_risk(self.name, ticker, current_price)
        if risk_signal:
            risk_signal_str = risk_signal.__str__()
            log = f"⚡[Realtime Risk Hook]\n{risk_signal_str}"
            logger.warning(log)
            # self.notifier.send_message(log)
            self.execution_manager.execute_sell(
                self.name, ticker, current_price, risk_signal
            )
