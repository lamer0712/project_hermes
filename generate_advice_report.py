import sqlite3
import re
from datetime import datetime, timedelta
import os

DB_PATH = "data/portfolio.db"
LOG_PATH = "backend.log"
REPORT_PATH = "advice_report.md"


def generate_report(days=3):
    target_time = datetime.now() - timedelta(days=days)
    target_time_str = target_time.strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"Generating report for the last {days} days (from {target_time_str} to now)..."
    )

    # 1. Fetch Trade History from DB
    trades = []
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT timestamp, agent_name, ticker, side, volume, price, executed_funds, paid_fee
                FROM trade_history
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
            """,
                (target_time_str,),
            )

            for row in cursor.fetchall():
                trades.append(dict(row))
            conn.close()
        except Exception as e:
            print(f"Error reading DB: {e}")
    else:
        print(f"DB file not found at {DB_PATH}")

    # 2. Extract Key Logs from backend.log
    key_logs = []
    timestamp_pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                current_time_str = ""
                for line in f:
                    match = timestamp_pattern.search(line)
                    if match:
                        current_time_str = match.group(1)

                    if current_time_str and current_time_str >= target_time_str:
                        # Extract significant lines
                        if "⚠️" in line:
                            continue
                        if (
                            ("BUY" in line or "SELL" in line)
                            or ("매수" in line or "매도" in line)
                            or (
                                "[Error]" in line
                                or "[Warning]" in line
                                or "Exception" in line
                            )
                            or ("reasons: [" in line and "reasons: []" not in line)
                            or ("Best Buy" in line)
                        ):

                            key_logs.append(line.rstrip())
        except Exception as e:
            print(f"Error reading log: {e}")
    else:
        print(f"Log file not found at {LOG_PATH}")

    # 3. Generate Markdown Report
    report_content = [
        f"# AI Trading System Advice & Audit Report",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Timeframe:** Last {days} days (since {target_time_str})",
        "",
        "## 1. Trade History Summary",
        "Recent trades executed according to the database:",
        "",
        "| Timestamp | Agent | Ticker | Side | Volume | Price | Value (KRW) | Fee (KRW) |",
        "|-----------|-------|--------|------|--------|-------|-------------|-----------|",
    ]

    if trades:
        for t in trades:
            report_content.append(
                f"| {t['timestamp']} | {t['agent_name']} | {t['ticker']} | {t['side']} | {t['volume']} | {t['price']:,.2f} | {t['executed_funds']:,.2f} | {t['paid_fee']:,.2f} |"
            )
    else:
        report_content.extend(
            [
                "| - | - | - | - | - | - | - | - |",
                "",
                "*No trades executed in this timeframe.*",
            ]
        )

    report_content.extend(
        [
            "",
            "## 2. Key Event & Decision Logs",
            "Significant log events including market regimes, filled conditions with reasons, trades, and any errors.",
            "```log",
        ]
    )

    if key_logs:
        report_content.extend(key_logs)
    else:
        report_content.append("No significant logs found in this timeframe.")

    report_content.extend(
        [
            "```",
            "",
            "## 3. Inquiry / Requests for Advice",
            "*Please detail the context or questions you want to ask the AI/Expert based on the above data.*",
            "",
            "1. **Problem:** ",
            "2. **Question:** ",
        ]
    )

    # Save the report
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(report_content))
            f.write("\n")
        print(f"Report successfully generated at: {os.path.abspath(REPORT_PATH)}")
    except Exception as e:
        print(f"Error saving report: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate trading advice report.")
    parser.add_argument(
        "--days",
        type=int,
        default=3,
        help="Number of recent days to include in the report.",
    )
    args = parser.parse_args()

    generate_report(days=args.days)
