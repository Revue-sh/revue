"""Password hashing and session management."""
from __future__ import annotations

import hashlib
import os
import secrets
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
SESSION_COOKIE = "revue_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

_serializer: URLSafeTimedSerializer | None = None


def _get_serializer() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(SECRET_KEY)
    return _serializer


def reset_serializer() -> None:
    """Reset the cached serializer (for tests that change SECRET_KEY)."""
    global _serializer
    _serializer = None


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
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def get_session(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    s = _get_serializer()
    try:
        data = s.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except BadSignature:
        return None


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
