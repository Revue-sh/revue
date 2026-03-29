"""Stripe billing routes — Checkout, Billing Portal, and Webhook."""
from __future__ import annotations

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

    return templates.TemplateResponse(request, "billing_success.html", {
        "session": session,
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
        event = construct_webhook_event(payload, sig_header)
    except ValueError as exc:
        _LOG.warning("Webhook config error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        _LOG.warning("Webhook signature verification failed: %s", exc)
        return JSONResponse({"error": "Invalid signature"}, status_code=400)

    try:
        with get_db() as conn:
            result = process_webhook_event(event, conn)
        _LOG.info("Webhook processed: %s → %s", event["type"], result)
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        _LOG.error("Webhook processing error for %s: %s", event["type"], exc)
        return JSONResponse({"error": "Processing failed"}, status_code=500)
