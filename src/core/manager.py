import time
from src.broker.broker_api import UpbitBroker
from src.data.market_data import UpbitMarketData
from src.strategies.base import SignalType
from src.utils.logger import logger
from src.strategies.strategy_manager import StrategyManager
from src.core.risk_manager import RiskManager
from src.communication.telegram_notifier import TelegramNotifier
from src.core.execution_manager import ExecutionManager
from src.core.models import TickerEvaluation, CycleContext
from src.strategies.base import Signal, SignalType


class ManagerAgent:
    """
    투자 사이클을 관리하는 에이전트.

    execute_cycle()을 통해 한 사이클을 실행하며,
    내부적으로 아래 파이프라인 단계를 순차 실행합니다:
      1. _build_cycle_context     — 보유현황·현금·현재가 수집
      2. _evaluate_and_execute_sells — 리스크+전략 평가 → 즉시 매도 + 매수 후보 수집
      3. _select_and_execute_buy  — 최적 매수 1건 실행
      4. _finalize_cycle          — 대기주문 확인 + 리포트 + 저장
    """

    # 업비트 최소 주문 금액
    MIN_ORDER_AMOUNT = 5000
    MAX_POSITION_RATIO = 0.3
    MAX_POSITIONS = 5

    # 시장 Regime에 따른 전략 매핑
    STRATEGY_MAP = {
        "bullish": ["Breakout", "PullbackTrend"],
        "ranging": ["VWAPReversion", "MeanReversion"],
        "volatile": ["Breakout", "VWAPReversion"],
        # "neutral": ["VWAPReversion", "MeanReversion"],
        # "bearish": ["Bearish"],
        # "panic": ["Panic"],
    }

    def __init__(
        self,
        name: str = "crypto_manager",
        portfolio_manager=None,
    ):
        self.name = name
        self.portfolio_manager = portfolio_manager
        self.agent_dir = f"manager/{self.name}"
        self.broker = UpbitBroker()
        self.strategy_manager = StrategyManager()
        self.risk_manager = RiskManager(self.portfolio_manager)
        self.notifier = TelegramNotifier()
        self.execution_manager = ExecutionManager(
            self.broker, self.portfolio_manager, self.notifier
        )

        # 마지막 싸이클의 종목별 평가 결과 저장
        self.last_ticker_stats = {}

    # ──────────────────────────────────────────────
    # 메인 사이클
    # ──────────────────────────────────────────────

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
        self.execution_manager.check_pending_orders()

        if not setup_market_data or not entry_market_data:
            self.notifier.flush_buffer()
            return

        # 중지 상태 체크
        if self.portfolio_manager and self.portfolio_manager.is_halted(self.name):
            self.notifier.flush_buffer()
            return

        # 1. 컨텍스트 구성
        ctx = self._build_cycle_context(entry_market_data, market_regime)

        # 2. 종목별 평가 + 매도 실행 + 매수 후보 수집
        self._evaluate_and_execute_sells(ctx, setup_market_data, entry_market_data)

        # 3. 최적 매수 1건 실행
        self._select_and_execute_buy(ctx)

        # 4. 마무리 (대기주문 확인 + 리포트 + 저장)
        self._finalize_cycle(ctx, market_regime)

    # ──────────────────────────────────────────────
    # 파이프라인 단계
    # ──────────────────────────────────────────────

    def _build_cycle_context(
        self, entry_market_data: dict, market_regime: str
    ) -> CycleContext:
        """보유현황·현금·현재가를 수집하여 CycleContext를 구성합니다."""
        buy_filter_passed = market_regime not in ["bearish", "panic"]
        if not buy_filter_passed:
            logger.info(
                f"⚠️ 거시 시장 침체({market_regime}): 신규 매수 차단, 매도만 수행합니다."
            )

        if self.portfolio_manager:
            holdings = self.portfolio_manager.get_holdings(self.name)
            current_prices = {}
            for ticker in holdings:
                if ticker in entry_market_data:
                    current_prices[ticker] = float(
                        entry_market_data[ticker].close.iloc[-1]
                    )

            portfolio_info = self.portfolio_manager.get_portfolio_summary(
                self.name, current_prices=current_prices
            )
            available_cash = self.portfolio_manager.get_available_cash(self.name)
        else:
            holdings = {}
            current_prices = {}
            portfolio_info = None
            available_cash = float("inf")

        return CycleContext(
            agent_name=self.name,
            market_regime=market_regime,
            buy_filter_passed=buy_filter_passed,
            available_cash=available_cash,
            holdings=holdings,
            portfolio_info=portfolio_info,
            current_prices=current_prices,
        )

    def _evaluate_ticker(
        self,
        ctx: CycleContext,
        ticker: str,
        setup_df,
        entry_df,
    ) -> TickerEvaluation:
        """
        단일 종목에 대해 리스크 평가 → 전략 평가를 수행합니다.
        리스크 시그널이 발생하면 즉시 매도를 실행하고 결과를 반환합니다.
        """
        current_price = float(entry_df.close.iloc[-1])
        is_held = ticker in ctx.holdings and ctx.holdings[ticker]["volume"] > 0

        # ── 리스크 매니저 우선 평가 ──
        if is_held:
            risk_signal = self.risk_manager.evaluate_risk(
                self.name, ticker, current_price
            )
            if risk_signal:
                risk_signal_str = risk_signal.__str__()
                log = f"⚡[Risk Manager]\n{risk_signal_str}"
                logger.warning(log)
                self.execution_manager.execute_sell(
                    self.name, ticker, current_price, risk_signal
                )
                return TickerEvaluation(
                    ticker=ticker,
                    regime=None,
                    strategy="RiskManager",
                    signal_type="SELL",
                    signal_reason=risk_signal.reason,
                    signal_strength=risk_signal.strength,
                    signal_confidence=risk_signal.confidence,
                    current_price=current_price,
                )

        # ── 종목별 Regime 판독 ──
        ticker_regime = None
        if entry_df is not None and not entry_df.empty:
            try:
                ticker_regime = UpbitMarketData.regime_detect(ticker, entry_df)
            except Exception as e:
                print(ticker, e)

        # ── 전략 선택 ──
        target_strategy_names = None
        if is_held:
            saved_strategy = ctx.holdings[ticker].get("strategy")
            if saved_strategy and saved_strategy != "Unknown":
                target_strategy_names = [saved_strategy]

        if target_strategy_names is None:
            target_strategy_names = self.STRATEGY_MAP.get(ticker_regime, None)

        if target_strategy_names is None:
            return TickerEvaluation(
                ticker=ticker,
                regime=ticker_regime,
                strategy="N/A",
                signal_type="HOLD",
                signal_reason="N/A",
                signal_strength=0,
                signal_confidence=-1,
                current_price=current_price,
            )

        # ── 전략 평가 (최고 confidence 선택) ──
        signal = None
        strategy = None
        for strategy_name in target_strategy_names:
            # volatile regime에서 Breakout은 거래량 조건 강화
            override_params = None
            if ticker_regime == "volatile" and strategy_name == "Breakout":
                override_params = {
                    "entry": {
                        "timeframe": "15m",
                        "volume_multiplier": 1.8,
                        "breakout_buffer": 0.002,
                    }
                }
            strategy_tmp = self.strategy_manager.get_strategy(
                strategy_name, override_params
            )

            signal_tmp = strategy_tmp.evaluate(
                ticker,
                setup_df,
                entry_df,
                ctx.portfolio_info,
            )

            if signal is None or signal_tmp.confidence > signal.confidence:
                signal = signal_tmp
                strategy = strategy_tmp

        return TickerEvaluation(
            ticker=ticker,
            regime=ticker_regime,
            strategy=strategy.name,
            signal_type=signal.type.value if signal else "HOLD",
            signal_reason=signal.reason if signal else "N/A",
            signal_strength=signal.strength if signal else 0,
            signal_confidence=signal.confidence if signal else 0,
            current_price=current_price,
        )

    def _evaluate_and_execute_sells(
        self,
        ctx: CycleContext,
        setup_market_data: dict,
        entry_market_data: dict,
    ) -> None:
        """모든 종목에 대해 평가를 수행하고, SELL 시그널은 즉시 실행, BUY는 후보에 수집합니다."""
        for ticker, entry_df in entry_market_data.items():
            setup_df = setup_market_data.get(ticker)

            evaluation = self._evaluate_ticker(ctx, ticker, setup_df, entry_df)
            ctx.ticker_stats[ticker] = evaluation

            # 리스크 매니저가 이미 처리한 경우 스킵
            if evaluation.strategy == "RiskManager":
                continue

            current_price = evaluation.current_price
            is_held = ticker in ctx.holdings and ctx.holdings[ticker]["volume"] > 0

            # SELL 시그널은 즉시 실행
            if evaluation.signal_type == "SELL" and is_held:
                # 원본 signal 객체를 재구성

                signal = Signal(
                    type=SignalType.SELL,
                    ticker=ticker,
                    reason=evaluation.signal_reason,
                    strength=evaluation.signal_strength,
                    confidence=evaluation.signal_confidence,
                )
                sig_str = signal.__str__()
                self.notifier.send_message(
                    f" - Strategy : {evaluation.strategy}\n\t{sig_str}"
                )
                self.execution_manager.execute_sell(
                    self.name, ticker, current_price, signal
                )

            # BUY 시그널은 후보로 수집
            elif evaluation.signal_type == "BUY":
                if not ctx.buy_filter_passed:
                    continue
                if ctx.available_cash < self.MIN_ORDER_AMOUNT:
                    continue
                if is_held:
                    continue

                signal = Signal(
                    type=SignalType.BUY,
                    ticker=ticker,
                    reason=evaluation.signal_reason,
                    strength=evaluation.signal_strength,
                    confidence=evaluation.signal_confidence,
                )
                # 전략을 다시 로드 (매수 실행에 필요)
                strategy = self.strategy_manager.get_strategy(evaluation.strategy)
                ctx.buy_candidates.append((signal, strategy, entry_df))

    def _select_and_execute_buy(self, ctx: CycleContext) -> None:
        """수집된 매수 후보 중 최적 1건을 실행합니다."""
        # confidence 기준 내림차순 정렬
        ctx.buy_candidates.sort(key=lambda x: x[0].confidence, reverse=True)

        for cand_signal, cand_strategy, cand_market_data in ctx.buy_candidates:
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
            log = f" - Strategy : {cand_strategy.name}\n\t{sig_str}"
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

            if success:
                logger.info(
                    f"[ManagerAgent] {ticker} 매수 접수 성공. 후순위 매수 후보 기각."
                )
                break

    def _finalize_cycle(self, ctx: CycleContext, market_regime: str) -> None:
        """대기주문 확인, 리포트 전송, 포트폴리오 상태 저장을 수행합니다."""
        # dict 변환 (기존 호환)
        ticker_stats = {t: ev.to_dict() for t, ev in ctx.ticker_stats.items()}

        # /eval 조회 등을 위해 최근 평가결과를 저장
        self.last_ticker_stats = ticker_stats

        # 대기 주문 최종 확인
        time.sleep(0.5)
        self.execution_manager.check_pending_orders()

        # 리포트 전송
        try:
            self._send_cycle_report(market_regime, ticker_stats)
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

    # ──────────────────────────────────────────────
    # 리포트
    # ──────────────────────────────────────────────

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
            sorted_stats = sorted(
                ticker_stats.values(),
                key=lambda x: (x["signal_type"] != "HOLD", x["signal_confidence"]),
                reverse=True,
            )

            for stat in sorted_stats[:3]:
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

    # ──────────────────────────────────────────────
    # 실시간 틱 처리
    # ──────────────────────────────────────────────

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
            self.notifier.start_buffering()
            log = f"⚡[Realtime Risk Hook]\n{risk_signal_str}"
            self.notifier.send_message(log)
            logger.warning(log)
            self.execution_manager.execute_sell(
                self.name, ticker, current_price, risk_signal
            )
            # 주문 제출 후 즉시 체결 여부 확인 (IOC 등 대응)
            self.execution_manager.check_pending_orders()
            self.notifier.flush_buffer()
