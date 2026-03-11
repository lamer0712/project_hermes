# Automated Execution Script Architecture

This system uses Markdown documents as the **Single Source of Truth (SSOT)**. It operates via a Python-based execution script that reads these files, sends localized prompts to each Agent (LLM), and dumps the resulting decisions back into Markdown files.

## Directory / Module Structure (Proposed)

```text
/investment_firm
 ├── /src
 │   ├── main.py                # Main system entry point, runs the scheduler
 │   ├── /agents                # Agent class implementations
 │   │   ├── base_agent.py      # LLM API wrapper, generic MD read/write logic
 │   │   ├── manager.py         # Manager Agent logic
 │   │   ├── investor.py        # Investment Agent logic
 │   │   └── global_risk.py     # Global Risk (+Kill Switch) logic
 │   ├── /utils
 │   │   ├── markdown_io.py     # Markdown parsing and generation utilities
 │   │   ├── market_data.py     # External price data fetcher (e.g., Yahoo Finance, Binance API)
 │   │   └── broker_api.py      # Real stock/crypto brokerage API integration for order execution
 ├── /rules                     # System prompts and guideline documents
 ├── /manager                   # Manager state files (current portfolio, HR records)
 ├── /agents                    # Individual Investment Agent directories
 └── /reports                   # Generated reports (hourly, daily)
```

## Core Component Operations

### 1. `markdown_io.py`
- Parses Markdown documents into Python objects (Dict/List).
- Dumps system execution results back into Markdown format on disk.
- Example: Appends a new trade record to the bottom of `trades.md`.

### 2. `base_agent.py`
- Base class wrapping the LangChain or OpenAI API.
- Combines three elements: **System Prompt** (`rules/prompt_*.md`), **State** (`agents/*/strategy.md`, `trades.md`), and **Market Data** to construct the final LLM prompt.
- Receives the LLM response (preferring JSON format), parses it, and executes real-world actions (file I/O, trading) via `utils`.

### 3. Scheduler (`main.py`)
- Utilizes Python's built-in `schedule` library or system `cron`.
- **High Frequency Loop (Every 1/5 Mins) - $0 API Cost**:
  - Runs `global_risk.py` -> Checks market data, calculates VaR/MDD limits -> Checks Kill Switch conditions.
  - Runs `investor.py` -> `execute_trade_by_rule()`: Pure python code parses parameters from `.md` files and executes trades via broker API. Absolutely no LLM involvement.
- **Hourly Loop (Every Hour at :30) - LLM Wakes Up**:
  - Runs `manager.py` -> Aggregates performance of all Investment Agents, makes HR decisions, updates portfolio.
  - Runs `investor.py` -> `review_and_update_strategy()`: LLM analyzes past 1 hour's trades and modifies the JSON parameters inside `strategy.md`.
- **Daily Loop (Every Midnight)**:
  - Runs the Shadow Agent (Audit) batch script -> Generates performance summary reports.

## Agent Communication Bridge
- There is **no direct, in-memory communication** between agents in this system.
- ALL communication is strictly asynchronous, mediated by **shared Markdown files** on disk.
  - When the Manager evaluates an Investment Agent, it does not query the agent via API. Instead, it reads the `performance.md` and `trades.md` files written by that agent.
  - This architecture is the core mechanism that ensures the system's greatest strengths: **"Absolute Persistence"** and **"Zero Hallucination"**.
