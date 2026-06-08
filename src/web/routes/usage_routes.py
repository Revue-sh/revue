"""REVUE-127 — POST /usage/track endpoint.

Key-based (not JWT) usage tracking for the CLI fire-and-forget path.
Mounted at the ROOT level (no /api prefix) to match the CLI's TRACK_URL.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from database import REVIEWS_LIMIT_BY_TIER, get_db
from models import (
    create_review_run,
    get_license_by_key,
    has_recent_track_event,
    increment_usage,
)

_LOG = logging.getLogger(__name__)

router = APIRouter()


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
