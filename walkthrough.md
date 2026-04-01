# Walkthrough: Fixing KRW-FLOCK Double Buy Issue

I have investigated and resolved the issue where `KRW-FLOCK` was recorded as being bought twice in the same cycle.

## 🏁 Problem Summary
The "double buy" was caused by a **race condition** in `ExecutionManager.check_pending_orders()`. This function was being called simultaneously from:
1. The **main trading cycle thread** (at the start and end of each cycle).
2. The **websocket tick handler thread** (every time a new price tick arrived).

If an order was filled just as a new tick arrived, both threads could see the order as "done" at the same time and attempt to record the buy in the [PortfolioManager](file:///Users/home/Project/project_hermes/src/core/portfolio_manager.py#11-808), leading to duplicate entries and incorrect cash balance calculations.

## 🛠️ Changes Made

### [Component: core]

#### [execution_manager.py](file:///Users/home/Project/project_hermes/src/core/execution_manager.py)
- Introduced `threading.Lock` to ensure [check_pending_orders()](file:///Users/home/Project/project_hermes/src/core/execution_manager.py#25-148) is atomic.
- Wrapped the entire order checking and recording logic within the lock context.

## 🧪 Verification Results

### Automated Tests
I created a stress test script [tests/test_execution_race.py](file:///Users/home/Project/project_hermes/tests/test_execution_race.py) that simulates 10 concurrent threads calling [check_pending_orders()](file:///Users/home/Project/project_hermes/src/core/execution_manager.py#25-148) for a single completed order.

**Result:**
```
record_buy call count: 1
✅ Race condition fix verified: record_buy called only once.
```
The test confirmed that the locking mechanism successfully prevents duplicate recordings.

---
**Note:** The `/tmp/` directory has been cleaned of temporary test artifacts. The fix is now active in the codebase.
