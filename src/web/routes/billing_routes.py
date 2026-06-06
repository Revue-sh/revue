"""Stripe billing routes — Checkout, Billing Portal, and Webhook."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from auth import get_session
from billing import (
    TIER_DISPLAY,
    construct_webhook_event,
    create_billing_portal_session,
    create_checkout_session,
    is_configured,
    process_webhook_event,
)
from config import templates
from database import get_db
from models import get_license_for_user, get_user_by_id

_LOG = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Licence activation page — REVUE-277 Phase 2
# ---------------------------------------------------------------------------

@router.get("/activate", response_class=HTMLResponse)
async def activate_page(request: Request) -> HTMLResponse:
    """CLI-first activation fallback (REVUE-384). Unauthenticated — anyone with
    a licence key can land here. Step 1 is a single paste input; on a valid
    paste the Activation Command-Box echoes ``revue activate <key>`` as the
    recommended path. The legacy browser-mint flow (POST
    ``/api/v2/licence/activate``) moves below the fold, collapsed, as the only
    raw-JWT surface.

    The page takes no request input into its rendered context: the command and
    its copy payload are built client-side from the pasted key (validated against
    ``^lic_[a-f0-9]{32}$`` before anything renders). The reusable Command-Box's
    masked state is owned by the Account→Plan consumer (out of scope here) and is
    covered by a macro-render unit test, not this unauthenticated page.
    """
    return templates.TemplateResponse(request, "activate.html", {})


# ---------------------------------------------------------------------------
# Billing page — shows upgrade options
# ---------------------------------------------------------------------------

@router.get("/billing", response_class=HTMLResponse)
async def billing_page(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    user_id = session["user_id"]
    with get_db() as conn:
        user = get_user_by_id(conn, user_id)
        license_key = get_license_for_user(conn, user_id)

    return templates.TemplateResponse(request, "billing.html", {
        "session": session,
        "user": user,
        "license_key": license_key,
        "tiers": TIER_DISPLAY,
        "stripe_configured": is_configured(),
    })


# ---------------------------------------------------------------------------
# Checkout — redirect to Stripe
# ---------------------------------------------------------------------------

@router.post("/billing/checkout")
async def start_checkout(request: Request) -> RedirectResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    if not is_configured():
        return RedirectResponse("/billing?error=stripe_not_configured", status_code=303)

    form = await request.form()
    tier = str(form.get("tier", ""))
    interval = str(form.get("interval", "month"))

    if tier not in TIER_DISPLAY:
        return RedirectResponse("/billing?error=invalid_tier", status_code=303)

    user_id = session["user_id"]
    with get_db() as conn:
        user = get_user_by_id(conn, user_id)

    if not user:
        return RedirectResponse("/login", status_code=303)

    try:
        url = create_checkout_session(
            customer_email=user.email,
            tier=tier,
            interval=interval,
            customer_id=user.stripe_customer_id or None,
            metadata={"user_id": str(user_id)},
        )
        return RedirectResponse(url, status_code=303)
    except ValueError as exc:
        _LOG.warning("Checkout error for user %s: %s", user_id, exc)
        return RedirectResponse(f"/billing?error=config_error", status_code=303)
    except Exception as exc:
        _LOG.error("Stripe checkout failed for user %s: %s", user_id, exc)
        return RedirectResponse("/billing?error=stripe_error", status_code=303)


# ---------------------------------------------------------------------------
# Checkout success
# ---------------------------------------------------------------------------

@router.get("/billing/success", response_class=HTMLResponse)
async def checkout_success(request: Request) -> HTMLResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    # REVUE-361: the page leads with the Activation Command-Box pre-filled with
    # the user's real licence key. The key is NOT on the session, so look it up
    # from the DB. ``get_license_for_user`` returns the latest *active* key for
    # the user's workspace, or None (e.g. a webhook race right after checkout,
    # or a free user opening this URL directly) — the template falls back to a
    # CLI-first "activate later" prompt so the command-box never renders blank.
    user_id = session["user_id"]
    with get_db() as conn:
        license_key = get_license_for_user(conn, user_id)

    return templates.TemplateResponse(request, "billing_success.html", {
        "session": session,
        "license_key": license_key,
    })


# ---------------------------------------------------------------------------
# Billing portal — manage/cancel subscription
# ---------------------------------------------------------------------------

@router.post("/billing/portal")
async def billing_portal(request: Request) -> RedirectResponse:
    session = get_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    if not is_configured():
        return RedirectResponse("/billing?error=stripe_not_configured", status_code=303)

    user_id = session["user_id"]
    with get_db() as conn:
        user = get_user_by_id(conn, user_id)

    if not user or not user.stripe_customer_id:
        return RedirectResponse("/billing?error=no_subscription", status_code=303)

    try:
        url = create_billing_portal_session(customer_id=user.stripe_customer_id)
        return RedirectResponse(url, status_code=303)
    except Exception as exc:
        _LOG.error("Billing portal failed for user %s: %s", user_id, exc)
        return RedirectResponse("/billing?error=stripe_error", status_code=303)


# ---------------------------------------------------------------------------
# Stripe webhook — receives subscription events
# ---------------------------------------------------------------------------

@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> Response:
    """Receive and process Stripe webhook events.

    Stripe sends events to this endpoint. We verify the signature using
    the webhook secret, then update the user's tier accordingly.

    Events handled:
        checkout.session.completed       → link Stripe customer ID to user
        customer.subscription.created    → upgrade tier
        customer.subscription.updated    → sync tier on plan change
        customer.subscription.deleted    → downgrade to free
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        construct_webhook_event(payload, sig_header)  # verify HMAC; return value unused
    except ValueError as exc:
        _LOG.warning("Webhook config error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        _LOG.warning("Webhook signature verification failed: %s", exc)
        return JSONResponse({"error": "Invalid signature"}, status_code=400)

    # construct_event() returns a stripe.Event whose data.object is a
    # StripeObject — NOT dict-compatible under stripe-python v15 (obj.get()
    # raises AttributeError: get). Process the already-verified raw bytes as
    # plain nested dicts so process_webhook_event's dict access works.
    event = json.loads(payload)

    try:
        with get_db() as conn:
            result = process_webhook_event(event, conn)
        _LOG.info("Webhook processed: %s → %s", event.get("type"), result)
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        _LOG.error("Webhook processing error for %s: %s", event.get("type"), exc)
        return JSONResponse({"error": "Processing failed"}, status_code=500)
