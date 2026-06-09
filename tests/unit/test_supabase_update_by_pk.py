"""Regression: _update_by_pk must honor None values (set NULL) and always
scope by user_id.

Bug: clearing custom_tag (PATCH {custom_tag: null}) built an empty SET list
because None values were filtered out, so the UPDATE returned no row and the
router surfaced a spurious 404 "expense not found". DB-free — _conn_ctx and the
cursor are mocked so we assert the generated SQL/params, not a live database.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

import app.context as ctx
from app.storage import supabase_store as ss


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, list[Any]]] = []
        self.description = [("id",), ("custom_tag",)]

    def execute(self, sql: str, params: list[Any]) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> tuple[Any, ...]:
        return ("EXP1", None)

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class _FakeConn:
    def __init__(self, cur: _FakeCursor) -> None:
        self._cur = cur

    def cursor(self) -> _FakeCursor:
        return self._cur

    def commit(self) -> None:
        return None


@pytest.fixture
def _capture(monkeypatch: pytest.MonkeyPatch) -> _FakeCursor:
    cur = _FakeCursor()

    @contextmanager
    def fake_ctx() -> Any:
        yield _FakeConn(cur)

    monkeypatch.setattr(ss, "_conn_ctx", fake_ctx)

    # Pretend supabase is configured (the property is read-only, so stub the
    # whole settings accessor the store uses).
    class _Cfg:
        supabase_configured = True

    monkeypatch.setattr(ss, "get_settings", lambda: _Cfg())
    ctx._current_user_id.set("user-123")
    return cur


def test_none_value_is_included_as_set_column(_capture: _FakeCursor) -> None:
    """Clearing a column to NULL must appear in the SET clause, not be dropped."""
    result = ss._update_by_pk("expenses", "id", "EXP1", {"custom_tag": None})
    assert result is not None  # row returned -> router won't 404
    sql, params = _capture.executed[-1]
    assert "custom_tag = %s" in sql
    # params: [value(None), pk, user_id]
    assert params[0] is None
    assert "EXP1" in params and "user-123" in params


def test_update_always_scopes_by_user_id(_capture: _FakeCursor) -> None:
    """Even single-PK tables (expenses) must filter by user_id."""
    ss._update_by_pk("expenses", "id", "EXP1", {"custom_tag": "Gym"})
    sql, params = _capture.executed[-1]
    assert "user_id = %s" in sql
    assert params[-1] == "user-123"


def test_empty_updates_returns_none(_capture: _FakeCursor) -> None:
    assert ss._update_by_pk("expenses", "id", "EXP1", {}) is None
