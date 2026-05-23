"""Email + password authentication for multi-user mode.

Why stdlib PBKDF2 instead of bcrypt/argon2?
-------------------------------------------
The deployment target is Vercel's serverless Python runtime, which is happiest
with pure-Python dependencies (no C extensions, no native wheels to ship per
arch). ``hashlib.pbkdf2_hmac`` is in the stdlib, OWASP-acceptable when tuned,
and avoids cold-start surprises. The iteration count below (600,000) follows
the 2023 OWASP guidance for PBKDF2-HMAC-SHA256; bump it later if hardware
catches up.

Hash format
-----------
Stored as a single string so the column stays simple:

    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

Verification re-runs the KDF with the parsed parameters, so we can change
``_ITERATIONS`` for new accounts without breaking older ones.

Sessions
--------
Signed cookie via ``itsdangerous.URLSafeTimedSerializer`` carrying the
user's UUID. The signing key is :attr:`Settings.SECRET_KEY`; cookies survive
restarts as long as the key is stable.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

log = logging.getLogger("vaani.auth")

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_BYTES = 32  # SHA-256 output

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8

_COOKIE_SALT = "vaani-session-v1"


class AuthError(Exception):
    """Base for all auth-layer failures surfaced to callers."""


class InvalidEmailError(AuthError):
    """Raised when an email address fails the syntactic format check."""


class WeakPasswordError(AuthError):
    """Raised when a chosen password is too short to be accepted."""


class EmailAlreadyRegisteredError(AuthError):
    """Raised when signup is attempted with an email that already exists."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """A successfully authenticated user, as returned to the request layer."""

    id: str
    email: str


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def normalize_email(email: str) -> str:
    """Lowercase + strip — the canonical form we store and look up by."""
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    """Return the normalised email, or raise :class:`InvalidEmailError`."""
    norm = normalize_email(email)
    if not _EMAIL_RE.match(norm):
        raise InvalidEmailError("Enter a valid email address.")
    return norm


def validate_password(password: str) -> str:
    """Return the password as-is, or raise :class:`WeakPasswordError`.

    The only rule is length — complexity requirements have been shown to push
    users toward predictable patterns. Length is what matters.
    """
    if not password or len(password) < _MIN_PASSWORD_LEN:
        raise WeakPasswordError(
            f"Password must be at least {_MIN_PASSWORD_LEN} characters."
        )
    return password


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256.

    Returns the encoded ``algo$iter$salt$hash`` string ready for storage.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS, dklen=_HASH_BYTES
    )
    return "$".join(
        [
            _ALGO,
            str(_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification against a stored ``algo$iter$salt$hash``."""
    if not encoded:
        return False
    try:
        algo, iter_str, salt_b64, hash_b64 = encoded.split("$", 3)
    except ValueError:
        return False
    if algo != _ALGO:
        return False
    try:
        iterations = int(iter_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# Session cookies
# ---------------------------------------------------------------------------


def _signer() -> URLSafeTimedSerializer:
    cfg = get_settings()
    secret = cfg.SECRET_KEY
    if not secret:
        # Fall back to a deterministic-but-weak derivation so local dev works.
        # ``app/main.py`` logs a warning at startup when MULTI_USER=true and
        # SECRET_KEY is unset — production deployments should fix that.
        seed = (cfg.DB_PASSWORD or cfg.APP_PASSWORD or "vaani-dev-fallback").encode()
        secret = hashlib.sha256(seed).hexdigest()
    return URLSafeTimedSerializer(secret, salt=_COOKIE_SALT)


def issue_session_token(user_id: str) -> str:
    """Sign a session token for ``user_id`` (no expiry baked into the token)."""
    return _signer().dumps({"uid": user_id})


def read_session_token(token: str, *, max_age_seconds: int) -> str | None:
    """Validate a session token and return its ``user_id``, or ``None`` if invalid."""
    if not token:
        return None
    try:
        payload = _signer().loads(token, max_age=max_age_seconds)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    uid = payload.get("uid")
    return str(uid) if uid else None


# ---------------------------------------------------------------------------
# Signup / login orchestration
#
# The actual user-row CRUD lives in ``app.storage.supabase_store`` so the
# functions here stay pure business logic and the SQL stays next to every
# other table in the storage module.
# ---------------------------------------------------------------------------


def signup(email: str, password: str, *, consented: bool) -> AuthenticatedUser:
    """Create a new account and return the authenticated user.

    ``consented`` must be ``True`` — the trial-tier hosted deployment cannot
    create accounts that haven't acknowledged the data notice. The router
    enforces this with a checkbox; we belt-and-braces it here.
    """
    if not consented:
        raise AuthError("Consent is required to create an account.")
    norm_email = validate_email(email)
    validate_password(password)

    from app.storage.supabase_store import create_user, get_user_by_email

    if get_user_by_email(norm_email) is not None:
        raise EmailAlreadyRegisteredError(
            "An account with that email already exists. Try signing in."
        )

    pw_hash = hash_password(password)
    user_id = create_user(email=norm_email, password_hash=pw_hash, consented=True)
    log.info("auth: signup ok email=%s", norm_email)
    return AuthenticatedUser(id=user_id, email=norm_email)


def login(email: str, password: str) -> AuthenticatedUser | None:
    """Return the authenticated user on success, or ``None`` on failure.

    Failure is intentionally indistinguishable between "unknown email" and
    "wrong password" — both surface the same generic message to the user.
    """
    norm_email = normalize_email(email)
    if not norm_email or not password:
        return None

    from app.storage.supabase_store import get_user_by_email

    row = get_user_by_email(norm_email)
    if row is None:
        # Run a dummy verify against a throwaway hash to keep timing similar
        # to the real-account path — defends against trivial email-enumeration
        # via response timing.
        verify_password(password, hash_password("dummy-for-timing"))
        return None
    if not verify_password(password, row.get("password_hash", "")):
        return None
    return AuthenticatedUser(id=str(row["id"]), email=str(row["email"]))


__all__ = [
    "AuthError",
    "AuthenticatedUser",
    "EmailAlreadyRegisteredError",
    "InvalidEmailError",
    "WeakPasswordError",
    "hash_password",
    "issue_session_token",
    "login",
    "normalize_email",
    "read_session_token",
    "signup",
    "validate_email",
    "validate_password",
    "verify_password",
]
