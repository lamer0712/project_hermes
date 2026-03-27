import os
import json
import traceback
from datetime import datetime
from src.utils.markdown_io import write_markdown
from src.utils.logger import logger
from src.broker.broker_api import UpbitBroker
from src.data.db import DatabaseManager


class PortfolioManager:
    """
    가상 자산 포트폴리오 관리 클래스.
    Upbit 계좌 자산을 단일 포트폴리오로 통합하여 관리.
    상태 데이터 구조: {agent_name: {cash, holdings: {ticker: {volume, avg_price, total_cost}}, initial_capital}}
    """

    # 이전 agent_name → 새 agent_name 매핑 (DB 마이그레이션용)
    _AGENT_NAME_MIGRATION = {
        "manager": "crypto_manager",
    }

    def __init__(
        self, total_capital: float = 1000000, db_path: str = "data/portfolio.db"
    ):
        self.total_capital = total_capital
        self.portfolios = {}
        self.db = DatabaseManager(db_path)
        self.load_state()

    def load_state(self):
        """DB에서 포트폴리오 상태 복원"""
        try:
            state = self.db.load_portfolio_state()
            if state:
                # DB 마이그레이션: 이전 agent_name을 새 이름으로 자동 매핑
                migrated_state = {}
                for agent_name, pb in state.items():
                    new_name = self._AGENT_NAME_MIGRATION.get(agent_name, agent_name)
                    if new_name != agent_name:
                        logger.info(f"[Manager] DB 마이그레이션: '{agent_name}' → '{new_name}'")
                        # DB에서도 이름 갱신
                        self.db.rename_agent(agent_name, new_name)
                    migrated_state[new_name] = pb
                state = migrated_state

                for agent_name, pb in state.items():
                    if agent_name not in self.portfolios:
                        self.portfolios[agent_name] = {
                            "cash": pb["available_cash"],
                            "holdings": {},
                            "initial_capital": pb["allocated_capital"],
                            "total_trades": pb.get("total_trades", 0),
                            "winning_trades": pb.get("winning_trades", 0),
                            "is_halted": pb["is_halted"],
                        }
                    else:
                        self.portfolios[agent_name]["cash"] = pb["available_cash"]
                        self.portfolios[agent_name]["initial_capital"] = pb[
                            "allocated_capital"
                        ]
                        self.portfolios[agent_name]["total_trades"] = pb.get(
                            "total_trades",
                            self.portfolios[agent_name].get("total_trades", 0),
                        )
                        self.portfolios[agent_name]["winning_trades"] = pb.get(
                            "winning_trades",
                            self.portfolios[agent_name].get("winning_trades", 0),
                        )
                        self.portfolios[agent_name]["is_halted"] = pb["is_halted"]

                    # Convert DB holdings (volume, avg_price, max_price, sl_levels_hit)
                    # to memory format (volume, avg_price, total_cost, max_price, sl_levels_hit)
                    db_holdings = pb.get("holdings", {})
                    mem_holdings = {}
                    for ticker, h in db_holdings.items():
                        mem_holdings[ticker] = {
                            "volume": h["volume"],
                            "avg_price": h["avg_price"],
                            "total_cost": h["volume"] * h["avg_price"],
                            "max_price": h["max_price"],
                            "sl_levels_hit": h["sl_levels_hit"],
                            "tp_levels_hit": h.get("tp_levels_hit", []),
                            "atr_14": h.get("atr_14", 0),
                            "strategy": h.get("strategy", "Unknown"),
                        }
                    self.portfolios[agent_name]["holdings"] = mem_holdings

                logger.info("[Manager] 포트폴리오 상태 DB에서 복원 완료")
        except Exception as e:
            logger.error(f"[Manager] 상태 복원 실패, 초기화: {e}")
            self.portfolios = {}

    def save_state(self):
        """포트폴리오 상태를 DB에 저장"""
        try:
            for agent_name, p in self.portfolios.items():
                self.db.save_portfolio(
                    agent_name,
                    {
                        "allocated_capital": p["initial_capital"],
                        "available_cash": p["cash"],
                        "total_trades": p.get("total_trades", 0),
                        "winning_trades": p.get("winning_trades", 0),
                        "is_halted": p["is_halted"],
                    },
                )
                self.db.save_holdings(agent_name, p["holdings"])
        except Exception as e:
            logger.error(f"[Manager] DB 상태 저장 실패: {e}")

    def allocate(self, agent_name: str, amount: float):
        """에이전트에게 자본을 배분합니다."""
        if agent_name not in self.portfolios:
            self.portfolios[agent_name] = {
                "cash": amount,
                "holdings": {},
                "initial_capital": amount,
                "total_trades": 0,
                "winning_trades": 0,
                "is_halted": False,
            }
        else:
            # 기존 포트폴리오에 자본 추가
            self.portfolios[agent_name]["cash"] += amount
            self.portfolios[agent_name]["initial_capital"] += amount

        logger.info(
            f"[Manager] {agent_name}에게 {amount:,.0f} KRW 배분 (현재 현금: {self.portfolios[agent_name]['cash']:,.0f} KRW)"
        )
        self.save_state()

    def get_available_cash(self, agent_name: str) -> float:
        """에이전트의 가용 현금을 반환합니다."""
        if agent_name not in self.portfolios:
            return 0.0
        return self.portfolios[agent_name]["cash"]

    def get_total_value(self, agent_name: str) -> float:
        """에이전트의 총 자산 가치를 반환합니다."""
        if agent_name not in self.portfolios:
            return 0.0

        portfolio = self.portfolios[agent_name]
        total_value = portfolio["cash"]
        for ticker, holding in portfolio["holdings"].items():
            total_value += holding["volume"] * holding["avg_price"]
        return total_value

    def get_holdings(self, agent_name: str) -> dict:
        """에이전트의 보유 종목을 반환합니다."""
        if agent_name not in self.portfolios:
            return {}
        return self.portfolios[agent_name].get("holdings", {})

    def record_buy(
        self,
        agent_name: str,
        ticker: str,
        volume: float,
        price: float,
        executed_funds: float = None,
        paid_fee: float = 0.0,
        strategy: str = "Unknown",
    ) -> bool:
        """
        매수 기록. 성공 시 True, 잔고 부족 시 False.

        Args:
            agent_name: investor 이름
            ticker: 종목 (예: KRW-BTC)
            volume: 실제 매수 체결 수량
            price: 매수 단가 (참고용)
            executed_funds: 실제 매수 체결 금액 (API 제공값, 없을 시 volume * price)
            paid_fee: 실제 지불 수수료
        """
        if agent_name not in self.portfolios:
            logger.info(f"[Manager] {agent_name}은 배분되지 않은 에이전트입니다.")
            return False

        portfolio = self.portfolios[agent_name]

        total_cost_excluding_fee = (
            executed_funds if executed_funds is not None else (volume * price)
        )
        total_cost_including_fee = total_cost_excluding_fee + paid_fee

        if total_cost_including_fee > portfolio["cash"]:
            logger.info(
                f"[Manager] {agent_name} 잔고 부족: 필요 {total_cost_including_fee:,.0f} > 가용 {portfolio['cash']:,.0f}"
            )
            # 오차 보정: 5,000원 이하의 부족분은 허용하고 차감 진행
            if total_cost_including_fee > portfolio["cash"] + 5000:
                return False

        portfolio["cash"] -= total_cost_including_fee
        holdings = portfolio["holdings"]

        if ticker in holdings:
            # 기존 보유 종목 데이터 갱신
            existing = holdings[ticker]
            new_total = existing["total_cost"] + total_cost_excluding_fee
            new_volume = existing["volume"] + volume
            # 기존 max_price 및 분할손절 단계 유지
            holdings[ticker] = {
                "volume": new_volume,
                "avg_price": new_total / new_volume if new_volume > 0 else 0,
                "total_cost": new_total,
                "max_price": existing.get("max_price", price),
                "sl_levels_hit": existing.get("sl_levels_hit", []),
                "tp_levels_hit": existing.get("tp_levels_hit", []),
                "atr_14": existing.get("atr_14", 0),
                "strategy": strategy,
            }
        else:
            holdings[ticker] = {
                "volume": volume,
                "avg_price": total_cost_excluding_fee / volume if volume > 0 else price,
                "total_cost": total_cost_excluding_fee,
                "max_price": price,
                "sl_levels_hit": [],
                "tp_levels_hit": [],
                "atr_14": 0,
                "strategy": strategy,
            }

        portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
        self.save_state()
        self.db.record_trade(
            agent_name,
            ticker,
            "buy",
            volume,
            price,
            executed_funds,
            paid_fee,
            strategy=strategy,
        )
        self.export_portfolio_report(agent_name)
        msg = f"✅ 매수: {ticker} 거래수량: {volume:.6f}, 단가: {price:,.0f}, 거래금액: {total_cost_excluding_fee:,.0f}, 수수료: {paid_fee:,.2f}, 정산금액: {total_cost_including_fee:,.0f}, 잔여현금: {portfolio['cash']:,.0f}"
        logger.info(msg)
        return msg

    def record_sell(
        self,
        agent_name: str,
        ticker: str,
        volume: float,
        price: float,
        executed_funds: float = None,
        paid_fee: float = 0.0,
    ) -> bool:
        """
        매도 기록. 성공 시 True, 보유 수량 부족 시 False.
        """
        if agent_name not in self.portfolios:
            return False

        portfolio = self.portfolios[agent_name]
        holdings = portfolio["holdings"]

        if (
            ticker not in holdings or holdings[ticker]["volume"] < volume * 0.999
        ):  # 오차 보정: 0.1% 허용
            held = holdings.get(ticker, {}).get("volume", 0)
            logger.info(
                f"[Manager] {agent_name} 보유수량 부족: {ticker} 보유 {held}, 매도 요청 {volume}"
            )
            return False

        sell_revenue_gross = (
            executed_funds if executed_funds is not None else (volume * price)
        )
        sell_revenue_net = sell_revenue_gross - paid_fee

        avg_price = holdings[ticker]["avg_price"]
        max_price = max(holdings[ticker].get("max_price", avg_price), avg_price)
        # 삭제 전에 전략명을 미리 캡처 (삭제 후에는 조회 불가)
        strategy = holdings[ticker].get("strategy", "Unknown")

        profit = sell_revenue_net - (avg_price * volume)
        profit_ratio = (profit / (avg_price * volume)) * 100

        portfolio["cash"] += sell_revenue_net
        holdings[ticker]["volume"] -= volume
        holdings[ticker]["total_cost"] = (
            holdings[ticker]["volume"] * holdings[ticker]["avg_price"]
        )

        # 잔여 수량이 0에 수렴하면 종목 삭제
        if holdings[ticker]["volume"] <= 1e-8:
            del holdings[ticker]

        portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
        if profit > 0:
            portfolio["winning_trades"] = portfolio.get("winning_trades", 0) + 1

        self.save_state()
        self.db.record_trade(
            agent_name,
            ticker,
            "sell",
            volume,
            price,
            sell_revenue_gross,
            paid_fee,
            strategy=strategy,
        )
        self.export_portfolio_report(agent_name)
        profit_emoji = "⏫" if profit > 0 else "⏬"
        msg = f"{profit_emoji} 매도: {ticker} 거래수량: {volume:.3f}, 단가: {price:,.1f}, 거래금액: {sell_revenue_gross:,.0f}, 수수료: {paid_fee:,.2f}, 정산금액: {sell_revenue_net:,.0f}, 손익: {profit:+,.0f}({profit_ratio:+.2f}%), 평단가: {avg_price:,.1f}, 고가: {max_price:,.1f}"
        logger.info(msg)
        return msg

    def set_halt(self, agent_name: str, status: bool) -> bool:
        """에이전트의 거래 중지 상태를 설정합니다."""
        if agent_name not in self.portfolios:
            return False

        self.portfolios[agent_name]["is_halted"] = status
        self.save_state()
        logger.info(
            f"[Manager] {agent_name} 거래 {'중지' if status else '재개'} 설정 완료"
        )
        return True

    def is_halted(self, agent_name: str) -> bool:
        """에이전트가 중지된 상태인지 확인합니다."""
        if agent_name not in self.portfolios:
            return False
        return self.portfolios[agent_name].get("is_halted", False)

    def clear_trade_history(self) -> bool:
        """모든 에이전트의 거래 내역(trade_history)을 DB에서 삭제합니다."""
        try:
            self.db.clear_trade_history()
            logger.info(
                "[PortfolioManager] DB의 trade_history 전체 기록을 초기화했습니다."
            )
            return True
        except Exception as e:
            logger.error(f"[PortfolioManager] trade_history 삭제 실패: {e}")
            return False

    def clean_old_trade_history(self, days: int = 7) -> bool:
        """지정된 기간이 지난 trade_history를 자동 삭제합니다."""
        try:
            self.db.delete_old_trade_history(days)
            logger.info(
                f"[PortfolioManager] {days}일 지난 과거 거래 내역을 정리했습니다."
            )
            return True
        except Exception as e:
            logger.error(f"[PortfolioManager] 자동 삭제 실패: {e}")
            return False

    def update_holding_metadata(
        self,
        agent_name: str,
        ticker: str,
        max_price: float = None,
        hit_sl_level: float = None,
        hit_tp_level: float = None,
        atr_14: float = None,
    ) -> bool:
        """
        보유 종목의 최대 가격(Trailing Stop용)과 도달한 손절 단계(Partial Stop Loss용)를 업데이트합니다.
        """
        if agent_name not in self.portfolios:
            return False

        holdings = self.portfolios[agent_name].get("holdings", {})
        if ticker not in holdings:
            return False

        modified = False
        holding = holdings[ticker]

        if max_price is not None:
            current_max = holding.get("max_price", holding.get("avg_price", 0))
            if max_price > current_max:
                holding["max_price"] = max_price
                modified = True

        if hit_sl_level is not None:
            sl_levels = holding.get("sl_levels_hit", [])
            if hit_sl_level not in sl_levels:
                sl_levels.append(hit_sl_level)
                holding["sl_levels_hit"] = sl_levels
                modified = True

        if hit_tp_level is not None:
            tp_levels = holding.get("tp_levels_hit", [])
            if hit_tp_level not in tp_levels:
                tp_levels.append(hit_tp_level)
                holding["tp_levels_hit"] = tp_levels
                modified = True

        if atr_14 is not None:
            holding["atr_14"] = atr_14
            modified = True

        if modified:
            self.save_state()

        return modified

    def get_total_value(self, agent_name: str, current_prices: dict = None) -> float:
        """에이전트의 총 자산 가치를 계산합니다 (현금 + 보유종목 평가액)."""
        if agent_name not in self.portfolios:
            return 0.0

        portfolio = self.portfolios[agent_name]
        total = portfolio["cash"]

        for ticker, holding in portfolio["holdings"].items():
            if current_prices and ticker in current_prices:
                total += holding["volume"] * current_prices[ticker]
            else:
                # 현재가 정보가 없을 경우 매입가 기준 평가
                total += holding["volume"] * holding["avg_price"]

        return total

    def get_return_rate(self, agent_name: str, current_prices: dict = None) -> float:
        """에이전트의 수익률을 계산합니다."""
        if agent_name not in self.portfolios:
            return 0.0

        initial = self.portfolios[agent_name]["initial_capital"]
        if initial <= 0:
            return 0.0

        current_total = self.get_total_value(agent_name, current_prices)
        return ((current_total - initial) / initial) * 100

    def get_portfolio_summary(
        self, agent_name: str, current_prices: dict = None
    ) -> dict:
        """에이전트의 포트폴리오 요약 정보 반환"""
        if agent_name not in self.portfolios:
            return {}

        portfolio = self.portfolios[agent_name]
        total_value = self.get_total_value(agent_name, current_prices)
        return_rate = self.get_return_rate(agent_name, current_prices)
        total_trades = portfolio.get("total_trades", 0)
        winning_trades = portfolio.get("winning_trades", 0)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        return {
            "agent_name": agent_name,
            "initial_capital": portfolio["initial_capital"],
            "cash": portfolio["cash"],
            "holdings": portfolio["holdings"],
            "total_value": total_value,
            "return_rate": return_rate,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "win_rate": win_rate,
        }

    def export_portfolio_report(self, agent_name: str, current_prices: dict = None):
        """개별 에이전트의 portfolio.md 파일 생성 및 업데이트"""
        summary = self.get_portfolio_summary(agent_name, current_prices)
        if not summary:
            return

        md = f"""# Portfolio: {agent_name.replace('_', ' ').title()}
> 최종 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 💰 자산 현황
| 항목 | 금액 |
|------|------|
| 배분 자본 | {summary['initial_capital']:,.0f} KRW |
| 현재 현금 | {summary['cash']:,.0f} KRW |
| 총 평가액 | {summary['total_value']:,.0f} KRW |
| **수익률** | **{summary['return_rate']:+.2f}%** |

## 📊 매매 통계
| 항목 | 값 |
|------|-----|
| 총 매매 횟수 | {summary['total_trades']} |
| 수익 매매 | {summary['winning_trades']} |
| 승률 | {summary['win_rate']:.1f}% |

## 📦 보유 종목
"""
        holdings = summary.get("holdings", {})
        if holdings:
            md += (
                "| 종목 | 수량 | 평균 매입가 | **현재가** | 매입 총액 | **수익률** |\n"
            )
            md += "|------|------|-----------|-----------|----------|---------|\n"
            for ticker, h in holdings.items():
                current_p = (
                    current_prices.get(ticker, h["avg_price"])
                    if current_prices
                    else h["avg_price"]
                )
                roi = (
                    ((current_p - h["avg_price"]) / h["avg_price"] * 100)
                    if h["avg_price"] > 0
                    else 0
                )
                md += f"| {ticker} | {h['volume']:.6f} | {h['avg_price']:,.2f} | **{current_p:,.2f}** | {h['total_cost']:,.0f} | **{roi:+.2f}%** |\n"
        else:
            md += "_보유 종목 없음_\n"

        portfolio_path = f"manager/portfolio.md"
        write_markdown(portfolio_path, md)

    def synchronize_balances(self, agent_name: str) -> str:
        """업비트 실잔고 기반 포트폴리오 100% 동기화 및 재배분 실행"""
        logger.info("[System] 🔄 업비트 실잔고 기반 포트폴리오 동기화 실행 중...")

        try:
            broker = UpbitBroker()
            balances = broker.get_balances()

            # 1. 실제 보유 잔고 및 코인 원가 파악
            total_cash = 0.0
            coin_holdings = {}

            for b in balances:
                currency = b.get("currency")
                balance = float(b.get("balance", "0"))
                avg_price = float(b.get("avg_buy_price", "0"))

                if balance <= 0:
                    continue

                if currency == "KRW":
                    total_cash = balance
                elif (
                    balance * avg_price > 100
                ):  # 에어드랍(100원 미만) 및 원가 없는 코인 제외
                    if currency not in (
                        "WEMIX",
                        "APENFT",
                        "MEETONE",
                        "HORUS",
                        "ADD",
                        "CHL",
                        "BLACK",
                    ):
                        ticker = f"KRW-{currency}"
                        coin_holdings[ticker] = {
                            "volume": balance,
                            "avg_price": avg_price,
                            "total_cost": balance * avg_price,
                        }

            total_coin_cost = sum(v["total_cost"] for v in coin_holdings.values())
            true_total_capital = total_cash + total_coin_cost

            if not agent_name:
                return "❌ 동기화 실패: 에이전트 이름이 필요합니다."

            target_capital_per_agent = true_total_capital

            self.portfolios[agent_name] = {
                "cash": 0.0,
                "holdings": {},
                "initial_capital": target_capital_per_agent,
                "total_trades": self.portfolios.get(agent_name, {}).get(
                    "total_trades", 0
                ),
                "winning_trades": self.portfolios.get(agent_name, {}).get(
                    "winning_trades", 0
                ),
                "is_halted": self.portfolios.get(agent_name, {}).get(
                    "is_halted", False
                ),
            }

            for old_agent in list(self.portfolios.keys()):
                if old_agent != agent_name:
                    del self.portfolios[old_agent]
                    self.db.delete_portfolio(old_agent)

            self.total_capital = true_total_capital
            allocated_costs = 0.0

            # 3. 신규 및 기존 코인 메타데이터 보존하며 업데이트
            old_holdings = self.portfolios.get(agent_name, {}).get("holdings", {})
            for ticker, data in coin_holdings.items():
                if ticker in old_holdings:
                    # 기존 메타데이터 유지
                    data["max_price"] = old_holdings[ticker].get(
                        "max_price", data["avg_price"]
                    )
                    data["sl_levels_hit"] = old_holdings[ticker].get(
                        "sl_levels_hit", []
                    )
                    data["tp_levels_hit"] = old_holdings[ticker].get(
                        "tp_levels_hit", []
                    )
                    data["atr_14"] = old_holdings[ticker].get("atr_14", 0)
                    data["strategy"] = old_holdings[ticker].get("strategy", "Unknown")
                else:
                    data["max_price"] = data["avg_price"]
                    data["sl_levels_hit"] = []
                    data["tp_levels_hit"] = []
                    data["atr_14"] = 0
                    data["strategy"] = "Unknown"

                self.portfolios[agent_name]["holdings"][ticker] = data
                allocated_costs += data["total_cost"]

            required_cash = target_capital_per_agent - allocated_costs
            self.portfolios[agent_name]["cash"] = required_cash

            self.save_state()
            self.export_portfolio_report(agent_name)

            msg = (
                f"✅ **실계좌 동기화 100% 완료**\n\n"
                f"💰 총 자본금: {true_total_capital:,.0f} KRW\n"
                f"💳 보유 현금: {total_cash:,.0f} KRW\n"
                f"🪙 코인 원가: {total_coin_cost:,.0f} KRW\n\n"
                f"👥 **매니저에게 {target_capital_per_agent:,.0f} KRW 배분완료.**"
            )
            logger.info(f"[System] 동기화 성공: {true_total_capital} KRW 분배 완료.")
            return msg

        except Exception as e:
            traceback.print_exc()
            return f"❌ 동기화 실패: {e}"
