"""REVUE-127/REVUE-364 — POST /usage/track and /funnel/event endpoints.

Key-based (not JWT) usage tracking for the CLI fire-and-forget path.
Mounted at the ROOT level (no /api prefix) to match the CLI's TRACK_URL.
"""
from __future__ import annotations

import hashlib
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from auth import get_session
from database import (
    REVIEWS_LIMIT_BY_TIER,
    VALID_FUNNEL_EVENT_TYPES,
    check_funnel_rate_limit,
    get_db,
    get_weekly_conversion,
    record_funnel_event,
)
from models import (
    create_review_run,
    get_license_by_key,
    has_recent_track_event,
    increment_usage,
)

_LOG = logging.getLogger(__name__)

router = APIRouter()

# REVUE-364: install_id pattern — UUID4 hex+hyphens, 4–64 chars.
_INSTALL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{4,64}$")


class UsageTrackRequest(BaseModel):
    key: str
    repo_id: str = ""
    agents_used: list[str] = []
    duration_ms: int = Field(default=0, ge=0)


@router.post("/usage/track")
async def track_usage(request: Request) -> Response:
    """Accept a usage event from the CLI and persist it.

    AC1: accepts key, repo_id, agents_used, duration_ms.
    AC2: validates key; 401 unknown, 403 inactive.
    AC3: persists event immutably to review_runs.
    AC4: decrements reviews_left for metered tiers (floor at read time).
    AC5: idempotent within 60 s for same key + repo_id.
    AC7: malformed payload → 400 with error body (never 422 / never blocks review).
    """
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "invalid_payload", "message": "Request body must be valid JSON."},
            status_code=400,
        )

    try:
        body = UsageTrackRequest.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_payload", "message": str(exc)},
            status_code=400,
        )

    repo_id = body.repo_id or None

    with get_db() as conn:
        # IMMEDIATE lock prevents TOCTOU between the dedup check and the insert.
        conn.execute("BEGIN IMMEDIATE")
        lic = get_license_by_key(conn, body.key)
        if lic is None:
            return JSONResponse({"error": "unknown_key"}, status_code=401)
        if not lic.is_active:
            return JSONResponse({"error": "key_inactive"}, status_code=403)

        if has_recent_track_event(conn, lic.id, repo_id):
            _LOG.info("usage/track duplicate suppressed key=%.8s… repo=%s", body.key, repo_id)
            return Response(status_code=204)

        create_review_run(
            conn,
            license_key_id=lic.id,
            repo_id=repo_id,
            agents_used=body.agents_used,
            duration_ms=body.duration_ms,
        )

        # Metered tiers carry a non-None reviews_limit; unlimited tiers (pro, enterprise)
        # use None. This is the canonical discriminator — do not check tier by name.
        if lic.reviews_limit is not None:
            increment_usage(conn, lic.id)

    return Response(status_code=204)


# ── REVUE-364: funnel telemetry ──────────────────────────────────────────────

class FunnelEventRequest(BaseModel):
    event_type: str = Field(max_length=32)
    install_id: str = Field(max_length=64)
    key: str = Field(default="", max_length=128)
    ts: int = 0


def _hash16(value: str) -> str:
    """Return SHA-256(value)[:16] — 8 bytes of entropy, sufficient for rate-limit
    correlation without storing the raw value (F1/F2)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@router.post("/funnel/event")
async def record_funnel_telemetry(request: Request) -> Response:
    """REVUE-364 AC1/AC3: accept install/activate/review funnel events.

    Unauthenticated — install events arrive before any credential exists.
    CSRF-exempt (see main.py CSRF_EXEMPT_PATHS).
    Rate-limited: per install_id (10/60s) + per IP (50/1h) — F2.
    Raw licence key and client IP are hashed before storage — F1.
    Billing paths (review_runs, usage_events) are completely unaffected.
    """
    try:
        raw = await request.json()
        body = FunnelEventRequest(**raw)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    if body.event_type not in VALID_FUNNEL_EVENT_TYPES:
        return JSONResponse(
            {"error": "invalid_event_type", "valid": sorted(VALID_FUNNEL_EVENT_TYPES)},
            status_code=400,
        )

    if not _INSTALL_ID_PATTERN.match(body.install_id):
        return JSONResponse({"error": "invalid_install_id"}, status_code=400)

    # F1: hash before storage — raw key never touches the DB regardless of
    # what the client sends. IP is also hashed here (server-supplied).
    license_key_hash = _hash16(body.key) if body.key else None
    client_ip = request.client.host if request.client else None
    ip_hash = _hash16(client_ip) if client_ip else None

    with get_db() as conn:
        # F5: IMMEDIATE lock prevents concurrent requests from both passing the
        # rate-limit check before either insert (same pattern as /usage/track).
        conn.execute("BEGIN IMMEDIATE")
        if not check_funnel_rate_limit(conn, body.install_id, ip_hash=ip_hash):  # F2
            return JSONResponse({"error": "rate_limited"}, status_code=429)

        record_funnel_event(
            conn,
            event_type=body.event_type,
            install_id=body.install_id,
            license_key_hash=license_key_hash,  # F1
            ip_hash=ip_hash,                     # F2
            ts=body.ts or None,
        )

    return Response(status_code=204)


@router.get("/funnel/weekly-conversion")
async def funnel_weekly_conversion(request: Request) -> JSONResponse:
    """REVUE-364 AC4: weekly install→activate→first-review conversion %.

    Requires session auth (operator — not exposed to end users).
    Returns up to 12 weeks, newest first.
    """
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "Unauthorised"}, status_code=401)

    with get_db() as conn:
        weeks = get_weekly_conversion(conn)

    return JSONResponse({"weeks": weeks})
