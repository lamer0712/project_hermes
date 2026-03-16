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
                    is_halted BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 포트폴리오별 보유 종목 상세 내역
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS holdings (
                    agent_name TEXT,
                    ticker TEXT,
                    volume REAL DEFAULT 0,
                    avg_price REAL DEFAULT 0,
                    max_price REAL DEFAULT 0,
                    sl_levels_hit TEXT DEFAULT '[]',
                    PRIMARY KEY (agent_name, ticker),
                    FOREIGN KEY (agent_name) REFERENCES portfolios(agent_name) ON DELETE CASCADE
                )
            ''')
            
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
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

    def save_portfolio(self, agent_name: str, data: dict):
        """포트폴리오 정보(현금, 중단 여부 등)를 DB에 저장/업데이트"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO portfolios (agent_name, allocated_capital, available_cash, is_halted, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(agent_name) DO UPDATE SET
                    allocated_capital=excluded.allocated_capital,
                    available_cash=excluded.available_cash,
                    is_halted=excluded.is_halted,
                    updated_at=CURRENT_TIMESTAMP
            ''', (
                agent_name,
                data.get("allocated_capital", 0),
                data.get("available_cash", 0),
                1 if data.get("is_halted", False) else 0
            ))

    def save_holdings(self, agent_name: str, holdings: dict):
        """해당 콜에서는 특정 에이전트의 모든 보유 종목을 갱신 (기존 정보 보존 후 upsert)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for ticker, info in holdings.items():
                if info.get("volume", 0) <= 0:
                    cursor.execute('DELETE FROM holdings WHERE agent_name=? AND ticker=?', (agent_name, ticker))
                else:
                    cursor.execute('''
                        INSERT INTO holdings (agent_name, ticker, volume, avg_price, max_price, sl_levels_hit)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(agent_name, ticker) DO UPDATE SET
                            volume=excluded.volume,
                            avg_price=excluded.avg_price,
                            max_price=excluded.max_price,
                            sl_levels_hit=excluded.sl_levels_hit
                    ''', (
                        agent_name,
                        ticker,
                        info.get("volume", 0),
                        info.get("avg_price", 0),
                        info.get("max_price", 0),
                        json.dumps(info.get("sl_levels_hit", []))
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
                        "sl_levels_hit": json.loads(row['sl_levels_hit'])
                    }
        return state

    def record_trade(self, agent_name: str, ticker: str, side: str, volume: float, price: float, executed_funds: float, paid_fee: float):
        """새로운 거래 기록을 추가"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trade_history (agent_name, ticker, side, volume, price, executed_funds, paid_fee)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (agent_name, ticker, side, volume, price, executed_funds, paid_fee))
