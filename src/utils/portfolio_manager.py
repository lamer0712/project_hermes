import os
import json
from datetime import datetime
from src.utils.markdown_io import write_markdown, read_markdown
from src.utils.telegram_notifier import TelegramNotifier
from src.utils.logger import logger


class PortfolioManager:
    """
    가상 자산 포트폴리오 관리 클래스.
    Upbit 계좌 자산을 단일 포트폴리오로 통합하여 관리.
    상태 데이터 구조: {agent_name: {cash, holdings: {ticker: {volume, avg_price, total_cost}}, initial_capital}}
    """

    STATE_FILE = "manager/portfolio_state.json"

    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        self.portfolios = {}
        self.notifier = TelegramNotifier()
        self.load_state()

    def load_state(self):
        """디스크에서 포트폴리오 상태 복원"""
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.total_capital = data.get("total_capital", self.total_capital)
                    self.portfolios = data.get("portfolios", {})
                    logger.info(
                        f"[Manager] 포트폴리오 상태 복원 완료 ('manager' 포트폴리오)"
                    )
            except Exception as e:
                logger.error(f"[Manager] 상태 복원 실패, 초기화: {e}")
                self.portfolios = {}

    def save_state(self):
        """포트폴리오 상태를 디스크에 저장"""
        os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
        data = {
            "total_capital": self.total_capital,
            "portfolios": self.portfolios,
            "last_updated": datetime.now().isoformat(),
        }
        with open(self.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

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
            if (
                total_cost_including_fee > portfolio["cash"] + 5000
            ):
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
            }
        else:
            holdings[ticker] = {
                "volume": volume,
                "avg_price": total_cost_excluding_fee / volume if volume > 0 else price,
                "total_cost": total_cost_excluding_fee,
                "max_price": price,
                "sl_levels_hit": [],
            }

        portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
        self.save_state()
        self.export_portfolio_report(agent_name)
        logger.info(
            f"[Manager] ✅ {agent_name} 매수 기록: {ticker} 거래수량: {volume:.6f}, 단가: {price:,.0f}, 거래금액: {total_cost_excluding_fee:,.0f}, 수수료: {paid_fee:,.2f}, 정산금액: {total_cost_including_fee:,.0f}, 잔여현금: {portfolio['cash']:,.0f})"
        )
        return True

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
        profit = sell_revenue_net - (avg_price * volume)

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
        self.export_portfolio_report(agent_name)
        profit_emoji = "⏫" if profit > 0 else "⏬"
        msg = f"[Manager] {profit_emoji} {agent_name} 매도 기록: {ticker}, 거래수량: {volume:.6f}, 단가: {price:,.0f}, 거래금액: {sell_revenue_gross:,.0f}, 수수료: {paid_fee:,.2f}, 정산금액: {sell_revenue_net:,.0f}, 손익: {profit:+,.0f}, 잔여현금: {portfolio['cash']:,.0f})"
        logger.info(msg)
        self.notifier.send_message(msg)
        return True

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

    def update_holding_metadata(
        self,
        agent_name: str,
        ticker: str,
        max_price: float = None,
        hit_sl_level: float = None,
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

    def get_portfolio_summary(self, agent_name: str, current_prices: dict = None) -> dict:
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
            md += "| 종목 | 수량 | 평균 매입가 | 매입 총액 |\n"
            md += "|------|------|-----------|----------|\n"
            for ticker, h in holdings.items():
                md += f"| {ticker} | {h['volume']:.6f} | {h['avg_price']:,.6f} | {h['total_cost']:,.2f} |\n"
        else:
            md += "_보유 종목 없음_\n"

        portfolio_path = f"manager/portfolio.md"
        write_markdown(portfolio_path, md)
