"""Stripe billing integration for Revue.

Handles:
- Checkout session creation (upgrade flow)
- Billing portal session creation (manage/cancel)
- Webhook event processing (subscription created/updated/deleted)

Tier → Stripe price ID mapping is loaded from environment variables so
it works in both test mode (sk_test_*) and live mode (sk_live_*) without
code changes.

Required environment variables:
    STRIPE_SECRET_KEY           sk_test_... or sk_live_...
    STRIPE_WEBHOOK_SECRET       whsec_...  (from Stripe dashboard)
    STRIPE_PRICE_INDIE_MONTHLY  price_...
    STRIPE_PRICE_PRO_MONTHLY    price_...

Optional:
    STRIPE_PRICE_INDIE_YEARLY   price_...
    STRIPE_PRICE_PRO_YEARLY     price_...
    APP_BASE_URL                https://revue.sh (default)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# REVUE-413: Stripe subscription-status → licence-state mapping
# ---------------------------------------------------------------------------
# A subscription.created/updated event carries a ``status``. That status decides
# whether the licence stays active, lapses (inactive but tier retained), drops
# to free, or is left untouched. Encoded as a registry (not an if/elif chain) so
# adding a status is a one-line table edit. Any status NOT listed here is treated
# as "no_change" (the safe default) — a new/unknown Stripe status never silently
# downgrades or unlocks a customer.
#
#   "active"   → set tier from price_id, is_active=True   (normal / recovery)
#   "lapsed"   → is_active=False, RETAIN tier             (dunning: past_due/unpaid)
#   "free"     → reset tier to free via update_user_tier  (genuine cancellation)
#   "no_change"→ leave tier + is_active untouched         (transient states)
_SUBSCRIPTION_STATUS_STATE: dict[str, str] = {
    "active": "active",
    "trialing": "active",
    "past_due": "lapsed",
    "unpaid": "lapsed",
    "canceled": "free",
    "incomplete": "no_change",
    "incomplete_expired": "no_change",
    "paused": "no_change",
}

# When a subscription.created/updated event omits ``status`` entirely (older
# fixtures, partial payloads), assume the subscription is live — this preserves
# the pre-REVUE-413 behaviour where a status-less created/updated event upgraded
# the tier from the price id.
_DEFAULT_STATUS_STATE = "active"


def _state_for_status(status: Optional[str]) -> str:
    """Map a raw Stripe subscription status to a licence state token.

    The state tokens (``active`` / ``lapsed`` / ``free`` / ``no_change``) and the
    full status→state table are defined on ``_SUBSCRIPTION_STATUS_STATE`` above —
    see that block comment for the token list and per-state semantics.

    Unknown statuses map to ``"no_change"`` so an unrecognised value can never
    downgrade or re-activate a licence by accident.
    """
    if status is None:
        return _DEFAULT_STATUS_STATE
    return _SUBSCRIPTION_STATUS_STATE.get(status, "no_change")


def _epoch_to_iso(epoch: Optional[int]) -> Optional[str]:
    """Convert a Stripe epoch-seconds timestamp to an ISO-8601 UTC string.

    Stripe sends ``current_period_end`` as integer epoch seconds; the licence row
    stores it as a human-readable ISO instant. Returns None for a missing/None
    value (no renewal date to persist).
    """
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Tier config
# ---------------------------------------------------------------------------

TIER_DISPLAY: dict[str, dict] = {
    "indie": {
        "label": "Indie",
        "price_monthly": 9,
        "reviews_limit": 100,
        "description": "Solo devs, micro-teams. 100 reviews/month, all 6 agents.",
    },
    "pro": {
        "label": "Pro",
        "price_monthly": 29,
        "reviews_limit": None,  # unlimited
        "description": "Startups, agencies. Unlimited reviews, all 6 agents.",
    },
}

TIER_REVIEWS_LIMIT: dict[str, Optional[int]] = {
    "free": 25,
    "indie": 100,
    "pro": None,
    "enterprise_starter": None,
    "enterprise_growth": None,
    "enterprise_plus": None,
}


def _price_id(env_var: str) -> Optional[str]:
    return os.environ.get(env_var) or None


def get_price_id(tier: str, interval: str = "month") -> Optional[str]:
    """Return the Stripe price ID for a given tier and billing interval."""
    mapping = {
        ("indie", "month"): _price_id("STRIPE_PRICE_INDIE_MONTHLY"),
        ("indie", "year"): _price_id("STRIPE_PRICE_INDIE_YEARLY"),
        ("pro", "month"): _price_id("STRIPE_PRICE_PRO_MONTHLY"),
        ("pro", "year"): _price_id("STRIPE_PRICE_PRO_YEARLY"),
        ("enterprise_starter", "month"): _price_id("STRIPE_PRICE_ENT_STARTER"),
        ("enterprise_growth", "month"): _price_id("STRIPE_PRICE_ENT_GROWTH"),
    }
    return mapping.get((tier, interval))


# ---------------------------------------------------------------------------
# Stripe client helpers
# ---------------------------------------------------------------------------

def _stripe():
    """Return the stripe module, configured with the secret key."""
    import stripe as _s
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise ValueError(
            "STRIPE_SECRET_KEY is not set. "
            "Add it to your environment variables."
        )
    _s.api_key = key
    return _s


def is_configured() -> bool:
    """Return True if Stripe is configured (key present)."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

