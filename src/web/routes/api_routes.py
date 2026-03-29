"""License validation and usage tracking API routes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from database import get_db
from auth import get_session
from models import get_license_by_key, increment_usage, reset_monthly_counter, create_review_run, get_all_runs_for_user, get_analytics

router = APIRouter()

ALL_AGENTS = [
    "orchestrator", "security-analyst", "performance-expert",
    "code-quality-expert", "architecture-reviewer", "consolidator", "sage",
]

AGENTS_BY_TIER: dict[str, list[str]] = {
    "free": ["orchestrator", "code-quality-expert", "consolidator"],
    "indie": ALL_AGENTS,
    "pro": ALL_AGENTS,
    "enterprise_starter": ALL_AGENTS,
    "enterprise_growth": ALL_AGENTS,
    "enterprise_plus": ALL_AGENTS,
}


class ValidateRequest(BaseModel):
    key: str
    repo_id: str = ""
    ci_run_id: str = ""


class TrackRequest(BaseModel):
    key: str
    repo_id: str = ""
    pr_title: str = ""
    pr_number: int = 0
    agents_used: list[str] = []
    findings_count: int = 0
    findings_by_severity: dict = {}
    duration_ms: int = 0


def _next_month_first(now: datetime) -> datetime:
    """Return midnight on the first day of the next month."""
    if now.month == 12:
        return datetime(now.year + 1, 1, 1)
    return datetime(now.year, now.month + 1, 1)


@router.post("/license/validate")
async def validate_license(body: ValidateRequest) -> JSONResponse:
    with get_db() as conn:
        lic = get_license_by_key(conn, body.key)

        if not lic or not lic.is_active:
            return JSONResponse(
                {"valid": False, "message": "Invalid license key"},
                status_code=401,
            )

        # Reset monthly counter if period_reset_at is in the past
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if lic.period_reset_at:
            reset_time = datetime.fromisoformat(lic.period_reset_at)
            if now >= reset_time:
                reset_monthly_counter(conn, lic.id)
                lic.reviews_used_this_month = 0
                # Set next reset to first day of next month
                next_reset = _next_month_first(now)
                conn.execute(
                    "UPDATE license_keys SET period_reset_at = ? WHERE id = ?",
                    (next_reset.isoformat(), lic.id),
                )

        # Check limit
        if lic.reviews_limit is not None and lic.reviews_used_this_month >= lic.reviews_limit:
            return JSONResponse({
                "valid": False,
                "message": "Review limit reached. Upgrade at https://revue.io/upgrade",
            })

        # Calculate reviews_left
        reviews_left = None
        if lic.reviews_limit is not None:
            reviews_left = max(0, lic.reviews_limit - lic.reviews_used_this_month)

        agents = AGENTS_BY_TIER.get(lic.tier, AGENTS_BY_TIER["free"])

        return JSONResponse({
            "valid": True,
            "tier": lic.tier,
            "agents_allowed": agents,
            "reviews_left": reviews_left,
            "expires_at": "",
        })


@router.post("/usage/track")
async def track_usage(body: TrackRequest) -> Response:
    with get_db() as conn:
        lic = get_license_by_key(conn, body.key)
        if not lic:
            return JSONResponse({"error": "Invalid key"}, status_code=404)

        increment_usage(conn, lic.id)
        create_review_run(
            conn,
            license_key_id=lic.id,
            repo_id=body.repo_id or None,
            pr_title=body.pr_title or None,
            pr_number=body.pr_number or None,
            agents_used=body.agents_used,
            findings_count=body.findings_count,
            findings_by_severity=body.findings_by_severity or None,
            duration_ms=body.duration_ms,
        )

    return Response(status_code=204)


@router.get("/runs")
async def list_runs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    repo_id: str = "",
    status: str = "",
) -> JSONResponse:
    """GET /api/runs — paginated run history for the authenticated user.

    Internal telemetry endpoint. Requires session auth (cookie).
    Returns JSON suitable for dashboards and metrics scripts.
    """
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "Unauthorised"}, status_code=401)

    user_id = session["user_id"]
    with get_db() as conn:
        runs, total = get_all_runs_for_user(
            conn,
            user_id=user_id,
            limit=min(limit, 200),  # cap at 200 per page
            offset=offset,
            repo_id=repo_id or None,
            status=status or None,
        )

    return JSONResponse({
        "total": total,
        "limit": limit,
        "offset": offset,
        "runs": [  # type: ignore[misc]
            {
                "id": r.id,
                "repo_id": r.repo_id,
                "pr_title": r.pr_title,
                "pr_number": r.pr_number,
                "ci_run_id": r.ci_run_id,
                "agents_used": r.agents_used,
                "findings_count": r.findings_count,
                "findings_by_severity": r.findings_by_severity,
                "duration_ms": r.duration_ms,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in runs
        ],
    })


@router.get("/analytics")
async def analytics_data(
    request: Request,
    days: int = 30,
) -> JSONResponse:
    """GET /api/analytics — aggregate finding trends for the authenticated user.

    Query params:
        days: lookback window in days (7–365, default 30)
    """
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "Unauthorised"}, status_code=401)

    days = max(7, min(days, 365))
    user_id = session["user_id"]
    with get_db() as conn:
        data = get_analytics(conn, user_id, days=days)
    return JSONResponse(data)
