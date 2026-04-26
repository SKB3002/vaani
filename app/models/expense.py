"""Expense-related pydantic models."""
from __future__ import annotations

from datetime import date as date_t  # noqa: N813 — aliased because field name shadows type
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.common import TYPE_CATEGORY_RE, PaymentMethod

_PAYMENT_VALUES = Literal["paid", "paid_cash", "paid_by", "paid_for", "adjusted"]


class ExpenseIn(BaseModel):
    """Incoming expense from manual entry or import."""

    date: date_t
    expense_name: str = Field(min_length=1)
    type_category: str
    payment_method: PaymentMethod
    paid_for_someone: bool = False
    paid_by_someone: bool = False
    person_name: str | None = None
    amount: float = Field(gt=0)
    source: Literal["voice", "manual", "atm_transfer", "import"] = "manual"
    raw_transcript: str | None = None
    notes: str | None = None
    custom_tag: str | None = None
    paid_for_method: Literal["cash", "online"] | None = None
    adjustment_type: Literal["cash_to_online", "online_to_cash"] | None = None

    @field_validator("type_category")
    @classmethod
    def _validate_type_category(cls, v: str) -> str:
        if not TYPE_CATEGORY_RE.match(v):
            raise ValueError(
                "type_category must match '<Need|Want|Investment>, <Food & Drinks|Travel|Enjoyment|Miscellaneous>'"
            )
        return v

    @field_validator("person_name")
    @classmethod
    def _person_required_if_flagged(cls, v: str | None, info) -> str | None:  # type: ignore[no-untyped-def]
        data = info.data
        if (data.get("paid_for_someone") or data.get("paid_by_someone")) and not v:
            raise ValueError("person_name is required when paid_for_someone or paid_by_someone is True")
        return v

    @model_validator(mode="after")
    def _default_payment_subfields(self) -> ExpenseIn:
        # Grid UI doesn't surface sub-dropdowns; apply sensible defaults for the
        # common case. API clients can still override either field explicitly.
        pm = self.payment_method.value if hasattr(self.payment_method, "value") else str(self.payment_method)
        if pm == "paid_for" and self.paid_for_method is None:
            self.paid_for_method = "online"
        if pm != "paid_for":
            self.paid_for_method = None
        if pm == "adjusted" and self.adjustment_type is None:
            self.adjustment_type = "cash_to_online"
        if pm != "adjusted":
            self.adjustment_type = None
        return self


class ExpenseUpdate(BaseModel):
    """Partial update — all fields optional."""

    date: date_t | None = None
    expense_name: str | None = None
    type_category: str | None = None
    payment_method: PaymentMethod | None = None
    paid_for_someone: bool | None = None
    paid_by_someone: bool | None = None
    person_name: str | None = None
    amount: float | None = None
    notes: str | None = None
    custom_tag: str | None = None
    paid_for_method: Literal["cash", "online"] | None = None
    adjustment_type: Literal["cash_to_online", "online_to_cash"] | None = None

    @field_validator("type_category")
    @classmethod
    def _validate_type_category(cls, v: str | None) -> str | None:
        if v is not None and not TYPE_CATEGORY_RE.match(v):
            raise ValueError("invalid type_category")
        return v

    @model_validator(mode="after")
    def _default_payment_subfields(self) -> ExpenseUpdate:
        # Partial updates: default sub-fields if omitted (grid UI never sets them).
        if self.payment_method is None:
            return self
        pm = self.payment_method.value if hasattr(self.payment_method, "value") else str(self.payment_method)
        if pm == "paid_for" and self.paid_for_method is None:
            self.paid_for_method = "online"
        if pm == "adjusted" and self.adjustment_type is None:
            self.adjustment_type = "cash_to_online"
        return self


class Expense(BaseModel):
    """Full expense row as stored in the ledger."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    date: date_t
    created_at: datetime
    expense_name: str
    type_category: str
    payment_method: PaymentMethod
    paid_for_someone: bool
    paid_by_someone: bool
    person_name: str | None = None
    amount: float
    cash_balance_after: float
    online_balance_after: float
    source: str
    raw_transcript: str | None = None
    notes: str | None = None
    import_batch_id: str | None = None
    paid_for_method: Literal["cash", "online"] | None = None
    adjustment_type: Literal["cash_to_online", "online_to_cash"] | None = None


class ParsedExpenseItem(BaseModel):
    """One expense line inside a multi-item voice response."""

    expense_name: str | None = None
    type_category: str | None = None
    payment_method: _PAYMENT_VALUES | None = None
    paid_for_someone: bool = False
    paid_by_someone: bool = False
    person_name: str | None = None
    amount: float | None = None
    paid_for_method: Literal["cash", "online"] | None = None
    adjustment_type: Literal["cash_to_online", "online_to_cash"] | None = None
    needs_clarification: bool = False
    question: str | None = None

    @field_validator("type_category")
    @classmethod
    def _validate_type_category(cls, v: str | None) -> str | None:
        if v is not None and not TYPE_CATEGORY_RE.match(v):
            raise ValueError("invalid type_category")
        return v


class ParsedExpense(BaseModel):
    """Strict JSON schema returned by the LLM parser (§5.3 of the plan)."""

    action: Literal["expense", "atm_transfer", "clarify"]
    date: date_t
    # Multi-item: LLM always returns items[] (1-N expenses per transcript).
    items: list[ParsedExpenseItem] = Field(default_factory=list)
    # Top-level ATM / clarify fields
    atm_amount: float | None = None
    needs_clarification: bool = False
    question: str | None = None
    confidence: float = 1.0
