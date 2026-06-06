"""Password hashing and session management."""
from __future__ import annotations

import hashlib
import os
import secrets
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")

# Base (insecure / dev) cookie name. Over HTTPS this is upgraded to the
# ``__Host-`` prefixed form (see ``session_cookie_name``).
SESSION_COOKIE_BASE = "revue_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


# ---------------------------------------------------------------------------
# Shared cookie-hardening helpers (used by both auth.py and csrf.py)
# ---------------------------------------------------------------------------
# Whether cookies are served over HTTPS. When on, both the session and CSRF
# cookies adopt the ``Secure`` flag + the ``__Host-`` name prefix. The decision
# is read PER CALL (not cached at import) so tests can monkeypatch the env, and
# so set/read always agree within a single process run. We deliberately do NOT
# sniff the per-request scheme for the NAME — the name must be stable so the
# read side can find what the set side wrote.
def cookie_secure() -> bool:
    """Return True when cookies should be Secure + ``__Host-`` prefixed.

    Gated on the explicit ``COOKIE_SECURE`` env flag. Defaults OFF so local dev
    and the http test transport keep the plain, non-Secure cookies (a Secure
    cookie is never sent back over plain HTTP). Production (HTTPS) sets
    ``COOKIE_SECURE=1`` in its deploy config.
    """
    return os.environ.get("COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}


def host_prefixed(base: str) -> str:
    """Map a base cookie name to its hardened name given the current mode.

    Secure mode → ``__Host-<base>`` (browsers ONLY accept this name when the
    cookie is Secure, Path=/, and has no Domain — enforced by the set helpers).
    Insecure mode → the base name unchanged.
    """
    return f"__Host-{base}" if cookie_secure() else base


def session_cookie_name() -> str:
    return host_prefixed(SESSION_COOKIE_BASE)


# ---------------------------------------------------------------------------
# Shared lazy-serializer cache (used by both auth.py and csrf.py)
# ---------------------------------------------------------------------------
# The session and CSRF serializers are DIFFERENT (auth = timed, unsalted; csrf =
# untimed, salted for token-type namespacing) — so they are two distinct
# instances, NOT one shared serializer. What is shared is only the lazy
# build-once / reset pattern. ``make_serializer_cache`` de-duplicates that
# pattern: each module supplies its own ``build`` callable and gets back a
# ``(get, reset)`` pair. Lazy construction matters because tests rotate
# ``SECRET_KEY`` and call ``reset`` to force a rebind.
def make_serializer_cache(build):
    """Return ``(get, reset)`` for a lazily-built, resettable serializer.

    ``build`` is a zero-arg callable that constructs the serializer; it is
    invoked at most once per ``reset`` cycle.
    """
    cache: dict = {"instance": None}

    def get():
        if cache["instance"] is None:
            cache["instance"] = build()
        return cache["instance"]

    def reset() -> None:
        cache["instance"] = None

    return get, reset


_get_serializer, reset_serializer = make_serializer_cache(
    lambda: URLSafeTimedSerializer(SECRET_KEY)
)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${h.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, h_hex = password_hash.split("$", 1)
    except ValueError:
        return False
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return secrets.compare_digest(h.hex(), h_hex)


def create_session(response: Response, user_id: int, email: str, tier: str) -> None:
    s = _get_serializer()
    token = s.dumps({"user_id": user_id, "email": email, "tier": tier})
    secure = cookie_secure()
    # ``__Host-`` requires Secure + Path=/ + NO Domain; we always pass path="/"
    # and never set a Domain, so the name resolves consistently across modes.
    response.set_cookie(
        session_cookie_name(),
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def get_session(request: Request) -> Optional[dict]:
    token = request.cookies.get(session_cookie_name())
    if not token:
        return None
    s = _get_serializer()
    try:
        data = s.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except BadSignature:
        return None


def clear_session(response: Response) -> None:
    # The browser only clears a cookie when name + path (+ Secure/SameSite)
    # match the set cookie. Route the delete through the SAME resolver so a
    # ``__Host-`` session is actually cleared on logout in secure mode.
    response.delete_cookie(
        session_cookie_name(),
        path="/",
        secure=cookie_secure(),
        samesite="lax",
    )
