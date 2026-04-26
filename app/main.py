"""FastAPI application factory + lifespan."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.bootstrap import bootstrap
from app.config import get_settings
from app.deps import get_budget_runner, get_ledger
from app.middleware.auth import PasswordGateMiddleware, make_login_router
from app.storage.supabase_store import supabase_observer
from app.routers import (
    balances,
    budgets,
    charts,
    demo_data,
    expenses,
    export,
    goals,
    health,
    home,
    imports,
    investments,
    pages,
    reports,
    settings,
    sheets,
    tables,
    voice,
    wishlist,
)
from app.services.sheets import lifecycle as sheets_lifecycle

logger = logging.getLogger("fineye")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = get_settings()
    logging.basicConfig(level=cfg.log_level)
    logger.info("FinEye starting up")
    bootstrap()
    ledger = get_ledger()
    replayed = ledger.replay()
    if replayed:
        logger.info("WAL replay applied %d pending entries", replayed)
    try:
        summary = get_budget_runner().recompute_all()
        logger.info(
            "Budget recompute: %d months, %d warnings",
            summary.months_computed,
            len(summary.warnings),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Budget recompute at startup failed: %s", e)

    # Auto-recompute Table C whenever an expense is written / updated / deleted.
    # Recompute is a cheap pure-function pass over pandas — safe for single-user.
    def _recompute_on_expense_change(event: dict[str, object]) -> None:
        if event.get("table") != "expenses":
            return
        try:
            get_budget_runner().recompute_all()
        except Exception:  # pragma: no cover - observer must never raise
            logger.exception("Budget auto-recompute on expense change failed")

    ledger.on_change(_recompute_on_expense_change)

    # Supabase dual-write observer — best-effort, never blocks CSV path
    if cfg.supabase_configured:
        ledger.on_change(supabase_observer)
        logger.info("Supabase dual-write observer registered")

    # Google Sheets backup (M6) — strictly opt-in; failures never block the app.
    try:
        await sheets_lifecycle.install(app)
    except Exception:  # noqa: BLE001 - Sheets failure must never block app
        logger.exception("Google Sheets init failed; continuing without backup")

    try:
        yield
    finally:
        await sheets_lifecycle.teardown(app)
        ledger.clear_observers()
        logger.info("FinEye shutting down")


def create_app() -> FastAPI:
    cfg = get_settings()
    app = FastAPI(
        title="FinEye API",
        version="0.1.0",
        description="Personal finance tracker — voice, pandas/CSV ledger, INR-first.",
        lifespan=lifespan,
    )

    # Password gate — only active when APP_PASSWORD is set (Vercel deployment)
    if cfg.APP_PASSWORD:
        app.add_middleware(PasswordGateMiddleware, password=cfg.APP_PASSWORD)
        app.include_router(make_login_router())

    app.include_router(health.router)
    app.include_router(home.router)
    app.include_router(expenses.router)
    app.include_router(balances.router)
    app.include_router(reports.router)
    app.include_router(settings.router)
    app.include_router(imports.router)
    app.include_router(investments.router)
    app.include_router(wishlist.router)
    app.include_router(tables.router)
    app.include_router(voice.router)
    app.include_router(sheets.router)
    app.include_router(budgets.router)
    app.include_router(goals.router)
    app.include_router(charts.router)
    app.include_router(demo_data.router)
    app.include_router(export.router)
    app.include_router(pages.router)

    static_dir = Path("static")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()
