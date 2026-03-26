"""
전략별 수익률 분석 모듈
trade_history DB에서 매수/매도를 페어링하여 전략별 성과를 계산합니다.
"""

import sqlite3
import os
from collections import defaultdict
from datetime import datetime

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "portfolio.db"
)


def load_trades(db_path: str) -> list[dict]:
    """trade_history에서 모든 거래 기록을 시간순으로 로드"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_history ORDER BY timestamp ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def pair_trades(trades: list[dict]) -> list[dict]:
    """매수-매도 페어링 (FIFO 방식)"""
    buy_queue: dict[str, list[dict]] = defaultdict(list)
    completed_trades = []

    for trade in trades:
        ticker = trade["ticker"]
        side = trade["side"]

        if side == "buy":
            buy_queue[ticker].append(trade)
        elif side == "sell":
            if buy_queue[ticker]:
                buy_trade = buy_queue[ticker].pop(0)

                buy_cost = buy_trade["executed_funds"] or (
                    buy_trade["volume"] * buy_trade["price"]
                )
                sell_revenue = trade["executed_funds"] or (
                    trade["volume"] * trade["price"]
                )

                buy_fee = buy_trade["paid_fee"] or 0
                sell_fee = trade["paid_fee"] or 0
                total_fee = buy_fee + sell_fee

                profit = sell_revenue - sell_fee - buy_cost
                profit_pct = (profit / buy_cost * 100) if buy_cost > 0 else 0

                strategy = buy_trade["strategy"] or "Unknown"

                buy_time = datetime.fromisoformat(buy_trade["timestamp"])
                sell_time = datetime.fromisoformat(trade["timestamp"])
                holding_hours = (sell_time - buy_time).total_seconds() / 3600

                completed_trades.append(
                    {
                        "ticker": ticker,
                        "strategy": strategy,
                        "buy_price": buy_trade["price"],
                        "sell_price": trade["price"],
                        "buy_cost": buy_cost,
                        "sell_revenue": sell_revenue - sell_fee,
                        "profit": profit,
                        "profit_pct": profit_pct,
                        "total_fee": total_fee,
                        "buy_time": buy_trade["timestamp"],
                        "sell_time": trade["timestamp"],
                        "holding_hours": holding_hours,
                    }
                )

    return completed_trades


def analyze_by_strategy(completed_trades: list[dict]) -> dict:
    """전략별 성과 집계"""
    strategy_stats = defaultdict(
        lambda: {
            "trades": [],
            "total_profit": 0,
            "total_cost": 0,
            "wins": 0,
            "losses": 0,
            "total_fee": 0,
            "holding_hours": [],
        }
    )

    for trade in completed_trades:
        strategy = trade["strategy"]
        stats = strategy_stats[strategy]
        stats["trades"].append(trade)
        stats["total_profit"] += trade["profit"]
        stats["total_cost"] += trade["buy_cost"]
        stats["total_fee"] += trade["total_fee"]
        stats["holding_hours"].append(trade["holding_hours"])
        if trade["profit"] > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

    return dict(strategy_stats)


def generate_report(db_path: str = None) -> str:
    """전략별 수익률 분석 리포트를 문자열로 반환"""
    db = db_path or DEFAULT_DB_PATH

    if not os.path.exists(db):
        return "❌ DB 파일을 찾을 수 없습니다."

    trades = load_trades(db)
    if not trades:
        return "❌ 거래 기록이 없습니다."

    completed = pair_trades(trades)
    if not completed:
        return "❌ 완료된 왕복 거래가 없습니다. (매수만 있고 매도 미체결)"

    stats = analyze_by_strategy(completed)

    # 전체 요약
    total_profit = sum(t["profit"] for t in completed)
    total_cost = sum(t["buy_cost"] for t in completed)
    total_fee = sum(t["total_fee"] for t in completed)
    total_wins = sum(1 for t in completed if t["profit"] > 0)
    total_losses = sum(1 for t in completed if t["profit"] <= 0)
    win_rate = total_wins / max(1, total_wins + total_losses) * 100
    roi = total_profit / max(1, total_cost) * 100

    lines = []
    lines.append("📊 전략별 수익률 리포트")
    lines.append(f"총 {len(completed)}건 왕복 거래")
    lines.append(f"총 손익: {total_profit:+,.0f} KRW ({roi:+.2f}%)")
    lines.append(f"승률: {total_wins}W/{total_losses}L ({win_rate:.0f}%)")
    lines.append(f"수수료: {total_fee:,.0f} KRW")
    lines.append("")

    # 전략별 상세
    for strategy, st in sorted(
        stats.items(), key=lambda x: x[1]["total_profit"], reverse=True
    ):
        count = len(st["trades"])
        wins = st["wins"]
        losses = st["losses"]
        wr = wins / max(1, count) * 100
        avg_pct = sum(t["profit_pct"] for t in st["trades"]) / max(1, count)
        avg_hold = sum(st["holding_hours"]) / max(1, count)
        max_win = max((t["profit_pct"] for t in st["trades"]), default=0)
        max_loss = min((t["profit_pct"] for t in st["trades"]), default=0)

        emoji = "🟢" if st["total_profit"] > 0 else "🔴"

        lines.append(f"{emoji} {strategy} ({count}건)")
        lines.append(f"  손익: {st['total_profit']:+,.0f} (평균 {avg_pct:+.2f}%)")
        lines.append(f"  승률: {wins}W/{losses}L ({wr:.0f}%)")
        lines.append(f"  최대: +{max_win:.2f}% / {max_loss:.2f}%")
        lines.append(f"  보유: 평균 {avg_hold:.1f}h")

        # 개별 거래 (최근 5건만)
        recent = sorted(st["trades"], key=lambda x: x["sell_time"], reverse=True)[:5]
        for t in recent:
            icon = "📈" if t["profit"] > 0 else "📉"
            lines.append(
                f"  {icon} {t['ticker']} {t['profit_pct']:+.2f}% ({t['profit']:+,.0f}) {t['holding_hours']:.1f}h"
            )
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_report())
