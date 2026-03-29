"""Dashboard and onboarding routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import get_session
from database import get_db
from models import get_license_for_user, get_recent_reviews, get_all_runs_for_user, get_analytics
from config import templates

router = APIRouter()

TIER_LABELS: dict[str, str] = {
    "free": "Free",
    "indie": "Indie",
    "pro": "Pro",
    "enterprise_starter": "Enterprise Starter",
    "enterprise_growth": "Enterprise Growth",
    "enterprise_plus": "Enterprise Plus",
}

TIER_PRICES: dict[str, str] = {
    "free": "$0/mo",
    "indie": "$9/mo",
    "pro": "$29/mo",
    "enterprise_starter": "$59/mo",
    "enterprise_growth": "$149/mo",
    "enterprise_plus": "Custom",
}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    session = get_session(request)
    if session:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "landing.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    user_id = session["user_id"]
    with get_db() as conn:
        license_key = get_license_for_user(conn, user_id)
        reviews = get_recent_reviews(conn, user_id)

    tier = session.get("tier", "free")
    return templates.TemplateResponse(request, "dashboard.html", {
        "session": session,
        "license_key": license_key,
        "reviews": reviews,
        "tier_label": TIER_LABELS.get(tier, tier),
        "tier_price": TIER_PRICES.get(tier, ""),
    })


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    user_id = session["user_id"]
    with get_db() as conn:
        license_key = get_license_for_user(conn, user_id)

    return templates.TemplateResponse(request, "onboarding.html", {
        "session": session,
        "license_key": license_key,
    })


@router.get("/runs", response_class=HTMLResponse)
async def run_history(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    user_id = session["user_id"]
    page = int(request.query_params.get("page", 1))
    repo_filter = request.query_params.get("repo", "")
    status_filter = request.query_params.get("status", "")
    limit = 25
    offset = (page - 1) * limit

    with get_db() as conn:
        runs, total = get_all_runs_for_user(
            conn,
            user_id=user_id,
            limit=limit,
            offset=offset,
            repo_id=repo_filter or None,
            status=status_filter or None,
        )

    total_pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse(request, "runs.html", {
        "session": session,
        "runs": runs,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "repo_filter": repo_filter,
        "status_filter": status_filter,
    })


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    days = int(request.query_params.get("days", 30))
    days = max(7, min(days, 365))  # clamp 7–365

    user_id = session["user_id"]
    with get_db() as conn:
        data = get_analytics(conn, user_id, days=days)

    return templates.TemplateResponse(request, "analytics.html", {
        "session": session,
        "analytics": data,
        "days": days,
    })


@router.get("/partials/usage_bar", response_class=HTMLResponse)
async def usage_bar_partial(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return HTMLResponse("")

    user_id = session["user_id"]
    with get_db() as conn:
        license_key = get_license_for_user(conn, user_id)

    return templates.TemplateResponse(request, "partials/usage_bar.html", {
        "license_key": license_key,
    })


@router.get("/partials/license_card", response_class=HTMLResponse)
async def license_card_partial(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return HTMLResponse("")

    user_id = session["user_id"]
    with get_db() as conn:
        license_key = get_license_for_user(conn, user_id)

    return templates.TemplateResponse(request, "partials/license_card.html", {
        "license_key": license_key,
    })
