from pydantic import BaseModel, Field, AliasChoices
from typing import Dict, Any, Optional

class ReallocationResponse(BaseModel):
    new_allocations: Dict[str, float] = Field(description="Agent name to new allocation amount in KRW")
    rebalance_reason: str = Field(description="Reason for rebalancing")

class RiskEvalResponse(BaseModel):
    trigger_kill_switch: bool = Field(description="Whether to trigger the kill switch")
    reason: str = Field(default="Unknown severe risk", description="Reason for the kill switch")
    risk_summary: str = Field(default="", description="Detailed risk summary")

class StrategyUpdateResponse(BaseModel):
    update_strategy: bool = Field(
        validation_alias=AliasChoices("update_strategy", "updated", "update"),
        description="Whether to update the strategy or not"
    )
    new_parameters: Optional[Dict[str, Any]] = Field(
        default=None, 
        validation_alias=AliasChoices("new_parameters", "strategy", "parameters", "params"),
        description="Updated strategy parameters if update_strategy is true"
    )
    reason: Optional[str] = Field(default=None, description="Reason for the updates")
