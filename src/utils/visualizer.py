import matplotlib
matplotlib.use('Agg')  # GUI 없는 서버 환경용 백엔드 설정
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import os
from datetime import datetime

class Visualizer:
    """
    성과 분석 데이터를 기반으로 그래프 이미지를 생성합니다.
    """
    
    def __init__(self, output_dir: str = "manager/charts"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        # 한글 폰트 설정이 어려울 수 있으므로 기본 테마와 영문 라벨 사용
        sns.set_theme(style="darkgrid")
        plt.rcParams['figure.figsize'] = (10, 6)

    def draw_equity_curve(self, df: pd.DataFrame, agent_name: str) -> str:
        """자산 성장 곡선 그래프 생성"""
        if df.empty:
            return ""

        plt.figure()
        plt.plot(df['timestamp'], df['total_value'], marker='o', linestyle='-', color='#00aaff', linewidth=2)
        
        # 시작 자본 대비 변화율 표시
        initial_val = df['total_value'].iloc[0]
        current_val = df['total_value'].iloc[-1]
        roi = (current_val - initial_val) / initial_val * 100
        
        plt.title(f"Equity Curve: {agent_name} (ROI: {roi:+.2f}%)", fontsize=14, fontweight='bold')
        plt.xlabel("Time")
        plt.ylabel("Total Value (KRW)")
        plt.xticks(rotation=45)
        plt.tight_layout()

        file_path = os.path.join(self.output_dir, f"equity_curve_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        plt.savefig(file_path)
        plt.close()
        return file_path

    def draw_strategy_performance(self, stats_df: pd.DataFrame, agent_name: str) -> str:
        """전략별 수익금 비교 막대 그래프"""
        if stats_df.empty:
            return ""

        plt.figure()
        # total_profit 막대 그래프
        sns.barplot(x=stats_df.index, y=stats_df['total_profit'], palette="viridis")
        
        plt.title(f"Profit by Strategy: {agent_name}", fontsize=14, fontweight='bold')
        plt.xlabel("Strategy")
        plt.ylabel("Total Profit (KRW)")
        plt.xticks(rotation=0)
        
        # 수익금 수치 표시
        for i, val in enumerate(stats_df['total_profit']):
            plt.text(i, val, f"{val:,.0f}", ha='center', va='bottom' if val > 0 else 'top', fontweight='bold')

        plt.tight_layout()
        file_path = os.path.join(self.output_dir, f"strategy_perf_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        plt.savefig(file_path)
        plt.close()
        return file_path

    def draw_win_rate_analysis(self, stats_df: pd.DataFrame, agent_name: str) -> str:
        """전략별 승률 및 Profit Factor 비교"""
        if stats_df.empty:
            return ""

        fig, ax1 = plt.subplots()

        # 승률 (Bar)
        sns.barplot(x=stats_df.index, y=stats_df['win_rate'], ax=ax1, color='#66c2a5', alpha=0.6, label='Win Rate (%)')
        ax1.set_ylabel('Win Rate (%)', color='#2ca25f')
        ax1.set_ylim(0, 100)

        # Profit Factor (Line)
        ax2 = ax1.twinx()
        plt.plot(stats_df.index, stats_df['profit_factor'], color='#f03b20', marker='D', linewidth=2, label='Profit Factor')
        ax2.set_ylabel('Profit Factor', color='#f03b20')
        ax2.axhline(y=1.0, color='gray', linestyle='--', alpha=0.5) # PF 1.0 기준선

        plt.title(f"Efficiency Analysis: {agent_name}", fontsize=14, fontweight='bold')
        fig.tight_layout()

        file_path = os.path.join(self.output_dir, f"efficiency_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        plt.savefig(file_path)
        plt.close()
        return file_path
