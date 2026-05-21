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
    STRIPE_PRICE_ENT_STARTER    price_...
    STRIPE_PRICE_ENT_GROWTH     price_...

Optional:
    STRIPE_PRICE_INDIE_YEARLY   price_...
    STRIPE_PRICE_PRO_YEARLY     price_...
    APP_BASE_URL                https://revue.sh (default)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

_LOG = logging.getLogger(__name__)

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
    "enterprise_starter": {
        "label": "Enterprise Starter",
        "price_monthly": 59,
        "reviews_limit": None,
        "description": "Small enterprises. 1–10 seats, self-serve.",
    },
    "enterprise_growth": {
        "label": "Enterprise Growth",
        "price_monthly": 149,
        "reviews_limit": None,
        "description": "Mid-size enterprises. 11–50 seats.",
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
        tier:           Target tier (indie, pro, enterprise_starter, enterprise_growth).
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
    mapping = {
        os.environ.get("STRIPE_PRICE_INDIE_MONTHLY"): "indie",
        os.environ.get("STRIPE_PRICE_INDIE_YEARLY"): "indie",
        os.environ.get("STRIPE_PRICE_PRO_MONTHLY"): "pro",
        os.environ.get("STRIPE_PRICE_PRO_YEARLY"): "pro",
        os.environ.get("STRIPE_PRICE_ENT_STARTER"): "enterprise_starter",
        os.environ.get("STRIPE_PRICE_ENT_GROWTH"): "enterprise_growth",
    }
    return mapping.get(price_id)


def process_webhook_event(event, conn) -> str:
    """Process a verified Stripe webhook event and update the DB.

    Handles:
        customer.subscription.created   → upgrade user tier
        customer.subscription.updated   → sync tier on plan change
        customer.subscription.deleted   → downgrade to free

    Args:
        event: stripe.Event object (already verified).
        conn:  SQLite connection (caller holds transaction).

    Returns:
        Human-readable description of what was done.
    """
    from models import get_user_by_stripe_customer, update_user_tier, update_stripe_customer_id

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
        metadata = obj.get("metadata", {})
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
        _LOG.warning("No user found for Stripe customer %s", customer_id)
        return f"skipped:unknown_customer:{customer_id}"

    if event_type == "customer.subscription.deleted":
        update_user_tier(conn, user.id, "free")
        _LOG.info("User %s downgraded to free (subscription cancelled)", user.id)
        return f"downgraded:user={user.id}:free"

    # created / updated — find the tier from the price ID
    items = obj.get("items", {}).get("data", [])
    price_id = items[0]["price"]["id"] if items else None
    if not price_id:
        return "skipped:no_price_id"

    new_tier = tier_from_price_id(price_id)
    if not new_tier:
        _LOG.warning("Unrecognised price ID %s — not updating tier", price_id)
        return f"skipped:unknown_price:{price_id}"

    update_user_tier(conn, user.id, new_tier)
    _LOG.info("User %s upgraded to %s (price %s)", user.id, new_tier, price_id)
    return f"upgraded:user={user.id}:tier={new_tier}"
