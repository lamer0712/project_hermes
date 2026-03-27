import os
import sys
import json
import math
import glob
import traceback
from src.communication.command_queue import CommandQueue
from src.broker.broker_api import UpbitBroker
from src.data.strategy_report import generate_report
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
            "eval": self._handle_eval,
            "report": self._handle_report,
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
        msg += f"• {t}\[{r.capitalize()}\]: {s}\[{st} {sc:.1f}\]\n  └ {sr}\n"

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
            msg += f"• 매수 이유: {sr}, score({sc:.1%})\n"
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

            msg += "\n"
            msg += f"현금: {s['cash']:,.0f} KRW\n"
            msg += f"총액: {s['total_value']:,.0f} KRW\n"
            msg += f"수익률: {s['return_rate']:+.2f}%\n"
            msg += f"매매: {s['total_trades']}회 (승률 {s['win_rate']:.0f}%)\n\n"

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
