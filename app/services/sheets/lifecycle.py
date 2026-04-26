"""Sheets backup lifecycle: install / reload (hot) / teardown.

Keeps all Sheets-specific wiring out of `app.main` and makes the integration
reloadable at runtime when the UI flips config via `PATCH /api/sheets/config`.

State is stored on `app.state.sheets` as a dict:
    {
        "client": SheetsClient | None,
        "queue":  SyncQueue     | None,
        "observer": callable    | None,   # registered on the ledger
    }
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from app.config import get_settings
from app.deps import get_ledger
from app.routers import sheets as sheets_router
from app.services.ledger import ChangeEvent
from app.services.sheets.client import SheetsClient
from app.services.sheets.integrations_store import (
    SheetsIntegrationConfig,
    load_sheets_config,
)
from app.services.sheets.sync_worker import SyncQueue, register_sheets_observer

logger = logging.getLogger("vaani.sheets.lifecycle")


def _state(app: FastAPI) -> dict[str, Any]:
    current = getattr(app.state, "sheets", None)
    if current is None:
        current = {"client": None, "queue": None, "observer": None}
        app.state.sheets = current
    return current


def build_client_and_queue(
    config: SheetsIntegrationConfig,
) -> tuple[SheetsClient | None, SyncQueue | None]:
    """Instantiate client + queue from the resolved config.

    Returns `(None, None)` if the integration is disabled or incomplete.
    """
    if not config.is_complete:
        return None, None
    settings = get_settings()
    try:
        client = SheetsClient(
            credentials_path=config.credentials_path,
            spreadsheet_id=config.spreadsheet_id,
        )
        queue = SyncQueue(
            client=client,
            wal_dir=settings.resolved_wal_dir(),
            data_dir=settings.resolved_data_dir(),
            max_retries=settings.GOOGLE_SHEETS_MAX_RETRIES,
            backoff_base=settings.GOOGLE_SHEETS_BACKOFF_BASE,
        )
        return client, queue
    except Exception:  # noqa: BLE001 - Sheets init must never raise out
        logger.exception("SheetsClient / SyncQueue init failed")
        return None, None


async def install(app: FastAPI) -> dict[str, Any]:
    """Boot the Sheets integration using the current resolved config.

    Idempotent: if already installed, returns the current status unchanged.
    """
    state = _state(app)
    if state["queue"] is not None:
        return status(app)

    settings = get_settings()
    config = load_sheets_config(settings.resolved_data_dir(), settings)
    client, queue = build_client_and_queue(config)
    if queue is None or client is None:
        sheets_router.set_client(None)
        sheets_router.set_sync_queue(None)
        return status(app)

    await queue.start()
    ledger = get_ledger()
    observer = register_sheets_observer(ledger, queue)
    state["client"] = client
    state["queue"] = queue
    state["observer"] = observer
    sheets_router.set_client(client)
    sheets_router.set_sync_queue(queue)
    logger.info(
        "Sheets backup enabled (spreadsheet=%s)", config.spreadsheet_id
    )
    return status(app)


async def teardown(app: FastAPI) -> None:
    """Stop the worker + deregister the observer. Idempotent."""
    state = _state(app)
    queue: SyncQueue | None = state.get("queue")
    observer: Callable[[ChangeEvent], None] | None = state.get("observer")

    if observer is not None:
        try:
            get_ledger().off_change(observer)
        except Exception:  # noqa: BLE001
            logger.debug("ledger.off_change raised (safe to ignore)", exc_info=True)

    if queue is not None:
        try:
            await queue.stop()
        except Exception:  # noqa: BLE001
            logger.debug("queue.stop raised (safe to ignore)", exc_info=True)

    state["client"] = None
    state["queue"] = None
    state["observer"] = None
    sheets_router.set_client(None)
    sheets_router.set_sync_queue(None)


async def reload(app: FastAPI) -> dict[str, Any]:
    """Hot-reload: tear down + install with fresh config. No server restart."""
    await teardown(app)
    return await install(app)


def status(app: FastAPI) -> dict[str, Any]:
    """Snapshot of current lifecycle state for status endpoints."""
    state = _state(app)
    settings = get_settings()
    config = load_sheets_config(settings.resolved_data_dir(), settings)
    client: SheetsClient | None = state.get("client")
    queue: SyncQueue | None = state.get("queue")

    connected = False
    if client is not None:
        try:
            client.spreadsheet_title()
            connected = True
        except Exception:  # noqa: BLE001 - connectivity probe never raises
            connected = False

    return {
        "enabled": config.enabled,
        "connected": connected,
        "spreadsheet_id": config.spreadsheet_id or None,
        "credentials_uploaded": config.credentials_uploaded,
        "client_email": config.client_email or None,
        "queue_depth": queue.queue_depth() if queue else 0,
        "deadletter_count": queue.deadletter_count() if queue else 0,
        "last_sync_at": queue.last_sync_at() if queue else None,
        "last_error": queue.last_error() if queue else None,
    }


__all__ = ["build_client_and_queue", "install", "teardown", "reload", "status"]
