"""End-to-end import flow via TestClient."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app.main import create_app


def _csv_bytes() -> bytes:
    df = pd.DataFrame(
        {
            "Date": ["2026-04-20", "2026-04-21", "2026-04-22"],
            "Description": ["Zomato", "HPCL", "BESCOM"],
            "Type": ["Want", "Need", "Need"],
            "Category": ["Food & Drinks", "Travel", "Miscellaneous"],
            "Method": ["UPI", "cash", "UPI"],
            "Amt": ["₹450", "₹800", "₹2,300"],
        }
    )
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue()


def test_upload_map_commit_and_dedup(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    # 1. upload
    r = client.post(
        "/api/import/upload",
        files={"file": ("test.csv", _csv_bytes(), "text/csv")},
    )
    assert r.status_code == 200
    upload = r.json()
    upload_id = upload["upload_id"]
    assert upload["row_count"] == 3

    # 2. map + dry-run
    mapping = {
        "Date": "date",
        "Description": "expense_name",
        "Type": "type",
        "Category": "category",
        "Method": "payment_method",
        "Amt": "amount",
    }
    r = client.post(
        f"/api/import/{upload_id}/map",
        json={"target_table": "expenses", "mapping": mapping},
    )
    assert r.status_code == 200
    report = r.json()
    assert report["total_rows"] == 3
    assert report["valid_rows"] == 3

    # 3. commit
    r = client.post(f"/api/import/{upload_id}/commit", json={"on_invalid": "skip"})
    assert r.status_code == 200
    summary = r.json()
    batch_id = summary["batch_id"]
    assert summary["inserted"] == 3

    # 4. verify ledger
    r = client.get("/api/expenses")
    assert r.status_code == 200
    assert len(r.json()) == 3

    # 5. re-upload same file → dedup
    r = client.post(
        "/api/import/upload",
        files={"file": ("test.csv", _csv_bytes(), "text/csv")},
    )
    upload_id2 = r.json()["upload_id"]
    client.post(
        f"/api/import/{upload_id2}/map",
        json={"target_table": "expenses", "mapping": mapping},
    )
    r = client.post(f"/api/import/{upload_id2}/commit", json={"on_invalid": "skip"})
    summary2 = r.json()
    assert summary2["inserted"] == 0
    assert summary2["duplicates"] == 3

    # 6. rollback original batch
    r = client.delete(f"/api/import/{batch_id}")
    assert r.status_code == 200
    assert r.json()["removed"] == 3

    r = client.get("/api/expenses")
    assert r.json() == []


def test_suggest_mapping(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    r = client.post(
        "/api/import/upload",
        files={"file": ("test.csv", _csv_bytes(), "text/csv")},
    )
    upload_id = r.json()["upload_id"]

    r = client.get(f"/api/import/{upload_id}/suggest", params={"target_table": "expenses"})
    assert r.status_code == 200
    body = r.json()
    assert "suggestions" in body
    # Amt → amount should be suggested with high confidence
    assert body["suggestions"].get("Amt") == "amount"
