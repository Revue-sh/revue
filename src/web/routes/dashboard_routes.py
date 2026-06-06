"""Dashboard and onboarding routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import get_session, create_session
from database import get_db
from models import (
    LicenseKey,
    get_any_license_for_user,
    get_license_for_user,
    get_recent_reviews,
    get_all_runs_for_user,
    get_analytics,
    get_conversion_analytics,
    get_user_by_id,
)
from config import templates, CURRENCY_SYMBOL

router = APIRouter()

# ---------------------------------------------------------------------------
# Account → Plan helpers (REVUE-382)
# ---------------------------------------------------------------------------

PlanState = Literal["active", "lapsed", "free", "not_activated"]


def derive_plan_state(license_row: Optional[LicenseKey]) -> PlanState:
    """Map a licence row (or None) to one of four display states.

    State matrix (order matters — earlier rules win):
      1. None                                → not_activated (no key at all)
      2. is_active=False AND tier != "free"  → lapsed  (PAID subscription ended;
                                               tier preserved — checked BEFORE the
                                               never-validated rule so a once-active-
                                               then-lapsed paid key stays lapsed.
                                               A free+inactive row is NOT lapsed —
                                               it falls through to not_activated,
                                               since "Re-subscribe to Free" is
                                               nonsensical)
      3. last_validated_at is None           → not_activated (has a key but the CLI
                                               has never validated it — tier-agnostic;
                                               the user must run `revue activate <key>`)
      4. is_active=True, tier="free"         → free
      5. is_active=True, tier=paid           → active

    ``not_activated`` is keyed on ``last_validated_at`` (REVUE-382): the server-
    side validation-cache state stamped by /v2/licence/validate. This is what
    makes AC5 ("pre-filled with the user's key") satisfiable — a freshly signed-up
    user has a key but has never validated, so we surface the Activation Command-
    Box pre-filled with their real key.

    NULL current_period_end / subscription_status do NOT affect state — they only
    drive the renewal line. REVUE-413 NULL-column rows resolve cleanly.
    """
    if license_row is None:
        return "not_activated"
    if not license_row.is_active:
        # An inactive row: a PAID subscription that ended → lapsed (tier
        # preserved). A free + inactive row is NOT lapsed — "Re-subscribe to
        # Free" + a tier=free checkout form is nonsensical — so it falls to
        # not_activated. The split lives INSIDE the inactive branch so it is
        # unconditional: a previously-validated free key that is later revoked
        # must still resolve to not_activated, never escape to "free".
        return "lapsed" if license_row.tier != "free" else "not_activated"
    if license_row.last_validated_at is None:
        return "not_activated"
    if license_row.tier == "free":
        return "free"
    return "active"


def last_verified_ago(last_validated_at: Optional[str]) -> str:
    """Render the validation-cache age as 'Last verified Nh ago' (AC2).

    Sources the validation-cache state (``last_validated_at``), NOT the Stripe
    subscription record. The stored value is a naive-UTC isoformat string (see
    ``models.touch_license_validated``), so we diff against a naive-UTC ``now``.

    Returns a graceful fallback for NULL ("Not verified yet") and for an
    unparseable value — never a raw ``None`` and never "0h ago".
    """
    if not last_validated_at:
        return "Not verified yet"
    try:
        stamped = datetime.fromisoformat(last_validated_at)
    except (ValueError, TypeError):
        return "Not verified yet"
    # Compare naive-UTC to naive-UTC (the stored representation).
    if stamped.tzinfo is not None:
        stamped = stamped.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = now - stamped
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        # Clock skew / future timestamp — treat as just verified.
        return "Last verified just now"
    hours = int(total_seconds // 3600)
    if hours < 1:
        return "Last verified just now"
    if hours < 48:
        return f"Last verified {hours}h ago"
    days = hours // 24
    return f"Last verified {days}d ago"


def masked_key_display(key: str) -> str:
    """Return a masked representation that shows prefix + last 4 chars.

    e.g. ``lic_••••••••••••••••••••••••••••1234``.
    The full key is never in the visible text — only in the copy payload.
    """
    prefix = "lic_"
    if not key.startswith(prefix) or len(key) <= len(prefix) + 4:
        # Fallback for malformed / short keys — show dots only
        return prefix + "••••"
    tail = key[-4:]
    dot_count = len(key) - len(prefix) - 4
    return prefix + ("•" * dot_count) + tail

TIER_LABELS: dict[str, str] = {
    "free": "Free",
    "indie": "Indie",
    "pro": "Pro",
    "enterprise_starter": "Enterprise Starter",
    "enterprise_growth": "Enterprise Growth",
    "enterprise_plus": "Enterprise Plus",
}

# Monthly amounts (currency-agnostic); the symbol comes from the single
# CURRENCY_SYMBOL source so price display stays consistent site-wide.
_TIER_MONTHLY: dict[str, int] = {
    "free": 0,
    "indie": 9,
    "pro": 29,
    "enterprise_starter": 59,
    "enterprise_growth": 149,
}
TIER_PRICES: dict[str, str] = {t: f"{CURRENCY_SYMBOL}{amt}/mo" for t, amt in _TIER_MONTHLY.items()}
TIER_PRICES["enterprise_plus"] = "Custom"


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
        user = get_user_by_id(conn, user_id)
        license_key = get_license_for_user(conn, user_id)
        reviews = get_recent_reviews(conn, user_id)

    # Defect E: read tier from the DB, not the login session. A webhook upgrade
    # (checkout -> pro) updates the DB but not the already-issued session cookie,
    # so session["tier"] goes stale (badge shows FREE) until re-login. Sync both
    # the rendered tier and the cookie here.
    if user:
        session["tier"] = user.tier
    tier = session.get("tier", "free")

    response = templates.TemplateResponse(request, "dashboard.html", {
        "session": session,
        "license_key": license_key,
        "reviews": reviews,
        "tier_label": TIER_LABELS.get(tier, tier),
        "tier_price": TIER_PRICES.get(tier, ""),
    })
    if user:
        create_session(response, user.id, user.email, user.tier)
    return response


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


@router.get("/conversion", response_class=HTMLResponse)
async def conversion_page(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    days = int(request.query_params.get("days", 30))
    days = max(7, min(days, 365))  # clamp 7–365

    with get_db() as conn:
        data = get_conversion_analytics(conn, days=days)

    return templates.TemplateResponse(request, "conversion.html", {
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


# ---------------------------------------------------------------------------
# Account → Plan page (REVUE-382)
# ---------------------------------------------------------------------------

@router.get("/account/plan", response_class=HTMLResponse)
async def account_plan(request: Request) -> HTMLResponse:
    """Authenticated licence-status page.

    Reads the licence WITHOUT the is_active=1 filter so that the Lapsed state
    (is_active=False, tier preserved) is reachable. The filter in
    ``get_license_for_user`` hides lapsed rows from dashboard/onboarding/billing
    — this page intentionally needs to see them.

    States (see ``derive_plan_state``):
      active       — paid tier, is_active=True, validated
      lapsed       — any tier, is_active=False (subscription ended)
      free         — tier=free, is_active=True, validated
      not_activated— no licence row, OR a key that has never been validated
                     (last_validated_at IS NULL)

    REVUE-413: current_period_end / subscription_status are NULL on pre-migration
    rows; the template must handle both as optional and never show a raw ``None``
    or crash when they are absent.

    REVUE-382 AC5: in the not-activated state the Activation Command-Box is
    pre-filled with the AUTHENTICATED USER'S OWN licence key — never a generic
    placeholder, and never another user's key (the key comes from the user's own
    row via ``get_any_license_for_user(conn, user_id)`` → ``any_license``).
    """
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    user_id = session["user_id"]
    with get_db() as conn:
        user = get_user_by_id(conn, user_id)
        # any_license — UNFILTERED read (includes is_active=0 lapsed rows). This
        # is the authoritative source for the page STATE: it is the only read
        # that can surface the Lapsed state, since get_license_for_user filters
        # is_active=1 and would hide it.
        any_license = get_any_license_for_user(conn, user_id)
        # active_license — ACTIVE-ONLY read (is_active=1). This drives the USAGE
        # METER only: it is passed to the template as ``license_key`` because that
        # is the name the shared usage_bar partial expects. A lapsed user has no
        # active row, so the meter renders "No license key" rather than stale usage.
        active_license = get_license_for_user(conn, user_id)

    # Refresh the session tier from DB (mirrors dashboard.py Defect-E fix). The
    # cookie is actually re-issued below via create_session(response, ...) — a
    # bare session["tier"]= mutation alone is a no-op since the session is a
    # decoded copy, not a live store. A webhook upgrade updates the DB but not the
    # already-issued cookie, so without this the badge stays stale until re-login.
    if user:
        session["tier"] = user.tier

    state = derive_plan_state(any_license)
    tier_label = TIER_LABELS.get(any_license.tier if any_license else "free", "Free")

    masked_display = masked_key_display(any_license.key) if any_license else ""

    # AC5: the not-activated Command-Box pre-fills the user's REAL key into
    # `revue activate <key>`. Server-side, from the user's own row only.
    activate_command = (
        f"revue activate {any_license.key}" if any_license else "revue activate <your-key>"
    )

    # AC2: "Last verified Nh ago" — sourced from the validation cache
    # (last_validated_at), NOT the Stripe subscription record. NULL → fallback.
    last_verified = last_verified_ago(any_license.last_validated_at if any_license else None)

    # Renewal line: only render when current_period_end is not None; otherwise
    # fall back to "No renewal date available" — never show a raw None or "—".
    renewal_date: Optional[str] = None
    if any_license and any_license.current_period_end:
        renewal_date = any_license.current_period_end[:10]  # ISO date portion only

    response = templates.TemplateResponse(request, "account_plan.html", {
        "session": session,
        "state": state,
        # ``any_license`` — the unfiltered row (drives state, badge, masked key,
        # renewal line, AC5 pre-fill). May be a lapsed (is_active=0) row.
        "any_license": any_license,
        # ``license_key`` — the ACTIVE-ONLY row, named for the shared usage_bar
        # partial. Drives the usage meter only; None for a lapsed user.
        "license_key": active_license,
        "tier_label": tier_label,
        "masked_display": masked_display,
        "activate_command": activate_command,
        "last_verified": last_verified,
        "renewal_date": renewal_date,
        # ``currency_symbol`` is also supplied via templates.env.globals (config),
        # so the Free CTA already renders it; pass it explicitly here too so this
        # handler's context is self-contained and the Free-CTA price is pinned by
        # a test rather than relying on a global being present.
        "currency_symbol": CURRENCY_SYMBOL,
    })
    # Re-issue the session cookie so a DB tier change (e.g. a Stripe webhook
    # upgrade) is reflected in the badge without requiring re-login. Mirrors the
    # dashboard handler's Defect-E fix — the bare session mutation above does not
    # persist without this call.
    if user:
        create_session(response, user.id, user.email, user.tier)
    return response
