import cProfile
import pstats
from pstats import SortKey
import os
import sys

# Patch sys.argv to run backtest_system.py with small arguments
sys.argv = ["backtest_system.py", "--days", "1"]

# We will profile the backtest_system script
import src.backtest_system as bs

if __name__ == "__main__":
    cProfile.run("bs.backtest_system(days=1, update=False)", "restats")
    p = pstats.Stats("restats")
    p.strip_dirs().sort_stats("cumulative").print_stats(30)
