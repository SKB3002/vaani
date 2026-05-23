"""Unit tests for the multi-user auth layer.

These tests cover the pure pieces (password hashing, session tokens, the
contextvar) without touching Postgres. Higher-level signup/login that hits
``supabase_store.create_user`` is exercised live in the e2e suite.
"""
from __future__ import annotations

from app.context import (
    current_user_id,
    reset_current_user,
    set_current_user,
)
from app.services.auth import (
    hash_password,
    issue_session_token,
    read_session_token,
    validate_email,
    validate_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_hash_password_roundtrips() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_hash_password_uses_unique_salt() -> None:
    a = hash_password("hunter2hunter2")
    b = hash_password("hunter2hunter2")
    # Identical password, different salts → different stored strings.
    assert a != b
    assert verify_password("hunter2hunter2", a)
    assert verify_password("hunter2hunter2", b)


def test_verify_password_rejects_malformed_encoded() -> None:
    assert not verify_password("anything", "")
    assert not verify_password("anything", "not-a-real-hash")
    assert not verify_password("anything", "argon2$1$x$y")  # wrong algo


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_email_normalises_and_rejects_garbage() -> None:
    import pytest

    from app.services.auth import InvalidEmailError

    assert validate_email("  Reviewer@Example.COM ") == "reviewer@example.com"
    with pytest.raises(InvalidEmailError):
        validate_email("no-at-sign")
    with pytest.raises(InvalidEmailError):
        validate_email("two@@dots.com")


def test_validate_password_requires_min_length() -> None:
    import pytest

    from app.services.auth import WeakPasswordError

    validate_password("longenough")
    with pytest.raises(WeakPasswordError):
        validate_password("short")
    with pytest.raises(WeakPasswordError):
        validate_password("")


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def test_session_token_roundtrips() -> None:
    token = issue_session_token("user-123")
    assert read_session_token(token, max_age_seconds=3600) == "user-123"


def test_session_token_rejects_tampered_payload() -> None:
    token = issue_session_token("user-123")
    tampered = token[:-2] + ("AB" if not token.endswith("AB") else "CD")
    assert read_session_token(tampered, max_age_seconds=3600) is None


def test_session_token_rejects_empty() -> None:
    assert read_session_token("", max_age_seconds=3600) is None


# ---------------------------------------------------------------------------
# Contextvar
# ---------------------------------------------------------------------------


def test_current_user_id_falls_back_to_settings_owner() -> None:
    # No contextvar set → falls back to settings.OWNER_ID (may be empty in
    # the test env, but the call must not raise).
    val = current_user_id()
    assert isinstance(val, str)


def test_current_user_id_uses_contextvar_when_set() -> None:
    token = set_current_user("ctx-user")
    try:
        assert current_user_id() == "ctx-user"
    finally:
        reset_current_user(token)
    # And resets cleanly.
    assert current_user_id() != "ctx-user" or current_user_id() == ""
