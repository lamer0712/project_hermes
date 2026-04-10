import pandas as pd
from datetime import datetime, timedelta
from src.data.db import DatabaseManager

class TradeAnalytics:
    """
    거래 내역(trade_history)과 포트폴리오 스냅샷을 분석하여 
    전략별/장세별 성과 지표를 산출합니다.
    """
    
    def __init__(self, db_path: str = "data/portfolio.db"):
        self.db = DatabaseManager(db_path)

    def get_strategy_performance(self, agent_name: str) -> pd.DataFrame:
        """전략별 주요 성과 지표 산출"""
        trades = self.db.get_trade_history(agent_name)
        if not trades:
            return pd.DataFrame()

        df = pd.DataFrame(trades)
        # 매도 거래만 필터링 (수익이 기록된 시점)
        sell_df = df[df['side'] == 'sell'].copy()
        
        if sell_df.empty:
            return pd.DataFrame()

        # 전략별 집계
        stats = sell_df.groupby('strategy').agg({
            'profit': ['count', 'sum', 'mean'],
            'hold_duration_min': 'mean'
        })
        
        stats.columns = ['trades', 'total_profit', 'avg_profit', 'avg_hold_min']
        
        # 승률(Win Rate) 계산
        win_rates = []
        for strategy in stats.index:
            strat_sells = sell_df[sell_df['strategy'] == strategy]
            wins = len(strat_sells[strat_sells['profit'] > 0])
            win_rates.append((wins / len(strat_sells) * 100) if len(strat_sells) > 0 else 0)
        
        stats['win_rate'] = win_rates
        
        # Profit Factor 계산
        pf_list = []
        for strategy in stats.index:
            strat_sells = sell_df[sell_df['strategy'] == strategy]
            gross_profit = strat_sells[strat_sells['profit'] > 0]['profit'].sum()
            gross_loss = abs(strat_sells[strat_sells['profit'] < 0]['profit'].sum())
            pf = (gross_profit / gross_loss) if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0)
            pf_list.append(pf)
            
        stats['profit_factor'] = pf_list
        
        return stats.sort_values('total_profit', ascending=False)

    def get_regime_performance(self, agent_name: str) -> pd.DataFrame:
        """장세별 전략 효율성 분석"""
        trades = self.db.get_trade_history(agent_name)
        if not trades:
            return pd.DataFrame()

        df = pd.DataFrame(trades)
        sell_df = df[df['side'] == 'sell'].copy()
        if sell_df.empty:
            return pd.DataFrame()

        # 장세와 전략별 멀티 인덱스 집계
        regime_stats = sell_df.groupby(['regime', 'strategy']).agg({
            'profit': ['count', 'sum'],
            'hold_duration_min': 'mean'
        })
        regime_stats.columns = ['trades', 'total_profit', 'avg_hold_min']
        
        return regime_stats

    def get_equity_curve_data(self, agent_name: str, days: int = 7) -> pd.DataFrame:
        """자산 성장 곡선 데이터 (Snapshots)"""
        snapshots = self.db.get_snapshots(agent_name, days)
        if not snapshots:
            return pd.DataFrame()
        
        df = pd.DataFrame(snapshots)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
