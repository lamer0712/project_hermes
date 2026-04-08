import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add root directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest_system import backtest_system
from src.strategies.strategy_manager import StrategyManager
from src.utils.logger import logger

def run_strategy_analysis(days: int = 15, strategy_name: str = None):
    sm = StrategyManager()
    all_strategies = sm.list_strategies()
    
    # 분석할 전략 목록 (보유 중인 주요 전략 중심)
    default_targets = [
        "Breakout",
        "PullbackTrend",
        "VWAPReversion",
        "MeanReversion"
    ]
    
    # 1. 특정 전략이 지정된 경우
    if strategy_name:
        if strategy_name not in all_strategies:
            logger.error(f"❌ '{strategy_name}' 전략을 찾을 수 없습니다. 사용 가능한 전략: {all_strategies}")
            return
        target_strategies = [strategy_name]
    else:
        # 2. 전체 전략 (등록된 것만 필터링)
        target_strategies = [s for s in default_targets if s in all_strategies]
    
    results = []
    
    logger.info(f"🔍 총 {len(target_strategies)}개 전략 분석 시작... (전략: {target_strategies})")
    
    for strategy in target_strategies:
        try:
            logger.info(f"\n>>> Analyzing Strategy: {strategy} <<<")
            # 개별 전략 백테스트 실행 (데이터는 캐시된 것 사용하도록 update=False)
            summary = backtest_system(days=days, update=False, force_strategy=strategy)
            
            if summary:
                results.append({
                    "Strategy": strategy,
                    "ROI (%)": summary["return_rate"],
                    "Win Rate (%)": summary["win_rate"],
                    "Profit Factor": summary["profit_factor"],
                    "RR Ratio": summary["risk_reward_ratio"],
                    "MDD (%)": summary.get("mdd", 0),
                    "Total Trades": summary["total_trades"]
                })
        except Exception as e:
            logger.error(f"Strategy {strategy} failed: {e}")
            continue

    # 결과 리포트 생성
    if not results:
        logger.error("분석 결과가 없습니다.")
        return

    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values(by="ROI (%)", ascending=False)
    
    # 파일로 저장 (Markdown)
    report_path = "strategy_analysis_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 📊 Strategy Performance Analysis Report\n\n")
        f.write(f"**Analysis Period:** {days} Days\n")
        f.write(f"**Generated At:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        f.write("## 1. Performance Overview\n\n")
        f.write(df_results.to_markdown(index=False))
        f.write("\n\n")
        
        f.write("## 2. Summary & Strategy Insight\n\n")
        best_strategy = df_results.iloc[0]["Strategy"]
        worst_strategy = df_results.iloc[-1]["Strategy"]
        
        f.write(f"- 🚀 **Best Performer:** {best_strategy}\n")
        f.write(f"- ⚠️ **Worst Performer:** {worst_strategy}\n\n")
        
        f.write("### 💡 Analysis Insights:\n")
        f.write("- **ROI & Win Rate:** 각 전략의 개별 수익률과 승률을 비교하여 어떤 장세에 적합한지 판단합니다.\n")
        f.write("- **Profit Factor:** 1보다 크면 수익성 있는 전략이며, 높을수록 효율적입니다.\n")
        f.write("- **MDD:** 리스크 노출도를 나타내며, 수익률 대비 MDD가 낮은 전략이 우수합니다.\n")
        f.write("- **Total Trades:** 거래 횟수가 너무 많으면 수수료로 인해 실전 수익이 낮아질 수 있습니다.\n")

    logger.info(f"\n✅ 전략 분석 완료! 리포트가 생성되었습니다: {report_path}")
    print(df_results)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Strategy Performance Analyzer")
    parser.add_argument("--days", type=int, default=15, help="Backtest days")
    parser.add_argument("--strategy", "-s", type=str, default=None, help="Specific strategy name to test")
    args = parser.parse_args()
    
    run_strategy_analysis(days=args.days, strategy_name=args.strategy)
