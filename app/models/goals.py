"""Pydantic models for goals (Tables A and B)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GoalAIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal_name: str = Field(min_length=1)
    target_amount: float = Field(ge=0)
    current_amount: float = Field(default=0.0, ge=0)
    monthly_contribution: float = Field(default=0.0, ge=0)


class GoalAPatch(BaseModel):
    goal_name: str | None = Field(default=None, min_length=1)
    target_amount: float | None = Field(default=None, ge=0)
    current_amount: float | None = Field(default=None, ge=0)
    monthly_contribution: float | None = Field(default=None, ge=0)


class GoalBIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal_name: str = Field(min_length=1)
    target_amount: float = Field(ge=0)
    manual_saved: float = Field(default=0.0, ge=0)
    auto_added: float = Field(default=0.0, ge=0)
    monthly_contribution: float = Field(default=0.0, ge=0)


class GoalBPatch(BaseModel):
    goal_name: str | None = Field(default=None, min_length=1)
    target_amount: float | None = Field(default=None, ge=0)
    manual_saved: float | None = Field(default=None, ge=0)
    auto_added: float | None = Field(default=None, ge=0)
    monthly_contribution: float | None = Field(default=None, ge=0)


class ContributeIn(BaseModel):
    amount: float = Field(gt=0)
    kind: Literal["manual", "auto"]
