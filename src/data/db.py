import sqlite3
import json
import os
from contextlib import contextmanager
from src.utils.logger import logger

class DatabaseManager:
    """SQLite 베이스의 영속성 계층 관리"""
    
    def __init__(self, db_path="data/portfolio.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[DB Error] {e}")
            raise
        finally:
            conn.close()

    def _init_db(self):
        """테이블 스키마 초기화"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 포트폴리오 메인 정보 (현금 잔고 등)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS portfolios (
                    agent_name TEXT PRIMARY KEY,
                    allocated_capital REAL DEFAULT 0,
                    available_cash REAL DEFAULT 0,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    total_gross_profit REAL DEFAULT 0,
                    total_gross_loss REAL DEFAULT 0,
                    peak_value REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0,
                    is_halted BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Migration: add columns if they don't exist
            cursor.execute("PRAGMA table_info(portfolios)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'total_trades' not in columns:
                cursor.execute('ALTER TABLE portfolios ADD COLUMN total_trades INTEGER DEFAULT 0')
            if 'winning_trades' not in columns:
                cursor.execute('ALTER TABLE portfolios ADD COLUMN winning_trades INTEGER DEFAULT 0')
            if 'total_gross_profit' not in columns:
                cursor.execute('ALTER TABLE portfolios ADD COLUMN total_gross_profit REAL DEFAULT 0')
            if 'total_gross_loss' not in columns:
                cursor.execute('ALTER TABLE portfolios ADD COLUMN total_gross_loss REAL DEFAULT 0')
            if 'peak_value' not in columns:
                cursor.execute('ALTER TABLE portfolios ADD COLUMN peak_value REAL DEFAULT 0')
            if 'max_drawdown' not in columns:
                cursor.execute('ALTER TABLE portfolios ADD COLUMN max_drawdown REAL DEFAULT 0')
            
            # 포트폴리오별 보유 종목 상세 내역
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS holdings (
                    agent_name TEXT,
                    ticker TEXT,
                    volume REAL DEFAULT 0,
                    avg_price REAL DEFAULT 0,
                    max_price REAL DEFAULT 0,
                    sl_levels_hit TEXT DEFAULT '[]',
                    strategy TEXT DEFAULT 'Unknown',
                    PRIMARY KEY (agent_name, ticker),
                    FOREIGN KEY (agent_name) REFERENCES portfolios(agent_name) ON DELETE CASCADE
                )
            ''')
            
            cursor.execute("PRAGMA table_info(holdings)")
            holdings_cols = [row[1] for row in cursor.fetchall()]
            if 'strategy' not in holdings_cols:
                cursor.execute("ALTER TABLE holdings ADD COLUMN strategy TEXT DEFAULT 'Unknown'")
            
            # 거래 기록 보관 (이력)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT,
                    ticker TEXT,
                    side TEXT, -- 'buy' or 'sell'
                    volume REAL,
                    price REAL,
                    executed_funds REAL,
                    paid_fee REAL,
                    strategy TEXT DEFAULT 'Unknown',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute("PRAGMA table_info(trade_history)")
            trade_cols = [row[1] for row in cursor.fetchall()]
            if 'strategy' not in trade_cols:
                cursor.execute("ALTER TABLE trade_history ADD COLUMN strategy TEXT DEFAULT 'Unknown'")

    def save_portfolio(self, agent_name: str, data: dict):
        """포트폴리오 정보(현금, 중단 여부 등)를 DB에 저장/업데이트"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO portfolios (agent_name, allocated_capital, available_cash, total_trades, winning_trades, total_gross_profit, total_gross_loss, peak_value, max_drawdown, is_halted, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(agent_name) DO UPDATE SET
                    allocated_capital=excluded.allocated_capital,
                    available_cash=excluded.available_cash,
                    total_trades=excluded.total_trades,
                    winning_trades=excluded.winning_trades,
                    total_gross_profit=excluded.total_gross_profit,
                    total_gross_loss=excluded.total_gross_loss,
                    peak_value=excluded.peak_value,
                    max_drawdown=excluded.max_drawdown,
                    is_halted=excluded.is_halted,
                    updated_at=CURRENT_TIMESTAMP
            ''', (
                agent_name,
                data.get("allocated_capital", 0),
                data.get("available_cash", 0),
                data.get("total_trades", 0),
                data.get("winning_trades", 0),
                data.get("total_gross_profit", 0),
                data.get("total_gross_loss", 0),
                data.get("peak_value", 0),
                data.get("max_drawdown", 0),
                1 if data.get("is_halted", False) else 0
            ))

    def save_holdings(self, agent_name: str, holdings: dict):
        """해당 콜에서는 특정 에이전트의 모든 보유 종목을 갱신 (제공된 항목 외 데이터는 삭제)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 현재 메모리에 있는 티커 목록
            active_tickers = list(holdings.keys())
            
            # DB에서 이 에이전트의 기존 티커들을 확인하여, holdings에 없는 것은 삭제
            if active_tickers:
                placeholders = ', '.join(['?'] * len(active_tickers))
                cursor.execute(f'''
                    DELETE FROM holdings 
                    WHERE agent_name = ? AND ticker NOT IN ({placeholders})
                ''', [agent_name] + active_tickers)
            else:
                # holdings가 비어있다면 해당 에이전트의 모든 보유 종목 삭제
                cursor.execute('DELETE FROM holdings WHERE agent_name = ?', (agent_name,))

            # 신규/업데이트 데이터 반영
            for ticker, info in holdings.items():
                if info.get("volume", 0) <= 0:
                    cursor.execute('DELETE FROM holdings WHERE agent_name=? AND ticker=?', (agent_name, ticker))
                else:
                    cursor.execute('''
                        INSERT INTO holdings (agent_name, ticker, volume, avg_price, max_price, sl_levels_hit, strategy)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(agent_name, ticker) DO UPDATE SET
                            volume=excluded.volume,
                            avg_price=excluded.avg_price,
                            max_price=excluded.max_price,
                            sl_levels_hit=excluded.sl_levels_hit,
                            strategy=excluded.strategy
                    ''', (
                        agent_name,
                        ticker,
                        info.get("volume", 0),
                        info.get("avg_price", 0),
                        info.get("max_price", 0),
                        json.dumps(info.get("sl_levels_hit", [])),
                        info.get("strategy", "Unknown")
                    ))

    def load_portfolio_state(self) -> dict:
        """모든 포트폴리오와 보유 정보를 Dict 형태로 반환 (기존 json 호환용)"""
        state = {}
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 포트폴리오 로드
            cursor.execute('SELECT * FROM portfolios')
            for row in cursor.fetchall():
                agent_name = row['agent_name']
                state[agent_name] = {
                    "allocated_capital": row['allocated_capital'],
                    "available_cash": row['available_cash'],
                    "total_trades": row['total_trades'] if 'total_trades' in row.keys() else 0,
                    "winning_trades": row['winning_trades'] if 'winning_trades' in row.keys() else 0,
                    "total_gross_profit": row['total_gross_profit'] if 'total_gross_profit' in row.keys() else 0,
                    "total_gross_loss": row['total_gross_loss'] if 'total_gross_loss' in row.keys() else 0,
                    "peak_value": row['peak_value'] if 'peak_value' in row.keys() else 0,
                    "max_drawdown": row['max_drawdown'] if 'max_drawdown' in row.keys() else 0,
                    "is_halted": bool(row['is_halted']),
                    "holdings": {}
                }
            
            # 보유 정보 로드
            cursor.execute('SELECT * FROM holdings')
            for row in cursor.fetchall():
                agent_name = row['agent_name']
                ticker = row['ticker']
                if agent_name in state:
                    state[agent_name]["holdings"][ticker] = {
                        "volume": row['volume'],
                        "avg_price": row['avg_price'],
                        "max_price": row['max_price'],
                        "sl_levels_hit": json.loads(row['sl_levels_hit']),
                        "strategy": row['strategy'] if 'strategy' in row.keys() else "Unknown"
                    }
        return state

    def rename_agent(self, old_name: str, new_name: str):
        """에이전트 이름을 변경합니다 (DB 마이그레이션용)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # 새 이름이 이미 존재하면 오래된 것을 삭제
            cursor.execute('DELETE FROM portfolios WHERE agent_name = ?', (new_name,))
            cursor.execute('DELETE FROM holdings WHERE agent_name = ?', (new_name,))
            # 이름 변경
            cursor.execute(
                'UPDATE portfolios SET agent_name = ? WHERE agent_name = ?',
                (new_name, old_name)
            )
            cursor.execute(
                'UPDATE holdings SET agent_name = ? WHERE agent_name = ?',
                (new_name, old_name)
            )
            cursor.execute(
                'UPDATE trade_history SET agent_name = ? WHERE agent_name = ?',
                (new_name, old_name)
            )
            logger.info(f"[DB] 에이전트 이름 변경: '{old_name}' → '{new_name}'")

    def delete_portfolio(self, agent_name: str):
        """에이전트 정보와 보유 종목을 DB에서 완전히 삭제"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM holdings WHERE agent_name = ?', (agent_name,))
            cursor.execute('DELETE FROM portfolios WHERE agent_name = ?', (agent_name,))
            logger.info(f"[DB] {agent_name} 포트폴리오 및 대장 삭제 완료")

    def record_trade(self, agent_name: str, ticker: str, side: str, volume: float, price: float, executed_funds: float, paid_fee: float, strategy: str = "Unknown"):
        """새로운 거래 기록을 추가"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trade_history (agent_name, ticker, side, volume, price, executed_funds, paid_fee, strategy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (agent_name, ticker, side, volume, price, executed_funds, paid_fee, strategy))

    def clear_trade_history(self):
        """trade_history 테이블의 모든 기록을 삭제"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trade_history')
            logger.info("[DB] trade_history 테이블 기록 전체 삭제 완료")

    def delete_old_trade_history(self, days: int = 7):
        """n일 이상 경과한 trade_history 과거 기록을 삭제"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM trade_history
                WHERE timestamp <= datetime('now', ?)
            ''', (f'-{days} days',))
            logger.info(f"[DB] {days}일 이상 경과한 trade_history 전체 기록 삭제 완료")
    def get_trade_history(self, agent_name: str) -> list:
        """에이전트의 전체 거래 기록을 반환합니다."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM trade_history 
                WHERE agent_name = ? 
                ORDER BY timestamp ASC
            ''', (agent_name,))
            return [dict(row) for row in cursor.fetchall()]
