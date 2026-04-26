"""Uniques teach endpoint: patch semantics + concurrent write safety."""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config as _cfg
from app import deps as _deps
from app.main import create_app


@pytest.fixture
def client(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("GROQ_API_KEY", "")
    _cfg.get_settings.cache_clear()
    _deps.get_ledger.cache_clear()
    _deps.get_balance_service.cache_clear()
    app = create_app()
    with TestClient(app) as tc:
        yield tc
    _cfg.get_settings.cache_clear()
    _deps.get_ledger.cache_clear()
    _deps.get_balance_service.cache_clear()


def test_patch_new_vendor(client: TestClient, tmp_workspace: Path) -> None:
    r = client.post(
        "/api/uniques/teach",
        json={"surface": "zomato", "type_category": "Want, Food & Drinks"},
    )
    assert r.status_code == 200
    data = json.loads((tmp_workspace / "data" / "uniques.json").read_text(encoding="utf-8"))
    assert data["vendors"]["zomato"]["category"] == "Food & Drinks"
    assert data["vendors"]["zomato"]["type"] == "Want"


def test_alias_only_when_vendor_differs(client: TestClient, tmp_workspace: Path) -> None:
    r = client.post(
        "/api/uniques/teach",
        json={"surface": "zomato", "vendor": "Zomato"},
    )
    assert r.status_code == 200
    data = r.json()
    # surface == canonical lowercased → no alias entry.
    assert "zomato" not in data["aliases"]


def test_concurrent_writes_do_not_corrupt(
    client: TestClient, tmp_workspace: Path
) -> None:
    path = tmp_workspace / "data" / "uniques.json"

    def teach(name: str) -> None:
        client.post(
            "/api/uniques/teach",
            json={"surface": name, "type_category": "Want, Miscellaneous"},
        )

    threads = [threading.Thread(target=teach, args=(f"vendor_{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = json.loads(path.read_text(encoding="utf-8"))
    for i in range(20):
        assert f"vendor_{i}" in data["vendors"]
