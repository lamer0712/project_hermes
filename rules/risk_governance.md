# Risk & Governance Guidelines

To ensure the automated investment firm survives unexpected market conditions or internal system errors, a **Triple Safety Net** is officially integrated into operations.

## 1. Global Risk Agent (Firm-Wide Risk Oversight)
- **Role**: Prevents individual risks taken by Investment Agents (e.g., overexposure to a single asset) from aggregating into a catastrophic risk for the entire firm.
- **Monitoring Scope (Continuous)**:
  - Firm-wide Portfolio Value at Risk (VaR).
  - Firm-wide Maximum Drawdown (MDD).
  - Excessive correlation exposure to specific asset classes or sectors.
- **Actions**:
  - Reaching 80% of Hard Limits: Send a **Warning** to the Manager Agent and restrict new entries.
  - Reaching 100% of Hard Limits: Trigger the **Kill Switch**.

## 2. Kill Switch (Emergency Stop System)
- **Role**: An absolute override authority to immediately halt all trading and liquidate (or freeze) positions when specific conditions are met.
- **Trigger Conditions**:
  1. **Market Crash**: Volatility indicators (e.g., VIX) breach predefined extreme levels (e.g., above 40), or major indices drop more than X% in a single day.
  2. **API/Infrastructure Failure**: Consecutive N failures receiving data from stock brokerage APIs, crypto exchange APIs, or market data feeds. (Prevents trading during a hallucination state).
  3. **Cumulative Loss Hard Limit**: Daily/Weekly/Monthly losses on total firm capital exceed permissible thresholds.
  4. **Rogue Agent (Logic Deviation)**: An Investment Agent acts in ways mathematically or logically inconsistent with its own `strategy.md`.
- **Execution Procedure**:
  1. Immediately SUSPEND all Investment Agent processes.
  2. Execute market-order liquidation logic for all currently held positions (can be configured to Hold/Freeze depending on user settings).
  3. Send an emergency alert (Slack/Telegram) to the Human Owner.

## 3. Shadow Agent (Post-mortem Audit)
- **Role**: A retrospective logger and analyst designed to extract transparency and improvements from past decisions.
- **Operations**:
  - Operates asynchronously; does not participate in real-time trading.
  - Every midnight, reads the Manager Agent's HR logs (`hr_records.md`) and the Investment Agents' trade logs (`trades.md`).
  - **Analysis Metric**: "Was this trade rational given the market conditions at the time and the rules in `strategy.md`?"
  - **Deliverable**: Generates an audit report used by the Human Owner to verify agent thought processes and debug prompts.
