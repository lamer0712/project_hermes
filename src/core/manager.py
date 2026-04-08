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
    # STRATEGY_MAP = {
    #     "bullish": ["Breakout", "PullbackTrend"],
    #     "ranging": ["VWAPReversion", "MeanReversion"],
    #     "volatile": ["Breakout", "VWAPReversion"],
    #     # "neutral": ["VWAPReversion", "MeanReversion"],
    #     # "bearish": ["Bearish"],
    #     # "panic": ["Panic"],
    # }
    STRATEGY_MAP = {
        "recovery": ["PullbackTrend"],
        "weakbullish": ["PullbackTrend", "VWAPReversion"],
        "bullish": ["Breakout", "PullbackTrend"],
        "earlybreakout": ["Breakout"],
        "ranging": ["VWAPReversion"],
        "volatile": ["Breakout", "VWAPReversion"],
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
        self.breakout_thresholds = {}  # 실시간 돌파 감시용 기준가 (high_20)
        self.breakout_cooldowns = {}   # 실시간 돌파 평가 쿨타임 기록용

        self.last_ticker_stats = {}
        self.current_regime = "ranging"  # 실시간 틱 리스크 관리용 장세 저장
        self.breakout_counts = {}  # 종목별 연속 돌파 횟수 관리

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

        # 0.5 실시간 돌파 기준가 업데이트 (실시간 감시용)
        self._update_breakout_thresholds(entry_market_data)
        self.current_regime = market_regime # 실시간용 장세 동기화
        self.breakout_counts = {} # 매 사이클마다 카운트 초기화 (신선도 유지)

        # 1. 컨텍스트 구성
        ctx = self._build_cycle_context(entry_market_data, market_regime)

        # 2. 종목별 평가 + 매도 실행 + 매수 후보 수집
        self._evaluate_and_execute_sells(ctx, setup_market_data, entry_market_data)

        # 3. 최적 매수 1건 실행
        self._select_and_execute_buy(ctx)

        # 4. 마무리 (대기주문 확인 + 리포트 + 저장)
        self._finalize_cycle(ctx, market_regime)
        self.notifier.flush_buffer()

    def _update_breakout_thresholds(self, entry_market_data: dict):
        """15분 봉 데이터를 기반으로 실시간 돌파 감시 기준가를 설정합니다."""
        self.breakout_thresholds = {}
        for ticker, df in entry_market_data.items():
            # 전고점(high_20)을 돌파 기준으로 설정
            if df is None or df.empty or "high_20" not in df.columns:
                continue
            # 보유 종목 제외
            if ticker in self.portfolio_manager.get_holdings(self.name):
                continue
            self.breakout_thresholds[ticker] = float(df.high_20.iloc[-1])

    # ──────────────────────────────────────────────
    # 파이프라인 단계
    # ──────────────────────────────────────────────

    def _build_cycle_context(
        self, entry_market_data: dict, market_regime: str
    ) -> CycleContext:
        """보유현황·현금·현재가를 수집하여 CycleContext를 구성합니다."""
        buy_filter_passed = market_regime not in ["bearish", "panic"]

        if self.portfolio_manager:
            holdings = self.portfolio_manager.get_holdings(self.name)
            current_prices = {}
            missing_tickers = []
            for ticker in holdings:
                if ticker in entry_market_data:
                    current_prices[ticker] = float(
                        entry_market_data[ticker].close.iloc[-1]
                    )
                else:
                    missing_tickers.append(ticker)
            
            # 부족한 현재가 실시간 조회 (실시간 돌파 등 단일 종목 평가 시 필요)
            if missing_tickers:
                prices = UpbitMarketData.get_current_prices_simple(missing_tickers)
                current_prices.update(prices)

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
                self.name, ticker, current_price, market_regime=ctx.market_regime
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
            custom_sl_price=signal.custom_sl_price if signal else None,
            custom_tp_price=signal.custom_tp_price if signal else None,
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
                    custom_sl_price=evaluation.custom_sl_price,
                    custom_tp_price=evaluation.custom_tp_price,
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
                    custom_sl_price=evaluation.custom_sl_price,
                    custom_tp_price=evaluation.custom_tp_price,
                )
                # 전략을 다시 로드 (매수 실행에 필요)
                strategy = self.strategy_manager.get_strategy(evaluation.strategy)
                ctx.buy_candidates.append((signal, strategy, entry_df))

    def _select_and_execute_buy(self, ctx: CycleContext) -> None:
        """수집된 매수 후보 중 최적 1건을 실행합니다."""
        # confidence 기준 내림차순 정렬
        ctx.buy_candidates.sort(key=lambda x: x[0].confidence, reverse=True)

        for cand_signal, cand_strategy, cand_market_data in ctx.buy_candidates:
            # 보유 종목 수 제한 확인
            if (
                len(self.portfolio_manager.get_holdings(self.name))
                >= self.MAX_POSITIONS
            ):
                logger.info(
                    f"[ManagerAgent] 최대 보유 종목 수({self.MAX_POSITIONS}) 도달로 매수 중단."
                )
                break

            if cand_signal.confidence <= 0.3:
                break

            ticker = cand_signal.ticker
            # 이미 이번 사이클에서 매수했거나 보유 중인 종목 스킵
            if ticker in self.portfolio_manager.get_holdings(self.name):
                continue

            current_price = float(cand_market_data.close.iloc[-1])
            available_cash = self.portfolio_manager.get_available_cash(self.name)

            if available_cash < self.MIN_ORDER_AMOUNT:
                logger.info(
                    f"[ManagerAgent] 잔고 부족({available_cash:,.0f} < {self.MIN_ORDER_AMOUNT})으로 매수 종료."
                )
                break

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
                    f"[ManagerAgent] {ticker} 매수 접수 성공. 다음 후보 검토..."
                )

    def _finalize_cycle(self, ctx: CycleContext, market_regime: str) -> None:
        """대기주문 확인, 리포트 전송, 포트폴리오 상태 저장을 수행합니다."""
        # dict 변환 (기존 호환)
        ticker_stats = {t: ev.to_dict() for t, ev in ctx.ticker_stats.items()}

        # /eval 조회 등을 위해 최근 평가결과를 저장
        self.last_ticker_stats = ticker_stats

        # 대기 주문 최종 확인
        self.execution_manager.check_pending_orders()

        # 리포트 전송
        try:
            current_prices = {
                t: s["current_price"] for t, s in ticker_stats.items()
            }
            # ctx에 있는 가격 정보와 병합 (평가되지 않은 보유 종목 가격 포함)
            for t, p in ctx.current_prices.items():
                if t not in current_prices:
                    current_prices[t] = p

            self._send_cycle_report(market_regime, ticker_stats, current_prices=current_prices)
            if self.portfolio_manager:
                self.portfolio_manager.export_portfolio_report(
                    self.name, current_prices=current_prices
                )
        except Exception as e:
            logger.error(f"Failed to send cycle report or export portfolio: {e}")

    # ──────────────────────────────────────────────
    # 리포트
    # ──────────────────────────────────────────────

    def _send_cycle_report(self, market_regime: str, ticker_stats: dict, current_prices: dict = None) -> None:
        """
        매 싸이클 결과 요약 리포트를 텔레그램으로 전송합니다.
        """
        if not self.portfolio_manager:
            return

        # 최신 가격 정보를 반영한 요약 정보 가져오기
        if current_prices is None:
            current_prices = {t: s["current_price"] for t, s in ticker_stats.items()}

        summary = self.portfolio_manager.get_portfolio_summary(
            self.name, current_prices=current_prices
        )
        if not summary:
            return

        # 1. Market Regime & 기본 자산 정보
        msg = f"📊 *Investment Report*\n"
        msg += f"🌐 Market Regime: {market_regime.upper()}\n"
        msg += f"💰 총 자산: {summary['total_value']:,.0f} KRW\n"
        msg += f"💵 현금 자산: {summary['cash']:,.0f} KRW ({summary['return_rate']:+.2f}%)\n\n"

        # 2. 보유 종목 투자금, 수익률, 수익금
        holdings = summary.get("holdings", {})
        if holdings:
            msg += "📦 *보유 종목 현황*\n"
            for ticker, h in holdings.items():
                price = current_prices.get(ticker, h["avg_price"])
                cost = h["total_cost"]
                val = h["volume"] * price
                pnl = val - cost
                roi = (pnl / cost * 100) if cost > 0 else 0

                msg += f"• {ticker}: {cost:,.0f} → {roi:+.2f}% ({pnl:+,.0f})\n"
            msg += "\n"

        # 3. 전략별 코인 사항 (Regime, Signal)
        top_buys = sorted(
            [v for v in ticker_stats.values() if v.get("signal_type") == "BUY"],
            key=lambda x: x.get("signal_confidence", 0),
            reverse=True,
        )

        selected_stats = sorted(
            [
                v
                for v in ticker_stats.values()
                if v.get("signal_type") == "HOLD" and v.get("signal_confidence", 0) != 0
            ],
            key=lambda x: x.get("signal_confidence", 0),
            reverse=True,
        )
        if top_buys:
            selected_stats = top_buys

        if ticker_stats and selected_stats:
            msg += "⚙️ *티커별 모니터링*\n"
            for stat in selected_stats[:3]:
                t = stat["ticker"]
                r = stat["regime"]
                s = stat["strategy"]
                st = stat["signal_type"]
                sr = stat["signal_reason"]
                sc = stat["signal_confidence"]
                msg += f"• {t}\[{r.capitalize()}] : {s} {st} {sc:.1f}\n  └ {sr}\n"

        self.notifier.send_message(msg)

    # ──────────────────────────────────────────────
    # 실시간 틱 처리
    # ──────────────────────────────────────────────

    def handle_realtime_tick(self, ticker: str, current_price: float) -> None:
        """웹소켓에서 수신한 실시간 틱 데이터를 바탕으로 긴급 손절/익절 및 실시간 돌파 진입을 검사합니다."""
        self.execution_manager.check_pending_orders()
        if getattr(self, "portfolio_manager", None) is None:
            return

        holdings = self.portfolio_manager.get_holdings(self.name)
        is_held = ticker in holdings and holdings[ticker].get("volume", 0) > 0

        if is_held:
            # ──────────────────────────────────────────────
            # 1. 보유 종목 리스크 관리 (기존)
            # ──────────────────────────────────────────────
            risk_signal = self.risk_manager.evaluate_risk(
                self.name, ticker, current_price, market_regime=self.current_regime
            )
            if risk_signal:
                risk_signal_str = risk_signal.__str__()
                self.notifier.start_buffering()
                log = f"⚡[Realtime Risk Hook]\n{risk_signal_str}"
                logger.warning(log)
                self.execution_manager.execute_sell(
                    self.name, ticker, current_price, risk_signal
                )
                self.execution_manager.check_pending_orders()
                self.notifier.flush_buffer()
        else:
            # ──────────────────────────────────────────────
            # 2. 미보유 종목 실시간 돌파 감시 (신규)
            # ──────────────────────────────────────────────
            threshold = self.breakout_thresholds.get(ticker)
            if threshold and current_price > threshold:
                # 2분(120초) 쿨타임 체크 (잦은 API 호출 방지 및 재돌파 기회 유지)
                last_eval = self.breakout_cooldowns.get(ticker, 0)
                if time.time() - last_eval > 120:
                    self.breakout_cooldowns[ticker] = time.time()
                    self._execute_early_buy(ticker, current_price)

    def _execute_early_buy(self, ticker: str, current_price: float):
        """실시간 돌파 감지 시 즉시 평가 및 매수를 시도합니다."""
        # 연속 돌파 카운팅
        count = self.breakout_counts.get(ticker, 0) + 1
        self.breakout_counts[ticker] = count

        if count >= 3:
            msg = f"🚀 [Triple Breakout] {ticker} 3회 연속 돌파 감지! (Price: {current_price:,.0f})\n강한 상승세가 지속되고 있습니다. 진입 여부를 검토하세요."
            logger.info(f"🔔 {msg}")
            self.notifier.send_message(msg)
            self.breakout_counts[ticker] = 0 # 알림 후 초기화

        logger.info(
            f"🔥 [Realtime Breakout] {ticker} 돌파 감지! ({count}회차, Price: {current_price:,.0f})"
        )

        # 실시간 평가를 위해 필요한 데이터(15분/60분 봉 + 지표) 가져오기
        # setup_df = UpbitMarketData.get_ohlcv_with_indicators_new(
        #     ticker, count=100, interval="minutes/60", current_price=current_price
        # )
        entry_df = UpbitMarketData.get_ohlcv_with_indicators_new(
            ticker, count=100, interval="minutes/15", current_price=current_price
        )
        if entry_df is None or entry_df.empty:
            return

        # 1. 컨텍스트 구성 (단일 종목용, 실시간 돌파는 변동성 장세로 가정)

        ctx = self._build_cycle_context({ticker: entry_df}, "volatile")

        # 2. 돌파 전략(Breakout) 직접 평가
        strategy = self.strategy_manager.get_strategy("Breakout")
        if not strategy:
            return

        self.notifier.start_buffering()

        signal = strategy.evaluate(ticker, None, entry_df, ctx.portfolio_info)

        # TickerEvaluation 기록 (finalize_cycle에서 사용)
        ctx.ticker_stats[ticker] = TickerEvaluation(
            ticker=ticker,
            regime="volatile",
            strategy=strategy.name,
            signal_type=signal.type.value,
            signal_reason=signal.reason,
            signal_strength=signal.strength,
            signal_confidence=signal.confidence,
            current_price=current_price,
        )

        if signal.type == SignalType.BUY:
            if not ctx.buy_filter_passed or ctx.available_cash < self.MIN_ORDER_AMOUNT:
                logger.info(
                    f"⏸️ [Realtime] {ticker} 매수 조건은 맞으나 필터링 혹은 잔고 부족으로 보류"
                )
                self.notifier.discard_buffer()
            else:
                ctx.buy_candidates.append((signal, strategy, entry_df))
                self._select_and_execute_buy(ctx)

                # 매수 성공 시 감시 목록 및 카운트 제거
                self.breakout_counts.pop(ticker, None)
                holdings = self.portfolio_manager.get_holdings(self.name)
                if ticker in holdings and holdings[ticker].get("volume", 0) > 0:
                    self.breakout_thresholds.pop(ticker, None)

                # 3. 마무리 (리포트 전송 등) - 매수 시도 시에만 전송
                self._finalize_cycle(ctx, "realtime breakout")
                self.notifier.flush_buffer()
        else:
            self.breakout_thresholds[ticker] = max(self.breakout_thresholds.get(ticker, 0), current_price)
            logger.info(f"⏸️ [Realtime] 매수 보류: {signal}")
            self.notifier.discard_buffer()
