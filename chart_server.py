"""
Project Hermes — TradingView-Style Chart Viewer
Flask API server providing OHLCV, trade history, regime, and strategy strength data.
"""

import os
import sys
import sqlite3
import json
import math
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

from flask import Flask, jsonify, request, send_file, Response
from src.utils.market_data import UpbitMarketData
from src.utils.db import DatabaseManager
from src.strategies.strategy_manager import StrategyManager
from src.utils.broker_api import UpbitBroker

app = Flask(__name__)
db = DatabaseManager()
strategy_manager = StrategyManager()
broker = UpbitBroker()

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────


@app.route("/")
def index():
    return send_file("chart_viewer.html")


@app.route("/api/tickers")
def api_tickers():
    """trade_history에 있는 고유 티커 + 현재 보유 티커를 합쳐서 반환"""
    tickers = set(broker.get_dynamic_target_coins(top_n=20))

    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT ticker FROM trade_history ORDER BY ticker")
            for row in cursor.fetchall():
                tickers.add(row["ticker"])
            cursor.execute("SELECT DISTINCT ticker FROM holdings WHERE volume > 0")
            for row in cursor.fetchall():
                tickers.add(row["ticker"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(sorted(list(tickers)))


@app.route("/api/ohlcv")
def api_ohlcv():
    """Upbit에서 OHLCV + 기술 지표를 가져와서 JSON으로 반환"""
    ticker = request.args.get("ticker", "KRW-BTC")
    count = int(request.args.get("count", 200))
    interval = request.args.get("interval", "minutes/15")

    df = UpbitMarketData.get_ohlcv_with_indicators_new(
        ticker, count=count, interval=interval
    )
    if df is None or df.empty:
        return jsonify([])

    def safe_float(v):
        """Convert value to float, returning None for NaN/None."""
        if v is None:
            return None
        try:
            f = float(v)
            return None if math.isnan(f) or math.isinf(f) else f
        except (ValueError, TypeError):
            return None

    records = []
    for _, row in df.iterrows():
        record = {
            "time": row["time"].isoformat() + "Z" if row["time"] is not None else None,
            "open": safe_float(row["open"]),
            "high": safe_float(row["high"]),
            "low": safe_float(row["low"]),
            "close": safe_float(row["close"]),
            "volume": safe_float(row["volume"]),
        }
        # Indicators
        for col in [
            "ma_9",
            "ma_20",
            "ma_50",
            "ma_60",
            "ema_20",
            "ema_50",
            "bb_upper",
            "bb_mid",
            "bb_lower",
            "bb_position",
            "rsi_14",
            "atr_14",
            "adx_14",
            "plus_di_14",
            "minus_di_14",
            "vwap",
            "volume_ma20",
            "high_20",
            "low_20",
            "change_5",
        ]:
            if col in df.columns:
                record[col] = safe_float(row.get(col))
        records.append(record)

    return Response(json.dumps(records), mimetype="application/json")


@app.route("/api/trades")
def api_trades():
    """DB에서 특정 티커의 거래 이력을 반환하고, 매도 시 수익률 계산"""
    ticker = request.args.get("ticker", "KRW-BTC")
    days = int(request.args.get("days", 30))
    target_time = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    trades = []
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, agent_name, ticker, side, volume, price, executed_funds, paid_fee, timestamp
                FROM trade_history
                WHERE ticker = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """,
                (ticker, target_time),
            )
            for row in cursor.fetchall():
                trades.append(dict(row))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Calculate profit for sell trades
    result = []
    buy_stack = []  # track buy prices for profit calculation

    for t in trades:
        entry = {
            "id": t["id"],
            "side": t["side"],
            "price": t["price"],
            "volume": t["volume"],
            "executed_funds": t["executed_funds"],
            "paid_fee": t["paid_fee"],
            "timestamp": t["timestamp"],
            "profit_rate": None,
        }

        if t["side"] == "buy":
            buy_stack.append(t["price"])
        elif t["side"] == "sell" and buy_stack:
            avg_buy = sum(buy_stack) / len(buy_stack)
            entry["profit_rate"] = round((t["price"] - avg_buy) / avg_buy * 100, 2)

        result.append(entry)

    return jsonify(result)


@app.route("/api/regime")
def api_regime():
    """OHLCV 데이터의 각 구간에 대해 regime을 계산하여 반환"""
    ticker = request.args.get("ticker", "KRW-BTC")
    count = int(request.args.get("count", 200))
    interval = request.args.get("interval", "minutes/15")

    df = UpbitMarketData.get_ohlcv_with_indicators_new(
        ticker, count=count, interval=interval
    )
    if df is None or df.empty:
        return jsonify([])

    # Calculate regime at various windows
    regimes = []
    window_size = 60  # minimum data needed for regime detection
    step = 10

    prev_regime = None
    regime_start = None

    for i in range(window_size, len(df)):
        window = df.iloc[: i + 1]
        try:
            regime = UpbitMarketData.regime_detect(ticker, window)
        except Exception:
            regime = "unknown"

        ts = df.iloc[i]["time"].isoformat() + "Z"

        if regime != prev_regime:
            if prev_regime is not None and regime_start is not None:
                regimes.append(
                    {
                        "regime": prev_regime,
                        "start": regime_start,
                        "end": ts,
                    }
                )
            regime_start = ts
            prev_regime = regime

    # Close last regime
    if prev_regime and regime_start:
        regimes.append(
            {
                "regime": prev_regime,
                "start": regime_start,
                "end": df.iloc[-1]["time"].isoformat() + "Z",
            }
        )

    return jsonify(regimes)


@app.route("/api/strategy_strength")
def api_strategy_strength():
    """현재 시점의 각 전략별 시그널 강도를 반환"""
    ticker = request.args.get("ticker", "KRW-BTC")

    # Get market data
    setup_data = UpbitMarketData.get_ohlcv_with_indicators_new(
        ticker, count=200, interval="minutes/60"
    )
    entry_data = UpbitMarketData.get_ohlcv_with_indicators_new(
        ticker, count=200, interval="minutes/15"
    )

    if setup_data is None or setup_data.empty or entry_data is None or entry_data.empty:
        return jsonify([])

    # Detect regime
    try:
        regime = UpbitMarketData.regime_detect(ticker, entry_data)
    except Exception:
        regime = "unknown"

    # Build portfolio info stub (no actual holdings info needed for evaluation)
    portfolio_info = {"holdings": {}, "cash": 0, "total_value": 0}

    # Evaluate each strategy
    strategy_map = {
        "bullish": ["Breakout", "PullbackTrend"],
        "ranging": ["VWAPReversion", "MeanReversion"],
        "volatile": ["Breakout"],
        "bearish": ["Bearish"],
        "panic": ["Panic"],
    }

    results = []
    all_strategies = strategy_manager.list_strategies()

    for strategy_name in all_strategies:
        strategy = strategy_manager.get_strategy(strategy_name)
        if strategy is None:
            continue

        try:
            signal = strategy.evaluate(ticker, setup_data, entry_data, portfolio_info)
            is_active = strategy_name in strategy_map.get(regime, [])
            results.append(
                {
                    "strategy": strategy_name,
                    "signal_type": signal.type.value if signal else "HOLD",
                    "strength": round(signal.strength, 4) if signal else 0,
                    "reason": signal.reason if signal else "N/A",
                    "is_active_for_regime": is_active,
                }
            )
        except Exception as e:
            results.append(
                {
                    "strategy": strategy_name,
                    "signal_type": "ERROR",
                    "strength": 0,
                    "reason": str(e),
                    "is_active_for_regime": False,
                }
            )

    return jsonify(
        {
            "ticker": ticker,
            "regime": regime,
            "btc_regime": UpbitMarketData.btc_regime(),
            "strategies": results,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