def create_checkout_session(
    customer_email: str,
    tier: str,
    interval: str = "month",
    customer_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Create a Stripe Checkout session and return the URL.

    Args:
        customer_email: Pre-fill the email field in Checkout.
        tier:           Target tier (indie, pro).
        interval:       Billing interval — "month" or "year".
        customer_id:    Existing Stripe customer ID (avoids duplicate customers).
        metadata:       Extra metadata attached to the session (e.g. user_id).

    Returns:
        Checkout session URL to redirect the user to.

    Raises:
        ValueError: If price ID is not configured for the requested tier.
        stripe.StripeError: On API errors.
    """
    stripe = _stripe()
    price_id = get_price_id(tier, interval)
    if not price_id:
        raise ValueError(
            f"No Stripe price ID configured for tier={tier} interval={interval}. "
            f"Set STRIPE_PRICE_{tier.upper()}_{'MONTHLY' if interval == 'month' else 'YEARLY'}."
        )

    base_url = os.environ.get("APP_BASE_URL", "https://revue.sh")
    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/billing",
        "metadata": metadata or {},
        "subscription_data": {"metadata": metadata or {}},
    }

    if customer_id:
        params["customer"] = customer_id
    else:
        params["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**params)
    return session.url


def create_billing_portal_session(
    customer_id: str,
    return_url: Optional[str] = None,
) -> str:
    """Create a Stripe Billing Portal session and return the URL.

    Allows the customer to manage, upgrade, or cancel their subscription.

    Args:
        customer_id: Stripe customer ID.
        return_url:  URL to redirect to after leaving the portal.

    Returns:
        Billing portal URL.
    """
    stripe = _stripe()
    base_url = os.environ.get("APP_BASE_URL", "https://revue.sh")
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url or f"{base_url}/dashboard",
    )
    return session.url


# ---------------------------------------------------------------------------
# Webhook processing
# ---------------------------------------------------------------------------

def construct_webhook_event(payload: bytes, sig_header: str):
    """Parse and verify a Stripe webhook event.

    Args:
        payload:    Raw request body bytes.
        sig_header: Value of the ``Stripe-Signature`` header.

    Returns:
        stripe.Event object.

    Raises:
        ValueError: If STRIPE_WEBHOOK_SECRET is not set.
        stripe.SignatureVerificationError: If signature is invalid.
    """
    stripe = _stripe()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not set.")
    return stripe.Webhook.construct_event(payload, sig_header, secret)


def tier_from_price_id(price_id: str) -> Optional[str]:
    """Map a Stripe price ID back to a Revue tier string.

    Checks all configured price env vars and returns the matching tier.
    Returns None if the price ID is unrecognised.
    """
    if not price_id:
        return None
    mapping = {
        os.environ.get("STRIPE_PRICE_INDIE_MONTHLY"): "indie",
        os.environ.get("STRIPE_PRICE_INDIE_YEARLY"): "indie",
        os.environ.get("STRIPE_PRICE_PRO_MONTHLY"): "pro",
        os.environ.get("STRIPE_PRICE_PRO_YEARLY"): "pro",
        os.environ.get("STRIPE_PRICE_ENT_STARTER"): "enterprise_starter",
        os.environ.get("STRIPE_PRICE_ENT_GROWTH"): "enterprise_growth",
    }
    # Defect D: unset env vars all become a single None key (last wins ->
    # enterprise_growth). Drop it so an unknown/None price never matches a tier.
    mapping.pop(None, None)
    return mapping.get(price_id)


def process_webhook_event(event, conn) -> str:
    """Process a verified Stripe webhook event and update the DB.

    Handles:
        customer.subscription.created   → upgrade user tier (+ persist renewal/status)
        customer.subscription.updated   → sync tier OR lapse OR cancel per status
        customer.subscription.deleted   → downgrade to free

    On created/updated the licence state is driven by the subscription ``status``
    via ``_SUBSCRIPTION_STATUS_STATE`` (REVUE-413):
        active/trialing → set tier from price_id, is_active=True, persist renewal
        past_due/unpaid → LAPSED: is_active=False, RETAIN tier, persist status
        canceled        → downgrade to free (genuine cancellation)
        other/unknown   → no change (safe default)

    The ``deleted`` event always maps to free regardless of status.

    Args:
        event: stripe.Event object (already verified).
        conn:  SQLite connection (caller holds transaction).

    Returns:
        Human-readable description of what was done.
    """
    from models import (
        get_user_by_id,
        get_user_by_stripe_customer,
        update_user_tier,
        update_stripe_customer_id,
        set_license_subscription_state,
    )

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type not in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "checkout.session.completed",
    ):
        return f"ignored:{event_type}"

    # -- checkout.session.completed: link customer ID to user --
    if event_type == "checkout.session.completed":
        customer_id = obj.get("customer")
        # Stripe sends `metadata: null` (not absent) for sessions created
        # outside Revue's flow; `get(..., {})` returns None on a null value,
        # so coerce explicitly to avoid AttributeError -> HTTP 500 -> retries.
        metadata = obj.get("metadata") or {}
        user_id = metadata.get("user_id")
        if user_id and customer_id:
            update_stripe_customer_id(conn, int(user_id), customer_id)
            _LOG.info("Linked Stripe customer %s to user %s", customer_id, user_id)
        return f"checkout_linked:user={user_id}"

    # -- subscription events: update tier --
    customer_id = obj.get("customer")
    if not customer_id:
        return "skipped:no_customer_id"

    user = get_user_by_stripe_customer(conn, customer_id)
    if not user:
        # Webhook delivery order is not guaranteed: customer.subscription.created
        # can arrive before checkout.session.completed links the customer. Fall
        # back to the user_id stamped into subscription_data.metadata at checkout
        # and link the customer now, so the upgrade isn't lost to a race.
        meta_user_id = (obj.get("metadata") or {}).get("user_id")
        if meta_user_id:
            user = get_user_by_id(conn, int(meta_user_id))
            if user and not user.stripe_customer_id:
                update_stripe_customer_id(conn, user.id, customer_id)
                _LOG.info("Linked Stripe customer %s to user %s (via subscription metadata)", customer_id, user.id)
    if not user:
        _LOG.warning("No user found for Stripe customer %s", customer_id)
        return f"skipped:unknown_customer:{customer_id}"

    if event_type == "customer.subscription.deleted":
        # The deleted event is a genuine, final cancellation — always free,
        # regardless of any status field. Regression-locked behaviour.
        update_user_tier(conn, user.id, "free")
        # Reset the subscription state too: a user who lapsed (is_active=0) and
        # then cancels must land as an ACTIVE free-tier licence — otherwise the
        # stale is_active=0 from the lapse permanently locks them out of the
        # free quota, and the stale 'past_due' status misrenders. No renewal
        # date for a cancelled subscription.
        set_license_subscription_state(
            conn, user.id,
            is_active=True,
            subscription_status="canceled",
            current_period_end=None,
        )
        _LOG.info("User %s downgraded to free (subscription cancelled)", user.id)
        return f"downgraded:user={user.id}:free"

    # created / updated — dispatch on the subscription STATE first (REVUE-413).
    # State must be resolved before any tier work: a `canceled` status can arrive
    # on an updated event that still carries the (now-defunct) price id, and must
    # map to free rather than re-upgrading to that price's tier.
    status = obj.get("status")
    state = _state_for_status(status)
    period_end = _epoch_to_iso(obj.get("current_period_end"))

    if state == "free":
        # Genuine cancellation delivered as an updated event (status=canceled).
        update_user_tier(conn, user.id, "free")
        # Same reset as the deleted branch: clear any prior lapsed is_active=0 so
        # the free-tier user keeps their free quota, and record the real status
        # instead of leaving a stale 'past_due'. current_period_end is NULLed (not
        # carried from the event): a cancelled subscription has no renewal date, so
        # persisting Stripe's still-present current_period_end would make the
        # Account → Plan page render a "renews on X" line for a free/cancelled plan.
        set_license_subscription_state(
            conn, user.id,
            is_active=True,
            subscription_status=status,
            current_period_end=None,
        )
        _LOG.info("User %s downgraded to free (status=%s)", user.id, status)
        return f"downgraded:user={user.id}:free:status={status}"

    if state == "lapsed":
        # Dunning (past_due / unpaid): suspend access but RETAIN the tier so the
        # Lapsed state — inactive licence, tier preserved — is reachable and the
        # Re-subscribe CTA is real. NOTE: Stripe always sends current_period_end
        # on a subscription object, so persisting it here cannot null out a prior
        # value in practice; no COALESCE guard needed.
        set_license_subscription_state(
            conn, user.id,
            is_active=False,
            subscription_status=status,
            current_period_end=period_end,
        )
        _LOG.info("User %s lapsed (status=%s, tier retained)", user.id, status)
        return f"lapsed:user={user.id}:status={status}"

    if state == "no_change":
        # Transient / unknown status (incomplete, paused, ...): touch nothing.
        _LOG.info("User %s subscription status=%s — no licence change", user.id, status)
        return f"skipped:status={status}"

    # state == "active" — find the tier from the price ID and (re)activate.
    # `items` may arrive as null; coerce as with metadata above.
    items = (obj.get("items") or {}).get("data", [])
    price_id = items[0]["price"]["id"] if items else None
    if not price_id:
        return "skipped:no_price_id"

    new_tier = tier_from_price_id(price_id)
    if not new_tier:
        _LOG.warning("Unrecognised price ID %s — not updating tier", price_id)
        return f"skipped:unknown_price:{price_id}"

    update_user_tier(conn, user.id, new_tier)
    # Persist renewal date + status and (re)activate — a recovery from a prior
    # lapsed state flips is_active back to True so the paid-up user is unlocked.
    set_license_subscription_state(
        conn, user.id,
        is_active=True,
        subscription_status=status,
        current_period_end=period_end,
    )
    _LOG.info("User %s upgraded to %s (price %s, status=%s)", user.id, new_tier, price_id, status)
    return f"upgraded:user={user.id}:tier={new_tier}"
