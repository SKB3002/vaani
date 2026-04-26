"""Reports: daily + monthly totals over ledger fixtures."""
from __future__ import annotations

from pathlib import Path

import ulid
from fastapi.testclient import TestClient

from app.main import create_app


def _row(day: str, amount: float, type_cat: str) -> dict:
    return {
        "id": str(ulid.new()),
        "date": day,
        "created_at": f"{day}T10:00:00+00:00",
        "expense_name": "x",
        "type_category": type_cat,
        "payment_method": "paid",
        "paid_for_someone": False,
        "paid_by_someone": False,
        "person_name": None,
        "amount": amount,
        "cash_balance_after": 0.0,
        "online_balance_after": 0.0,
        "source": "manual",
        "raw_transcript": None,
        "notes": None,
        "import_batch_id": None,
    }


def test_daily_and_monthly_totals(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    from app.deps import get_ledger

    ledger = get_ledger()
    ledger.append("expenses", _row("2026-04-23", 100.0, "Need, Food & Drinks"))
    ledger.append("expenses", _row("2026-04-23", 250.0, "Want, Enjoyment"))
    ledger.append("expenses", _row("2026-04-22", 900.0, "Need, Travel"))

    r = client.get("/api/reports/totals", params={"scope": "daily", "day": "2026-04-23"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 350.0
    assert body["count"] == 2
    assert body["by_type"] == {"Need": 100.0, "Want": 250.0}

    r2 = client.get("/api/reports/totals", params={"scope": "monthly", "month": "2026-04"})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["total"] == 1250.0
    assert body2["count"] == 3
