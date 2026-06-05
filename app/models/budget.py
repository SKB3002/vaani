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
    # Optional Need/Want/Investment classification for a custom-tag rule. When the
    # category is a bare custom tag (not a "Type, Category" built-in), this records
    # the tag's type so the grouped Table C view can roll it up. Ignored for
    # built-in categories whose type is implicit in the prefix.
    type: str | None = Field(default=None, pattern=r"^(Need|Want|Investment)$")


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


class RunningCategoryState(BaseModel):
    """Per-category running pool. One row per `budget_rules` category."""

    model_config = ConfigDict(extra="ignore")

    category: str
    current_budget: float = 0.0
    last_rolled_month: str = ""  # "YYYY-MM"; empty = never rolled
    updated_at: str = ""


class RunningStateResult(BaseModel):
    """Engine output: one Table-C row per category + new pots + audit info."""

    rows: list[OverflowRow]
    new_state: list[RunningCategoryState]
    med_balance_out: float
    emerg_balance_out: float
    warnings: list[str] = Field(default_factory=list)
    rolled_categories: list[str] = Field(default_factory=list)


class TagCreateIn(BaseModel):
    """Create a custom spend tag with a Need/Want/Investment type.

    Auto-creates a budget_rules row (so the tag becomes its own Table C line)
    and records the tag->type mapping (so the grouped view rolls it up and the
    LLM can auto-apply it).
    """

    name: str = Field(min_length=1, max_length=60)
    type: str = Field(pattern=r"^(Need|Want|Investment)$")
    monthly_budget: float = Field(default=0.0, ge=0)
    carry_cap: float = Field(default=0.0, ge=0)
    priority: int = 100


class BudgetAdjustIn(BaseModel):
    """Add/Set button payload."""

    category: str = Field(min_length=1)
    amount: float = Field(ge=0)
    kind: str = Field(pattern=r"^(add|set)$")
    note: str | None = None
