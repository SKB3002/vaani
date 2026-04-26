"""Lifecycle: install / reload / teardown idempotence + observer de-dup."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from app.services.sheets import lifecycle
from app.services.sheets.integrations_store import (
    SheetsIntegrationConfig,
    update_sheets_config,
)


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_build_returns_none_when_disabled(tmp_workspace: Path) -> None:
    cfg = SheetsIntegrationConfig(
        enabled=False,
        spreadsheet_id="X",
        credentials_path=str(tmp_workspace / "does-not-exist.json"),
        client_email="",
    )
    client, queue = lifecycle.build_client_and_queue(cfg)
    assert client is None
    assert queue is None


def test_build_returns_none_when_credentials_missing(tmp_workspace: Path) -> None:
    cfg = SheetsIntegrationConfig(
        enabled=True,
        spreadsheet_id="X",
        credentials_path=str(tmp_workspace / "nope.json"),
        client_email="",
    )
    client, queue = lifecycle.build_client_and_queue(cfg)
    assert client is None
    assert queue is None


def test_install_is_idempotent_and_noop_when_disabled(tmp_workspace: Path) -> None:
    app = FastAPI()
    result1 = _run(lifecycle.install(app))
    result2 = _run(lifecycle.install(app))
    assert result1["enabled"] is False
    assert result2["enabled"] is False
    assert app.state.sheets["queue"] is None
    assert app.state.sheets["observer"] is None


def test_reload_deregisters_previous_observer(tmp_workspace: Path) -> None:
    """When install + reload are called, only one observer remains registered."""
    from app.deps import get_ledger

    app = FastAPI()
    data_dir = tmp_workspace / "data"

    # Seed a real credentials file so lifecycle.build returns a client/queue.
    creds = data_dir / ".secrets" / "service_account.json"
    creds.parent.mkdir(parents=True)
    creds.write_text(
        '{"type":"service_account","client_email":"a@b.iam","private_key":"K","project_id":"p"}',
        encoding="utf-8",
    )
    update_sheets_config(
        data_dir,
        enabled=True,
        spreadsheet_id="XID",
        credentials_path=str(creds),
        client_email="a@b.iam",
    )

    ledger = get_ledger()
    ledger.clear_observers()

    _run(lifecycle.install(app))
    assert len(ledger._observers) == 1  # noqa: SLF001

    _run(lifecycle.reload(app))
    assert len(ledger._observers) == 1  # still exactly one after reload

    _run(lifecycle.teardown(app))
    assert len(ledger._observers) == 0
    ledger.clear_observers()


def test_teardown_is_idempotent(tmp_workspace: Path) -> None:
    app = FastAPI()
    _run(lifecycle.teardown(app))  # no prior install
    _run(lifecycle.teardown(app))
    assert app.state.sheets["queue"] is None


@pytest.mark.asyncio
async def test_status_reports_config(tmp_workspace: Path) -> None:
    app = FastAPI()
    data_dir = tmp_workspace / "data"
    update_sheets_config(data_dir, spreadsheet_id="ZZZ")
    result = lifecycle.status(app)
    assert result["spreadsheet_id"] == "ZZZ"
    assert result["credentials_uploaded"] is False
    assert result["enabled"] is False
