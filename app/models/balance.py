"""Balance models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BalanceSnapshot(BaseModel):
    asof: datetime
    cash_balance: float
    online_balance: float
    reason: str


class BalanceSeedIn(BaseModel):
    cash_balance: float
    online_balance: float
    reason: Literal["seed", "manual_adjust"] = "seed"
    mode: Literal["set", "add"] = "set"


class AtmTransferIn(BaseModel):
    amount: float = Field(gt=0, description="Amount moved from online to cash")
