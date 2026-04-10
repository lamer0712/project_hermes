import os
import sys
import json
import math
import glob
import traceback
from src.communication.command_queue import CommandQueue
from src.broker.broker_api import UpbitBroker
from src.data.strategy_report import generate_report
from src.core.analytics import TradeAnalytics
from src.utils.visualizer import Visualizer
from src.optimization.optimizer import StrategyOptimizer
from src.utils.logger import logger


class CommandQueueHandler:
    """
    텔레그램 명령 큐를 처리하는 핸들러.

    CommandQueue에서 명령을 꺼내 각 명령 유형별 핸들러 메서드로 디스패치합니다.
    pm, investors, manager, notifier를 인스턴스로 보유하여
    매 호출마다 인자 전달이 불필요합니다.
    """

    def __init__(self, pm, manager, notifier):
        self.pm = pm
        self.manager = manager
        self.notifier = notifier

        # 명령 → 핸들러 디스패치 테이블
        self._dispatch = {
            "restart": self._handle_restart,
            "kill": self._handle_kill,
            "status": self._handle_status,
            "liquidate": self._handle_liquidate,
            "limit_sell": self._handle_limit_sell,
            "sync": self._handle_sync,
            "halt": self._handle_halt,
            "resume": self._handle_resume,
            "clear": self._handle_clear,
            "report": self._handle_report,
            "analytics": self._handle_analytics,
            "optimize": self._handle_optimize,
            "apply_optimize": self._handle_apply_optimize,
            "strategy": self._handle_strategy,
        }

    def process(self):
        """큐에서 명령을 꺼내 디스패치합니다."""
        commands = CommandQueue.pop_all()
        if not commands:
            return

        logger.info(f"\n--- [Command Queue] {len(commands)}개 명령 처리 시작 ---")

        should_restart = False

        for cmd in commands:
            command = cmd.get("command")
            params = cmd.get("params", {})

            try:
                if command == "restart":
                    should_restart = True
                    continue  # 재시작은 모든 명령 처리 후 마지막에

                handler = self._dispatch.get(command)
                if handler:
                    handler(params)
                else:
                    logger.info(f"[Command Queue] 알 수 없는 명령: {command}")

            except Exception as e:
                traceback.print_exc()
                logger.error(f"[Command Queue] 명령 실행 오류: {command} - {e}")
                self.notifier.send_message(f"❌ 명령 실행 오류: {command}\n{e}")

        # 모든 명령 처리 후 재시작
        if should_restart:
            self._handle_restart({})

    # ──────────────────────────────────────────────
    # 개별 명령 핸들러
    # ──────────────────────────────────────────────

    def _handle_restart(self, params):
        """main.py 프로세스를 자체 재시작합니다."""
        logger.info("\n" + "=" * 60)
        logger.info("🔄 [System] main.py 재시작...")
        logger.info("=" * 60)

        self.notifier.send_message("🔄 *main.py 재시작...*")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _handle_kill(self, params):
        """시스템을 종료합니다."""
        self.notifier.send_message(
            "🛑 *시스템 종료 명령이 수신되었습니다. 프로그램을 종료합니다.*"
        )
        logger.info("[System] Kill command received. Exiting...")
        sys.exit(0)

    def _handle_status(self, params):
        """포트폴리오 상태 메시지를 생성하여 전송합니다."""
        self.pm.load_state()  # DB에서 최신 정보 로드
        status_msg = self._get_status_message()
        self.notifier.send_message(status_msg)

    def _handle_liquidate(self, params):
        """특정 티커를 강제 청산(전량 매도)합니다."""
        result = self._execute_liquidate(params)
        self.notifier.send_message(result)

    def _handle_limit_sell(self, params):
        """특정 티커의 지정가 전량 매도를 실행합니다."""
        result = self._execute_limit_sell(params)
        self.notifier.send_message(result)

    def _handle_sync(self, params):
        """업비트 실계좌 잔고를 동기화합니다."""
        sync_result = self.pm.synchronize_balances(self.manager.name)
        self.notifier.send_message(sync_result)

    def _handle_halt(self, params):
        """거래를 중지합니다."""
        agent_name = self.manager.name
        if self.pm.set_halt(agent_name, True):
            self.notifier.send_message(f"🛑 *[{agent_name}] 거래 중지 설정 완료*")
        else:
            self.notifier.send_message(f"❌ *[{agent_name}] 거래 중지 설정 실패*")

    def _handle_resume(self, params):
        """거래를 재개합니다."""
        agent_name = self.manager.name
        if self.pm.set_halt(agent_name, False):
            self.notifier.send_message(f"✅ *[{agent_name}] 거래 재개 설정 완료*")
        else:
            self.notifier.send_message(f"❌ *[{agent_name}] 거래 재개 설정 실패*")

    def _handle_clear(self, params):
        """시스템 로그 및 DB 거래 내역을 정리합니다."""
        # 1. Clear *.log files
        log_files = glob.glob("*.log")
        for log_file in log_files:
            with open(log_file, "w") as f:
                pass

        # 2. Clear trade_history in DB
        self.pm.clear_trade_history()

        self.notifier.send_message("🧹 *시스템 로그 및 거래 내역(DB) 정리 완료*")
        logger.info("[System] Logs and trade history cleared.")

    def _handle_report(self, params):
        """전략별 수익률 분석 리포트를 생성하여 전송합니다."""
        try:
            report = generate_report()
            self.notifier.send_message(report)
        except Exception as e:
            logger.error(f"[Command Queue] 리포트 생성 오류: {e}")
            self.notifier.send_message(f"❌ 리포트 생성 실패: {e}")

    def _handle_analytics(self, params):
        """심화 성과 분석 리포트 및 그래프를 생성하여 전송합니다."""
        agent_name = self.manager.name
        analytics = TradeAnalytics()
        visualizer = Visualizer()
        
        # self.notifier.send_message(f"📊 *[{agent_name}] 성과 분석 데이터를 생성 중입니다...*")
        
        try:
            # 1. 집계 데이터 가져오기
            stats = analytics.get_strategy_performance(agent_name)
            equity_df = analytics.get_equity_curve_data(agent_name, days=7)
            
            # 2. 요약 메시지 작성
            msg = f"📈 *심화 성과 분석 리포트 ({agent_name})*\n"
            msg += f"> 분석 기간: 최근 7일\n\n"
            
            if stats.empty:
                msg += "❌ 아직 분석할 충분한 매매 기록이 없습니다."
                self.notifier.send_message(msg)
            else:
                msg += "*[전략별 요약]*\n"
                for strat, row in stats.iterrows():
                    msg += f"• *{strat}*\n"
                    msg += f"  - 수익: {row['total_profit']:+,.0f}원 (승률 {row['win_rate']:.1f}%)\n"
                    msg += f"  - PF: {row['profit_factor']:.2f} | 평균보유: {row['avg_hold_min']:.1f}분\n"
                
                self.notifier.send_message(msg)
                
                # 3. 그래프 생성 및 전송
                # Equity Curve
                if not equity_df.empty:
                    chart_path = visualizer.draw_equity_curve(equity_df, agent_name)
                    if chart_path:
                        self.notifier.send_photo(chart_path, caption="📈 자산 성장 곡선 (Equity Curve)")
                
                # Strategy Performance Bar
                chart_path2 = visualizer.draw_strategy_performance(stats, agent_name)
                if chart_path2:
                    self.notifier.send_photo(chart_path2, caption="💰 전략별 누적 수익금")
                
                # Efficiency Analysis
                chart_path3 = visualizer.draw_win_rate_analysis(stats, agent_name)
                if chart_path3:
                    self.notifier.send_photo(chart_path3, caption="🎯 전략 효율성 분석 (Win Rate & PF)")
                    
        except Exception as e:
            traceback.print_exc()
            logger.error(f"[Command Queue] Analytics 생성 오류: {e}")
            self.notifier.send_message(f"❌ 분석 리포트 생성 실패: {e}")

    def _handle_eval(self, params):
        """특정 티커의 가장 최근 평가 결과를 전송합니다."""
        ticker = params.get("ticker", "").upper()
        stats = getattr(self.manager, "last_ticker_stats", {})

        if not ticker:
            self.notifier.send_message("❌ 조회할 티커를 입력해주세요.")
            self.notifier.send_message(f"{list(stats.keys())}")
            return

        # ManagerAgent에서 최근 stats 가져오기
        stat = stats.get(ticker)

        if not stat:
            self.notifier.send_message(
                f"❌ {ticker}에 대한 최근 분석 데이터가 없습니다."
            )
            self.notifier.send_message(f"{list(stats.keys())}")
            return

        t = stat["ticker"]
        r = stat["regime"]
        s = stat["strategy"]
        st = stat["signal_type"]
        sr = stat["signal_reason"]
        ss = stat["signal_strength"]
        sc = stat.get("signal_confidence", 0)
        current_price = stat.get("current_price", 0)

        msg = f"⚙️ *티커 상세 현황*\n"
        # msg += f"[차트 바로가기](https://upbit.com/exchange?code=CRIX.UPBIT.{t})\n"
        msg += f"• {t}\[{r.capitalize()}] : {s} {st} {sc:.1f}\n  └ {sr}\n"

        holdings = self.pm.get_holdings(self.manager.name)
        if ticker in holdings and holdings[ticker]["volume"] > 0:
            h = holdings[ticker]
            buy_strategy = h.get("strategy", "Unknown")
            avg_price = h.get("avg_price", 0)
            atr = h.get("atr_14", 0.0)
            tp_hit = h.get("tp_levels_hit", [])
            sl_hit = h.get("sl_levels_hit", [])

            profit_pct = 0.0
            if avg_price > 0 and current_price > 0:
                profit_pct = (current_price - avg_price) / avg_price * 100.0

            risk_params = self.manager.risk_manager.risk_params
            take_profit_pct = risk_params.get("take_profit_pct", 10.0)

            stop_loss_pct = risk_params.get("stop_loss_pct", -5.0)
            if atr > 0 and avg_price > 0:
                atr_pct = (atr / avg_price) * 100.0
                stop_loss_pct = -max(3.0, min(15.0, atr_pct * 2.5))

            msg += f"\n📦 *보유 포지션 상세 현황*\n"
            msg += f"• 매수 전략: {buy_strategy}({r})\n"
            msg += f"• 매수 이유: {sr}, score({sc:.1f})\n"
            msg += f"• 매수 금액: {h['volume'] * avg_price:,.0f}원\n"
            msg += f"• 현재 평가 금액: {h['volume'] * current_price:,.0f}원\n"
            msg += f"• 평단 수익률: {profit_pct:+.2f}% (평단가: {avg_price:,.2f}원)\n"
            msg += f"• 목표 익절가: +{take_profit_pct:.1f}% ({avg_price * (1 + take_profit_pct / 100):,.2f}원)"
            msg += f" (달성 내역: {tp_hit})\n" if tp_hit else "\n"
            msg += f"• 동적 손절가: {stop_loss_pct:.1f}% ({avg_price * (1 + stop_loss_pct / 100):,.2f}원)"
            msg += f" (발동 내역: {sl_hit})\n" if sl_hit else "\n"

        self.notifier.send_message(msg)

    # ──────────────────────────────────────────────
    # 내부 헬퍼 메서드
    # ──────────────────────────────────────────────

    def _get_status_message(self) -> str:
        """포트폴리오 상태 메시지를 생성합니다."""
        pm = self.pm
        target_agent = self.manager.name

        if target_agent in pm.portfolios:
            s = pm.get_portfolio_summary(target_agent)
            msg = f"📊 *포트폴리오 상세 현황 ({target_agent})*\n\n"

            is_halted = pm.is_halted(target_agent)
            total_trades = s.get("total_trades", 0)
            win_rate = s.get("win_rate", 100)
            return_rate = s.get("return_rate", 0)

            if is_halted:
                msg += "🛑 *거래 중지됨 (Halted)*\n"
            elif total_trades > 10 and (win_rate < 20.0 or return_rate < -15.0):
                msg += "🛑 *매수 차단됨 (Kill Switch 발동)*\n"
            self.notifier.send_message(f"❌ *최적화 중 오류 발생:* {str(e)}")

            msg += "\n"
            msg += f"현금: {s['cash']:,.0f} KRW\n"
            msg += f"총액: {s['total_value']:,.0f} KRW\n"
            msg += f"수익률: {s['return_rate']:+.2f}%\n"
            msg += f"낙폭(MDD): -{s['max_drawdown']:.2f}%\n"
            msg += f"매매: {s['total_trades']}회 (승률 {s['win_rate']:.0f}%)\n"
            msg += f"PF: {s['profit_factor']:.2f} | 손익비: {s['risk_reward_ratio']:.2f}\n\n"

            msg += "*보유 종목*\n"
            holdings = s.get("holdings", {})
            if not holdings:
                msg += "없음\n"
            else:
                for ticker, data in holdings.items():
                    cost = data.get("total_cost", 0)
                    vol = data.get("volume", 0)
                    avg = data.get("avg_price", 0)
                    msg += f"• {ticker}: {vol:.6f} (평단 {avg:,.2f}, 매입가 {cost:,.0f}원)\n"

            return msg

        return "❌ 포트폴리오를 찾을 수 없습니다."

    def _execute_liquidate(self, params: dict) -> str:
        """특정 티커를 강제 청산(전량 매도)합니다."""
        agent_name = self.manager.name
        ticker = params.get("ticker")

        if not ticker:
            return f'❌ 청산 실패: 티커를 지정해주세요.\n예: "/liquidate ARDR"'

        # 실제 Upbit 잔고 확인
        broker = UpbitBroker()
        currency = ticker.split("-")[1] if "-" in ticker else ticker
        actual_balance = 0.0
        try:
            balances = broker.get_balances()
            for b in balances:
                if b.get("currency") == currency:
                    actual_balance = float(b.get("balance", "0"))
                    break
        except Exception as e:
            return f"❌ 청산 실패: 잔고 조회 오류 - {e}"

        if actual_balance <= 0:
            holdings = self.pm.get_holdings(agent_name)
            if ticker in holdings and holdings[ticker]["volume"] > 0:
                phantom_vol = holdings[ticker]["volume"]
                self.pm.record_sell(agent_name, ticker, phantom_vol, 0)
                return f"🔄 {agent_name}: {ticker} 실제 잔고 0 → PM 팬텀 보유량({phantom_vol:.6f}) 정리 완료"
            return f"❌ 청산 실패: {ticker} 실제 보유량이 없습니다."

        # PM 추적 수량 기준으로 매도
        sell_volume = actual_balance
        holdings = self.pm.get_holdings(agent_name)
        if ticker in holdings and holdings[ticker]["volume"] > 0:
            sell_volume = min(holdings[ticker]["volume"], actual_balance)
        else:
            return f"❌ 청산 실패: {agent_name}의 {ticker} PM 보유 기록이 없습니다."

        # 시장가 매도
        logger.info(
            f"[청산] {agent_name}: {ticker} 매도 실행 | 수량: {sell_volume:.6f} (실제잔고: {actual_balance:.6f})"
        )
        res = broker.place_order(
            ticker, "ask", volume=str(sell_volume), ord_type="market"
        )
        # logger.info(res)

        if res and "error" not in res:
            return f"✅ 청산 완료\n종목: {ticker}\n수량: {actual_balance:.6f}\n결과: 시장가 전량 매도 성공"
        else:
            err_val = res.get("error", {}) if isinstance(res, dict) else {}
            if isinstance(err_val, dict):
                error_msg = err_val.get("message", str(res))
            else:
                error_msg = str(err_val)
                if isinstance(res, dict) and "details" in res and res["details"]:
                    try:
                        parsed = json.loads(res["details"])
                        if isinstance(parsed, dict) and isinstance(
                            parsed.get("error"), dict
                        ):
                            error_msg = parsed["error"].get("message", error_msg)
                    except Exception:
                        pass
            return f"❌ 청산 실패: {ticker} 매도 주문 오류\n{error_msg}"

    def _execute_limit_sell(self, params: dict) -> str:
        """지정가 전량 매도를 실행합니다."""
        agent_name = self.manager.name
        ticker = params.get("ticker")
        price = params.get("price")

        if not ticker or not price:
            return "❌ 지정가 매도 실패: 종목, 또는 가격 누락"

        # 실제 Upbit 잔고 확인
        broker = UpbitBroker()
        currency = ticker.split("-")[1] if "-" in ticker else ticker
        actual_balance = 0.0
        try:
            balances = broker.get_balances()
            for b in balances:
                if b.get("currency") == currency:
                    actual_balance = float(b.get("balance", "0"))
                    break
        except Exception as e:
            return f"❌ 지정가 매도 실패: 잔고 조회 오류 - {e}"

        if actual_balance <= 0:
            return f"❌ 지정가 매도 실패: {ticker} 실제 보유량이 없습니다."

        # PM 추적 수량 기준으로 매도
        sell_volume = actual_balance
        holdings = self.pm.get_holdings(agent_name)
        if ticker in holdings and holdings[ticker]["volume"] > 0:
            sell_volume = min(holdings[ticker]["volume"], actual_balance)
        else:
            return f"❌ 지정가 매도 실패: {agent_name}의 {ticker} 장부상 보유 기록이 없습니다."

        price = int(math.ceil(float(price) * 1.001 / sell_volume * 0.01) * 100)
        logger.info(
            f"[지정가 매도] {agent_name}: {ticker} 매도 실행 | 수량: {sell_volume:.6f} | 지정가: {price} KRW"
        )
        res = broker.place_order(
            ticker, "ask", volume=str(sell_volume), price=str(price), ord_type="limit"
        )

        if res and "error" not in res:
            self.pm.record_sell(agent_name, ticker, sell_volume, price)

            uuid = res.get("uuid", "N/A")
            return f"✅ 지정가 매도 주문 접수 완료\n종목: {ticker}\n수량: {sell_volume:.6f}\n지정가: {price} KRW\n주문 UUID: {uuid}"
        else:
            err_val = res.get("error", {}) if isinstance(res, dict) else {}
            if isinstance(err_val, dict):
                error_msg = err_val.get("message", str(res))
            else:
                error_msg = str(err_val)
                if isinstance(res, dict) and "details" in res and res["details"]:
                    try:
                        parsed = json.loads(res["details"])
                        if isinstance(parsed, dict) and isinstance(
                            parsed.get("error"), dict
                        ):
                            error_msg = parsed["error"].get("message", error_msg)
                    except Exception:
                        pass
            return f"❌ 지정가 매도 주문 실패: {ticker}\n{error_msg}"

    def _handle_optimize(self, params):
        """전략 파라미터 및 매핑 최적화를 수행하고 승인을 요청합니다."""
        agent_name = params.get("agent_name", "crypto_manager")
        self.notifier.send_message(f"🧬 *[{agent_name}] 전략 최적화 및 성과 비교 시작...* (수 분 소요)")
        
        try:
            # 1. 최적화 실행 (최근 7일 데이터 기반, 베이스라인 비교 포함)
            optimizer = StrategyOptimizer(days=7)
            comparison = optimizer.optimize(current_manager=self.manager)
            
            if not comparison:
                self.notifier.send_message(f"⚠️ *[{agent_name}] 최적화 실패 (데이터 부족 등)*")
                return

            base = comparison["baseline"]
            opt = comparison["optimized"]
            
            # 2. 메시지 구성 (성과 비교)
            msg = f"📊 *전략 최적화 성과 비교 ({self.manager.name})*\n"
            msg += f"`데이터 기간: 최근 7일` \n\n"
            msg += f"| 항목 | 현재 (Before) | 제안 (After) |\n"
            msg += f"| :--- | :---: | :---: |\n"
            msg += f"| **ROI** | {base['roi']:+.2f}% | **{opt['roi']:+.2f}%** |\n"
            msg += f"| **PF** | {base['pf']:.2f} | **{opt['pf']:.2f}** |\n"
            msg += f"| **MDD** | {base['mdd']:.2f}% | **{opt['mdd']:.2f}%** |\n"
            msg += f"| **Trades** | {base['total_trades']}회 | {opt['total_trades']}회 |\n\n"
            
            # 1. 주요 파라미터 변경 사항 요약
            msg += "⚙️ *제안된 파라미터 설정*\n"
            proposed = comparison["proposed_config"]["strategy_params"]
            for s_name, p_set in proposed.items():
                msg += f"• `{s_name}`: {p_set}\n"
            
            msg += "\n🏆 *장세별 최적 전략(Champion) 매핑*\n"
            s_map = comparison["proposed_config"]["strategy_map"]
            for regime, strats in s_map.items():
                msg += f"• `{regime:12}`: {', '.join(strats)}\n"
            
            self.notifier.send_message(msg)
            
            # 3. 승인 버튼 추가
            keyboard = {
                "inline_keyboard": [[
                    {"text": "✅ 최적화 적용", "callback_data": "confirm_opt"},
                    {"text": "❌ 취소", "callback_data": "cancel_opt"}
                ]]
            }
            self.notifier.send_message("위 성과를 검토하고 적용 여부를 선택해주세요.", reply_markup=keyboard)
            
        except Exception as e:
            traceback.print_exc()
            logger.error(f"[CommandHandler] 최적화 중 오류: {e}")
            self.notifier.send_message(f"❌ *최적화 중 오류 발생:* {str(e)}")

    def _handle_apply_optimize(self, params):
        """임시 저장된 최적화 결과를 정식 반영합니다."""
        agent_name = params.get("agent_name", "crypto_manager")
        pending_path = "data/pending_optimized_params.json"
        target_path = "data/optimized_params.json"
        
        if not os.path.exists(pending_path):
            self.notifier.send_message(f"⚠️ *[{agent_name}] 적용할 대기 중인 최적화 결과가 없습니다.*")
            return
            
        try:
            with open(pending_path, "r") as f:
                data = json.load(f)
                proposed_config = data["proposed_config"]
                
            # 정식 파일로 저장
            with open(target_path, "w") as f:
                json.dump(proposed_config, f, indent=4)
                
            # 매니저 객체 핫스왑
            self.manager.strategy_manager.load_optimized_config()
            self.manager.strategy_map = self.manager.strategy_manager.optimized_strategy_map or self.manager.DEFAULT_STRATEGY_MAP
            
            # 임시 파일 삭제
            if os.path.exists(pending_path):
                os.remove(pending_path)
            
            self.notifier.send_message(f"✅ *[{agent_name}] 최적화 설정이 성공적으로 반영되었습니다!*")
            logger.info(f"[CommandHandler] Optimized config applied to {agent_name}")
            
        except Exception as e:
            logger.error(f"[CommandHandler] 최적화 반영 중 오류: {e}")
            self.notifier.send_message(f"❌ *최적화 반영 중 오류 발생:* {str(e)}")


    def _handle_strategy(self, params):
        """현재 장세별 전략 매핑 현황을 전송합니다."""
        try:
            strategy_map = self.manager.strategy_map
            
            msg = "🎯 **현재 장세별 전략 매핑 현황**\n"
            msg += "최근 최적화된 설정이 적용되어 있습니다.\n\n"
            
            for regime, strats in strategy_map.items():
                strats_text = ", ".join(strats) if strats else "매수 안함 (Skip)"
                msg += f"• **{regime:12}** : {strats_text}\n"
            
            # 현재 적용된 상세 파라미터도 요약해서 보여주면 좋을 것 같음
            opt_params = self.manager.strategy_manager.optimized_params
            if opt_params:
                msg += "\n⚙️ **전략별 최적 파라미터**\n"
                for s_name, p_set in opt_params.items():
                    msg += f"• `{s_name}`: {p_set}\n"
            
            self.notifier.send_message(msg)
            
        except Exception as e:
            logger.error(f"[CommandHandler] Strategy 조회 중 오류: {e}")
            self.notifier.send_message(f"❌ 전략 매핑 조회 실패: {e}")
