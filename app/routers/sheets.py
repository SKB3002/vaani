"""Google Sheets backup endpoints (M6).

Safety: every endpoint is a no-op / 503 when GOOGLE_SHEETS_ENABLED is false
or credentials are missing. Local writes are never blocked.

UI-driven setup (2026-04-23):
- POST /api/sheets/credentials    — upload service-account JSON
- DELETE /api/sheets/credentials  — remove the uploaded key
- PATCH  /api/sheets/config       — update spreadsheet id / url / enabled
- GET    /api/sheets/status       — now includes credentials_uploaded + client_email
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.deps import get_ledger
from app.services.ledger import LedgerWriter
from app.services.sheets.client import SheetsClient, SheetsClientError
from app.services.sheets.integrations_store import (
    load_sheets_config,
    update_sheets_config,
)
from app.services.sheets.sync_worker import (
    SYNCABLE_TABLES,
    SyncQueue,
    build_tab_headers,
)
from app.storage.schemas import SCHEMAS

router = APIRouter(prefix="/api/sheets", tags=["sheets"])
logger = logging.getLogger("vaani.sheets.router")

# Module-level singletons. Populated by lifecycle.install / set via tests.
_sync_queue: SyncQueue | None = None
_sheets_client: SheetsClient | None = None

MAX_CRED_FILE_BYTES = 50 * 1024  # 50 KB
REQUIRED_CRED_FIELDS = ("type", "client_email", "private_key", "project_id")
SPREADSHEET_URL_RE = re.compile(r"/d/([a-zA-Z0-9-_]+)")
SPREADSHEET_ID_RE = re.compile(r"^[a-zA-Z0-9-_]+$")


def set_sync_queue(queue: SyncQueue | None) -> None:
    global _sync_queue
    _sync_queue = queue


def set_client(client: SheetsClient | None) -> None:
    global _sheets_client
    _sheets_client = client


def get_sync_queue() -> SyncQueue | None:
    return _sync_queue


def get_sheets_client() -> SheetsClient | None:
    return _sheets_client


# ---------- status ----------
@router.get("/status")
def status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    queue = _sync_queue
    client = _sheets_client
    config = load_sheets_config(settings.resolved_data_dir(), settings)

    connected = False
    if config.enabled and config.is_complete and client is not None:
        try:
            client.spreadsheet_title()  # lazy init
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


# ---------- credentials upload ----------
@router.post("/credentials")
async def upload_credentials(
    request: Request,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    filename = file.filename or "service_account.json"
    if not filename.lower().endswith(".json"):
        raise HTTPException(400, "only .json files are accepted")

    payload = await file.read(MAX_CRED_FILE_BYTES + 1)
    if len(payload) > MAX_CRED_FILE_BYTES:
        raise HTTPException(400, f"file too large (max {MAX_CRED_FILE_BYTES} bytes)")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "service account file must be a JSON object")

    missing = [f for f in REQUIRED_CRED_FIELDS if not data.get(f)]
    if missing:
        raise HTTPException(
            400,
            f"missing required field(s): {', '.join(missing)}",
        )
    if data.get("type") != "service_account":
        raise HTTPException(
            400,
            "file is not a service account key (expected type=service_account)",
        )

    client_email = str(data["client_email"])

    data_dir = settings.resolved_data_dir()
    secrets_dir = data_dir / ".secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(secrets_dir, stat.S_IRWXU)  # 0700
        except OSError:
            logger.debug("chmod 0700 on %s failed", secrets_dir, exc_info=True)

    dest = secrets_dir / "service_account.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_bytes(payload)
    if os.name != "nt":
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            logger.debug("chmod 0600 on %s failed", tmp, exc_info=True)
    tmp.replace(dest)

    update_sheets_config(
        data_dir,
        credentials_path=str(dest),
        client_email=client_email,
    )
    # Do NOT auto-enable sync — user must toggle explicitly.
    return {
        "ok": True,
        "client_email": client_email,
        "hint": "Share your spreadsheet with this email as Editor",
    }


@router.delete("/credentials")
async def delete_credentials(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    data_dir = settings.resolved_data_dir()
    config = load_sheets_config(data_dir, settings)
    if config.credentials_path:
        try:
            Path(config.credentials_path).unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "failed to remove credentials at %s", config.credentials_path,
                exc_info=True,
            )
    update_sheets_config(
        data_dir,
        credentials_path=None,
        client_email=None,
        enabled=False,
    )
    # Hot-reload to drop any running worker now that creds are gone.
    from app.services.sheets import lifecycle as sheets_lifecycle
    await sheets_lifecycle.reload(request.app)
    return {"ok": True}


# ---------- config patch ----------
class ConfigPatch(BaseModel):
    spreadsheet_id: str | None = None
    spreadsheet_url: str | None = None
    enabled: bool | None = None


def _extract_spreadsheet_id(value: str) -> str:
    """Accept a full Sheets URL or bare ID. Reject garbage."""
    value = value.strip()
    if not value:
        raise ValueError("empty spreadsheet id/url")
    match = SPREADSHEET_URL_RE.search(value)
    if match:
        return match.group(1)
    if "/" in value or " " in value:
        raise ValueError("could not extract spreadsheet id from input")
    if not SPREADSHEET_ID_RE.match(value):
        raise ValueError("spreadsheet id contains invalid characters")
    return value


@router.patch("/config")
async def patch_config(
    payload: ConfigPatch,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    data_dir = settings.resolved_data_dir()
    current = load_sheets_config(data_dir, settings)

    updates: dict[str, Any] = {}
    new_spreadsheet_id = current.spreadsheet_id

    if payload.spreadsheet_url is not None or payload.spreadsheet_id is not None:
        raw = (
            payload.spreadsheet_url
            if payload.spreadsheet_url is not None
            else payload.spreadsheet_id
        )
        if raw is None or raw == "":
            updates["spreadsheet_id"] = None
            new_spreadsheet_id = ""
        else:
            try:
                new_spreadsheet_id = _extract_spreadsheet_id(raw)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            updates["spreadsheet_id"] = new_spreadsheet_id

    new_enabled = current.enabled
    if payload.enabled is not None:
        new_enabled = payload.enabled
        updates["enabled"] = payload.enabled

    if updates:
        update_sheets_config(data_dir, **updates)

    should_reload = (
        new_spreadsheet_id != current.spreadsheet_id
        or new_enabled != current.enabled
    )
    reload_performed = False
    if should_reload:
        from app.services.sheets import lifecycle as sheets_lifecycle
        await sheets_lifecycle.reload(request.app)
        reload_performed = True

    refreshed = load_sheets_config(data_dir, settings)
    # Probe connection cheaply so UI doesn't have to poll.
    connected = False
    client = _sheets_client
    if refreshed.enabled and refreshed.is_complete and client is not None:
        try:
            client.spreadsheet_title()
            connected = True
        except Exception:  # noqa: BLE001
            connected = False

    return {
        "enabled": refreshed.enabled,
        "spreadsheet_id": refreshed.spreadsheet_id or None,
        "client_email": refreshed.client_email or None,
        "connected": connected,
        "reload_performed": reload_performed,
    }


# ---------- test connection ----------
@router.post("/test-connection")
def test_connection(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    config = load_sheets_config(settings.resolved_data_dir(), settings)
    if not config.enabled:
        raise HTTPException(400, "GOOGLE_SHEETS_ENABLED is false")
    client = _sheets_client
    if client is None:
        raise HTTPException(400, "sheets client not configured")
    try:
        title = client.spreadsheet_title()
        tabs = client.list_tabs()
    except SheetsClientError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "spreadsheet_title": title, "tabs_present": tabs}


# ---------- full bootstrap sync ----------
@router.post("/sync-all")
def sync_all(
    settings: Settings = Depends(get_settings),
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    config = load_sheets_config(settings.resolved_data_dir(), settings)
    if not config.enabled:
        raise HTTPException(400, "GOOGLE_SHEETS_ENABLED is false")
    client = _sheets_client
    if client is None:
        raise HTTPException(400, "sheets client not configured")

    data_dir = settings.resolved_data_dir()
    summary: dict[str, Any] = {"tabs": {}, "errors": []}
    for table in sorted(SYNCABLE_TABLES):
        headers = build_tab_headers(data_dir, table)
        try:
            client.ensure_tab(table, headers)
            df = ledger.read(table)
            rows = df.to_dict(orient="records")
            pk = SCHEMAS[table]["pk"]
            written = client.batch_upsert(table, rows, key_column=pk)
            summary["tabs"][table] = {"written": written, "total": len(rows)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("sync-all failed for %s", table)
            summary["errors"].append({"table": table, "error": str(exc)})
    return summary


# ---------- drain ----------
@router.post("/drain-queue")
async def drain_queue() -> dict[str, Any]:
    queue = _sync_queue
    if queue is None:
        raise HTTPException(400, "sync queue not running")
    result = await queue.drain()
    return result


# ---------- reconciler ----------
@router.post("/reconcile")
def reconcile(
    ledger: LedgerWriter = Depends(get_ledger),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    config = load_sheets_config(settings.resolved_data_dir(), settings)
    if not config.enabled:
        raise HTTPException(400, "GOOGLE_SHEETS_ENABLED is false")
    client = _sheets_client
    if client is None:
        raise HTTPException(400, "sheets client not configured")

    unknowns: dict[str, list[dict[str, Any]]] = {}
    for table in sorted(SYNCABLE_TABLES):
        pk = SCHEMAS[table]["pk"]
        try:
            local_df = ledger.read(table)
            local_keys = set(local_df[pk].astype("string").dropna().tolist())
            remote = client.read_all(table)
        except SheetsClientError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile skipped %s: %s", table, exc)
            continue
        unknown_rows = [r for r in remote if str(r.get(pk, "")) not in local_keys]
        if unknown_rows:
            unknowns[table] = unknown_rows
    return {"unknowns": unknowns, "tabs_checked": sorted(SYNCABLE_TABLES)}


@router.post("/reconcile/import")
def reconcile_import(
    tab: str = Query(...),
    ledger: LedgerWriter = Depends(get_ledger),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    config = load_sheets_config(settings.resolved_data_dir(), settings)
    if not config.enabled:
        raise HTTPException(400, "GOOGLE_SHEETS_ENABLED is false")
    if tab not in SYNCABLE_TABLES:
        raise HTTPException(400, f"tab '{tab}' not syncable")
    client = _sheets_client
    if client is None:
        raise HTTPException(400, "sheets client not configured")

    pk = SCHEMAS[tab]["pk"]
    local_df = ledger.read(tab)
    local_keys = set(local_df[pk].astype("string").dropna().tolist())
    remote = client.read_all(tab)
    imported = 0
    for row in remote:
        if str(row.get(pk, "")) in local_keys or not row.get(pk):
            continue
        ledger.append(tab, dict(row))
        imported += 1
    return {"imported": imported, "tab": tab}
