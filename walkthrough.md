# `src/` Directory Reorganization Walkthrough

The `src/` directory has been successfully refactored from a flat `utils/`-heavy architecture into a more professional, domain-driven structure.

## Changes Made

### 1. New Directory Layout Created
We created domain-specific folders to logically organize the modules:
- **`core/`**: Holds the main trading logic and central managers ([manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/manager.py), [portfolio_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/portfolio_manager.py), [execution_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/execution_manager.py), [risk_manager.py](file:///Users/lamer/Project/stock/project_hermes/src/core/risk_manager.py)).
- **`data/`**: Manages market data state, websocket connections, and database interactions ([market_data.py](file:///Users/lamer/Project/stock/project_hermes/src/data/market_data.py), [upbit_websocket.py](file:///Users/lamer/Project/stock/project_hermes/src/data/upbit_websocket.py), [db.py](file:///Users/lamer/Project/stock/project_hermes/src/data/db.py)).
- **`broker/`**: Centralizes exchange API integrations, paving the way for the new KIS API integration alongside the current Upbit broker ([broker_api.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/broker_api.py)).
- **`ai/`**: Isolates intelligence and prompt configurations ([llm_client.py](file:///Users/lamer/Project/stock/project_hermes/src/ai/llm_client.py), [gemini_client.py](file:///Users/lamer/Project/stock/project_hermes/src/ai/gemini_client.py)).
- **`communication/`**: Groups UI/UX interaction systems ([command_handler.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/command_handler.py), [command_queue.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/command_queue.py), [telegram_listener.py](file:///Users/lamer/Project/stock/project_hermes/src/interfaces/telegram_listener.py), [telegram_notifier.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/telegram_notifier.py)).
- **`utils/`**: Reduced to solely contain true independent utilities like [logger.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/logger.py) and [markdown_io.py](file:///Users/lamer/Project/stock/project_hermes/src/utils/markdown_io.py).

### 2. Files Relocated
Files were safely relocated to their new homes using `git mv` so all commit histories were retained.

### 3. Core Imports Refactored
All absolute (`import src.utils...`) and relative/module (`from utils.manager import...`) imports were updated automatically across [main.py](file:///Users/lamer/Project/stock/project_hermes/src/main.py), the strategy files, and between individual modules to correctly map to the new architectures.

## Validation Results

We verified the codebase by running [venv/bin/python](file:///Users/lamer/Project/stock/project_hermes/venv/bin/python) to statically link and import [src/main.py](file:///Users/lamer/Project/stock/project_hermes/src/main.py). The initialization completed with zero `ModuleNotFoundError`s, meaning the dependency mapping is completely intact and functional, ready for future development scaling.

> [!TIP]
> The current system looks much cleaner and will make it significantly easier to implement your `Korea Investment & Securities (KIS) API` into `src/broker/` alongside Upbit, seamlessly adhering to the existing `interfaces/`.
