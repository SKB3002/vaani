"""Request-scoped current-user context.

Multi-user data isolation hinges on one idea: every read/write that hits
Supabase is scoped to a ``user_id``. Historically that id was a single global
value (``settings.OWNER_ID``). To support many users without rewriting every
router, we carry the active user's id in a :class:`contextvars.ContextVar` that
is set per request by the auth middleware and read by the storage layer.

``ContextVar`` values are isolated per asyncio task and copied into the worker
thread that Starlette uses for sync endpoints, so a value set in middleware is
visible to the request handler, its post-commit observers (budget recompute,
insights invalidation), and any nested service call within the same request.

In single-user / local CSV mode the middleware never runs, the var stays unset,
and :func:`current_user_id` falls back to ``settings.OWNER_ID`` — preserving the
existing behaviour exactly.
"""
from __future__ import annotations

import contextvars

_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vaani_current_user_id", default=None
)


def set_current_user(user_id: str | None) -> contextvars.Token[str | None]:
    """Bind the active user id for the current context. Returns a reset token."""
    return _current_user_id.set(user_id)


def reset_current_user(token: contextvars.Token[str | None]) -> None:
    """Restore the previous value bound before :func:`set_current_user`."""
    _current_user_id.reset(token)


def current_user_id() -> str:
    """Return the active user id, falling back to the single-user owner.

    The fallback to ``settings.OWNER_ID`` keeps local CSV mode and any code
    path that runs outside a request (startup recompute, migration scripts)
    working unchanged. Imported lazily so this module stays import-cheap.
    """
    uid = _current_user_id.get()
    if uid:
        return uid
    from app.config import get_settings

    return get_settings().OWNER_ID
