"""BalanceService: append-only balance snapshots + ATM transfer + adjustments."""
from __future__ import annotations

import logging

from app.services.ledger import LedgerWriter
from app.services.tz import now_utc

logger = logging.getLogger(__name__)


class BalanceService:
    def __init__(self, ledger: LedgerWriter) -> None:
        self.ledger = ledger

    def current(self) -> dict[str, float] | None:
        df = self.ledger.read("balances")
        if df.empty:
            return None
        last = df.iloc[-1]
        return {
            "cash_balance": float(last["cash_balance"]),
            "online_balance": float(last["online_balance"]),
        }

    def seed(
        self,
        cash: float,
        online: float,
        reason: str = "seed",
        mode: str = "set",
    ) -> dict[str, float]:
        """Write a balance snapshot.

        mode='set'  → absolute values (existing behaviour).
        mode='add'  → adds to the current running balance (cash income, salary, etc.).
        """
        cash_f = float(cash)
        online_f = float(online)
        if mode == "add":
            current = self.current() or {"cash_balance": 0.0, "online_balance": 0.0}
            cash_f = current["cash_balance"] + cash_f
            online_f = current["online_balance"] + online_f
        self.ledger.append(
            "balances",
            {
                "asof": now_utc().isoformat(),
                "cash_balance": cash_f,
                "online_balance": online_f,
                "reason": reason,
            },
        )
        return {"cash_balance": cash_f, "online_balance": online_f}

    def snapshot_after_expense(
        self,
        payment_method: str,
        amount: float,
        paid_for_method: str | None = None,
        adjustment_type: str | None = None,  # noqa: ARG002 — accepted for signature symmetry; 'adjusted' bypasses this path
    ) -> tuple[float, float]:
        """Apply an expense to the running balance, append snapshot, return (cash, online).

        Rules (§5.3):
        - paid       → online -= amount
        - paid_cash  → cash   -= amount
        - paid_by    → no change (someone else paid)
        - paid_for   → cash if paid_for_method=='cash' else online (default online, warn)
        - adjusted   → NOT handled here; callers must use BalanceService.adjust() instead
        """
        if payment_method == "adjusted":
            raise ValueError(
                "'adjusted' must go through BalanceService.adjust(); it does not write an expense row"
            )
        current = self.current() or {"cash_balance": 0.0, "online_balance": 0.0}
        cash = current["cash_balance"]
        online = current["online_balance"]

        if payment_method == "paid":
            online -= amount
        elif payment_method == "paid_cash":
            cash -= amount
        elif payment_method == "paid_by":
            pass  # someone else paid; no balance change
        elif payment_method == "paid_for":
            if paid_for_method == "cash":
                cash -= amount
            else:
                if paid_for_method is None:
                    logger.warning(
                        "paid_for without paid_for_method; defaulting to online decrement"
                    )
                online -= amount
        else:
            # Legacy/unknown — fall back to online decrement (preserves prior behaviour).
            logger.warning("unknown payment_method %r; decrementing online", payment_method)
            online -= amount

        self.ledger.append(
            "balances",
            {
                "asof": now_utc().isoformat(),
                "cash_balance": cash,
                "online_balance": online,
                "reason": "expense",
            },
        )
        return cash, online

    def atm_transfer(self, amount: float) -> dict[str, float]:
        """Move `amount` from online to cash. Appends a balances row."""
        current = self.current() or {"cash_balance": 0.0, "online_balance": 0.0}
        cash = current["cash_balance"] + amount
        online = current["online_balance"] - amount
        self.ledger.append(
            "balances",
            {
                "asof": now_utc().isoformat(),
                "cash_balance": cash,
                "online_balance": online,
                "reason": "atm_withdraw",
            },
        )
        return {"cash_balance": cash, "online_balance": online}

    def adjust(self, amount: float, direction: str) -> dict[str, float]:
        """Transfer between cash/online or other manual adjustment.

        direction: ``cash_to_online`` → cash -= amount, online += amount
                   ``online_to_cash`` → cash += amount, online -= amount

        Appends a row to balances.csv with reason='adjusted'. Does NOT write an
        expense row.
        """
        if direction not in {"cash_to_online", "online_to_cash"}:
            raise ValueError(f"invalid adjustment direction: {direction!r}")
        if amount <= 0:
            raise ValueError("adjustment amount must be positive")
        current = self.current() or {"cash_balance": 0.0, "online_balance": 0.0}
        cash = current["cash_balance"]
        online = current["online_balance"]
        if direction == "cash_to_online":
            cash -= amount
            online += amount
        else:
            cash += amount
            online -= amount
        self.ledger.append(
            "balances",
            {
                "asof": now_utc().isoformat(),
                "cash_balance": cash,
                "online_balance": online,
                "reason": "adjusted",
            },
        )
        return {"cash_balance": cash, "online_balance": online}
