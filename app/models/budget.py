"""Pydantic models for the budget overflow engine."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BudgetRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    monthly_budget: float = Field(ge=0)
    carry_cap: float = Field(ge=0)
    priority: int = 100


class CapsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    medical_upper_cap: float = Field(default=10000, ge=0)
    emergency_monthly_cap: float = Field(default=5000, ge=0)


class BudgetRuleIn(BaseModel):
    category: str = Field(min_length=1)
    monthly_budget: float = Field(ge=0)
    carry_cap: float = Field(ge=0)
    priority: int = 100


class BudgetRulePatch(BaseModel):
    monthly_budget: float | None = Field(default=None, ge=0)
    carry_cap: float | None = Field(default=None, ge=0)
    priority: int | None = None


class CapsPatch(BaseModel):
    medical_upper_cap: float | None = Field(default=None, ge=0)
    emergency_monthly_cap: float | None = Field(default=None, ge=0)


class OverflowRow(BaseModel):
    month: str
    category: str
    budget: float
    actual: float
    remaining: float
    carry_buffer: float
    overflow: float
    to_medical: float
    to_emergency: float
    med_balance: float
    emerg_balance: float
    notes: str | None = None


class OverflowResult(BaseModel):
    rows: list[OverflowRow]
    next_carry: dict[str, float]
    med_balance_out: float
    emerg_balance_out: float
    warnings: list[str] = Field(default_factory=list)

    def to_records(self) -> list[dict[str, Any]]:
        return [r.model_dump() for r in self.rows]
