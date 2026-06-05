"""Tests for Story [64] — Stripe billing integration."""
from __future__ import annotations

import json
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


async def _signup(client: AsyncClient, email: str = "billing@test.com") -> None:
    resp = await client.post(
        "/signup",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)


def _get_user(email: str = "billing@test.com"):
    from database import get_db
    from models import get_user_by_email
    with get_db() as conn:
        return get_user_by_email(conn, email)


# =====================================================================
# billing.py unit tests
# =====================================================================

def test_is_configured_false_without_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from billing import is_configured
    assert is_configured() is False


def test_is_configured_true_with_key(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    from importlib import reload
    import billing
    reload(billing)
    from billing import is_configured
    assert is_configured() is True


def test_get_price_id_returns_none_when_not_set(monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_INDIE_MONTHLY", raising=False)
    from billing import get_price_id
    assert get_price_id("indie", "month") is None


def test_get_price_id_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_indie123")
    from billing import get_price_id
    assert get_price_id("indie", "month") == "price_indie123"


def test_tier_from_price_id_maps_correctly(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_indie")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_ENT_STARTER", "price_ent_s")
    monkeypatch.setenv("STRIPE_PRICE_ENT_GROWTH", "price_ent_g")
    from billing import tier_from_price_id
    assert tier_from_price_id("price_indie") == "indie"
    assert tier_from_price_id("price_pro") == "pro"
    assert tier_from_price_id("price_ent_s") == "enterprise_starter"
    assert tier_from_price_id("price_ent_g") == "enterprise_growth"
    assert tier_from_price_id("price_unknown") is None


def test_tier_display_has_all_tiers():
    from billing import TIER_DISPLAY
    assert "indie" in TIER_DISPLAY
    assert "pro" in TIER_DISPLAY
    assert "enterprise_starter" in TIER_DISPLAY
    assert "enterprise_growth" in TIER_DISPLAY


def test_tier_display_prices():
    from billing import TIER_DISPLAY
    assert TIER_DISPLAY["indie"]["price_monthly"] == 9
    assert TIER_DISPLAY["pro"]["price_monthly"] == 29
    assert TIER_DISPLAY["enterprise_starter"]["price_monthly"] == 59
    assert TIER_DISPLAY["enterprise_growth"]["price_monthly"] == 149


def test_create_checkout_session_raises_without_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from billing import create_checkout_session
    with pytest.raises(ValueError, match="STRIPE_SECRET_KEY"):
        create_checkout_session("user@test.com", "indie")


def test_create_checkout_session_raises_without_price_id(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.delenv("STRIPE_PRICE_INDIE_MONTHLY", raising=False)
    from billing import create_checkout_session
    with pytest.raises(ValueError, match="price ID"):
        create_checkout_session("user@test.com", "indie")


def test_create_checkout_session_calls_stripe(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_test_indie")

    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/test"

    with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        from billing import create_checkout_session
        url = create_checkout_session(
            customer_email="user@test.com",
            tier="indie",
            metadata={"user_id": "42"},
        )

    assert url == "https://checkout.stripe.com/pay/test"
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["mode"] == "subscription"
    assert call_kwargs["line_items"][0]["price"] == "price_test_indie"
    assert call_kwargs["metadata"]["user_id"] == "42"
    assert "customer_email" in call_kwargs


def test_create_checkout_session_uses_existing_customer_id(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_test_pro")

    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/test2"

    with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        from billing import create_checkout_session
        create_checkout_session(
            customer_email="user@test.com",
            tier="pro",
            customer_id="cus_existing123",
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs.get("customer") == "cus_existing123"
    assert "customer_email" not in call_kwargs


def test_create_billing_portal_session(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    # Neutralise any ambient APP_BASE_URL (e.g. a dev shell exporting
    # http://localhost:8000) so the assertion verifies the production default.
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    mock_session = MagicMock()
    mock_session.url = "https://billing.stripe.com/session/test"

    with patch("stripe.billing_portal.Session.create", return_value=mock_session) as mock_create:
        from billing import create_billing_portal_session
        url = create_billing_portal_session("cus_test123")

    assert url == "https://billing.stripe.com/session/test"
    mock_create.assert_called_once_with(
        customer="cus_test123",
        return_url="https://revue.sh/dashboard",
    )


# =====================================================================
# Webhook processing
# =====================================================================

def _make_subscription_event(event_type: str, customer_id: str, price_id: str) -> dict:
    return {
        "type": event_type,
        "data": {
            "object": {
                "customer": customer_id,
                "items": {"data": [{"price": {"id": price_id}}]},
            }
        },
    }


def _make_checkout_event(customer_id: str, user_id: str) -> dict:
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": customer_id,
                "metadata": {"user_id": user_id},
            }
        },
    }


def test_process_webhook_subscription_created(monkeypatch, _tmp_db):
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_indie_test")

    from database import get_db, get_connection
    from models import get_user_by_email, create_user, create_workspace, create_license_key
    import hashlib

    with get_db() as conn:
        user_id = create_user(conn, "webhook@test.com", "hash")
        ws_id = create_workspace(conn, user_id, "ws")
        create_license_key(conn, ws_id, "lic_test123")
        # Set stripe_customer_id
        conn.execute("UPDATE users SET stripe_customer_id = 'cus_test' WHERE id = ?", (user_id,))

    event = _make_subscription_event(
        "customer.subscription.created", "cus_test", "price_indie_test"
    )

    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        result = process_webhook_event(event, conn)

    assert "indie" in result
    with get_db() as conn:
        user = get_user_by_email(conn, "webhook@test.com")
    assert user.tier == "indie"


def test_process_webhook_subscription_deleted(monkeypatch, _tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, get_user_by_email

    with get_db() as conn:
        user_id = create_user(conn, "cancel@test.com", "hash")
        ws_id = create_workspace(conn, user_id, "ws")
        create_license_key(conn, ws_id, "lic_cancel", tier="pro", reviews_limit=None)
        conn.execute("UPDATE users SET tier = 'pro', stripe_customer_id = 'cus_cancel' WHERE id = ?", (user_id,))

    event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_cancel", "items": {"data": []}}},
    }

    from billing import process_webhook_event
    with get_db() as conn:
        result = process_webhook_event(event, conn)

    assert "free" in result
    with get_db() as conn:
        user = get_user_by_email(conn, "cancel@test.com")
    assert user.tier == "free"


def test_process_webhook_checkout_links_customer(monkeypatch, _tmp_db):
    from database import get_db
    from models import create_user, create_workspace, get_user_by_email

    with get_db() as conn:
        user_id = create_user(conn, "link@test.com", "hash")
        create_workspace(conn, user_id, "ws")

    event = _make_checkout_event("cus_newlink", str(user_id))

    from billing import process_webhook_event
    with get_db() as conn:
        result = process_webhook_event(event, conn)

    assert "checkout_linked" in result
    with get_db() as conn:
        user = get_user_by_email(conn, "link@test.com")
    assert user.stripe_customer_id == "cus_newlink"


def test_process_webhook_checkout_null_metadata_does_not_crash(_tmp_db):
    """Stripe sends metadata: null for sessions not created via Revue
    (dashboard sessions, payment links). The handler must treat null as
    empty and return gracefully — regression for the AttributeError -> 500
    surfaced during REVUE-315 webhook E2E."""
    from database import get_db
    from billing import process_webhook_event

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"customer": "cus_x", "metadata": None}},
    }
    with get_db() as conn:
        result = process_webhook_event(event, conn)
    assert result.startswith("checkout_linked")


def test_process_webhook_subscription_null_items_does_not_crash(_tmp_db):
    """A subscription event with items: null must not crash (same null-field
    anti-pattern as null metadata). Regression for billing.py:288.

    The customer must be *known* so execution reaches the items read; an
    unknown customer short-circuits earlier and would never exercise the bug.
    """
    from database import get_db
    from models import create_user, update_stripe_customer_id
    from billing import process_webhook_event

    with get_db() as conn:
        user_id = create_user(conn, "nullitems@test.com", "hash")
        update_stripe_customer_id(conn, user_id, "cus_nullitems")

    event = {
        "type": "customer.subscription.created",
        "data": {"object": {"customer": "cus_nullitems", "items": None}},
    }
    with get_db() as conn:
        result = process_webhook_event(event, conn)
    # null items → no price → graceful skip, not an AttributeError/500.
    assert result.startswith("skipped")


def test_process_webhook_subscription_before_checkout_link(monkeypatch, _tmp_db):
    """REVUE-315 Defect C: webhook order is not guaranteed. If
    customer.subscription.created arrives BEFORE checkout.session.completed
    links the customer, the tier must still upgrade — via the user_id stamped
    into subscription_data.metadata at checkout."""
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_c")
    from database import get_db
    from models import create_user, get_user_by_email
    from billing import process_webhook_event

    with get_db() as conn:
        uid = create_user(conn, "race@test.com", "hash")  # free, NOT linked

    event = {
        "type": "customer.subscription.created",
        "data": {"object": {
            "customer": "cus_race",
            "items": {"data": [{"price": {"id": "price_pro_c"}}]},
            "metadata": {"user_id": str(uid)},
        }},
    }
    with get_db() as conn:
        result = process_webhook_event(event, conn)
    assert "upgraded" in result
    with get_db() as conn:
        user = get_user_by_email(conn, "race@test.com")
    assert user.tier == "pro"
    assert user.stripe_customer_id == "cus_race"  # linked via fallback


def test_tier_from_price_id_none_and_unconfigured(monkeypatch):
    """REVUE-315 Defect D: a None/unknown price must never map to a tier, even
    when some STRIPE_PRICE_* env vars are unset — unset vars become None dict
    keys that previously collided and made tier_from_price_id(None) return the
    last tier (enterprise_growth)."""
    from billing import tier_from_price_id
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_d")
    for v in ("STRIPE_PRICE_INDIE_MONTHLY", "STRIPE_PRICE_INDIE_YEARLY",
              "STRIPE_PRICE_PRO_YEARLY", "STRIPE_PRICE_ENT_STARTER",
              "STRIPE_PRICE_ENT_GROWTH"):
        monkeypatch.delenv(v, raising=False)
    assert tier_from_price_id(None) is None
    assert tier_from_price_id("price_unknown") is None
    assert tier_from_price_id("price_pro_d") == "pro"


def test_process_webhook_unknown_customer_skipped(monkeypatch, _tmp_db):
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_indie_test")
    from database import get_db
    from billing import process_webhook_event

    event = _make_subscription_event(
        "customer.subscription.created", "cus_unknown", "price_indie_test"
    )
    with get_db() as conn:
        result = process_webhook_event(event, conn)
    assert "skipped" in result


def test_process_webhook_ignores_irrelevant_events(_tmp_db):
    from database import get_db
    from billing import process_webhook_event

    event = {"type": "payment_intent.created", "data": {"object": {}}}
    with get_db() as conn:
        result = process_webhook_event(event, conn)
    assert result.startswith("ignored:")


# =====================================================================
# Route tests
# =====================================================================

@pytest.mark.asyncio
async def test_billing_page_requires_auth(client: AsyncClient):
    resp = await client.get("/billing", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_billing_page_renders(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/billing")
    assert resp.status_code == 200
    assert b"Upgrade your plan" in resp.content
    assert b"Indie" in resp.content
    assert b"Pro" in resp.content
    assert b"Enterprise Starter" in resp.content
    assert b"Enterprise Growth" in resp.content


@pytest.mark.asyncio
def test_currency_symbol_is_single_source():
    """REVUE-315: one CURRENCY_SYMBOL constant feeds every surface (templates via
    Jinja global, Python via import) — change it once, the whole site follows."""
    from config import CURRENCY_SYMBOL, templates
    assert templates.env.globals.get("currency_symbol") == CURRENCY_SYMBOL


@pytest.mark.asyncio
async def test_billing_page_shows_prices(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/billing")
    # USD pricing (REVUE-315, Anthropic-style) — display must match the charged currency.
    # Symbol comes from the single config.CURRENCY_SYMBOL source (DRY).
    assert "$9" in resp.text
    assert "$29" in resp.text
    assert "$59" in resp.text
    assert "$149" in resp.text
    # No stray pound-denominated plan prices remain on the billing page.
    assert "£9" not in resp.text
    assert "£29" not in resp.text


@pytest.mark.asyncio
async def test_billing_page_shows_current_plan(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/billing")
    assert b"Current plan" in resp.content
    assert b"free" in resp.content


@pytest.mark.asyncio
async def test_billing_page_shows_coming_soon_without_stripe(client: AsyncClient, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    await _signup(client)
    resp = await client.get("/billing")
    assert b"Coming soon" in resp.content


@pytest.mark.asyncio
async def test_billing_checkout_requires_auth(client: AsyncClient):
    resp = await client.post(
        "/billing/checkout",
        data={"tier": "indie"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_billing_checkout_without_stripe_redirects(client: AsyncClient, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    await _signup(client)
    resp = await client.post(
        "/billing/checkout",
        data={"tier": "indie"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=stripe_not_configured" in resp.headers["location"]


@pytest.mark.asyncio
async def test_billing_checkout_invalid_tier(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    await _signup(client)
    resp = await client.post(
        "/billing/checkout",
        data={"tier": "invalid_tier"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=invalid_tier" in resp.headers["location"]


@pytest.mark.asyncio
async def test_billing_checkout_redirects_to_stripe(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_test_indie")
    await _signup(client)

    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/cs_test_123"

    with patch("stripe.checkout.Session.create", return_value=mock_session):
        resp = await client.post(
            "/billing/checkout",
            data={"tier": "indie", "interval": "month"},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://checkout.stripe.com/pay/cs_test_123"


@pytest.mark.asyncio
async def test_billing_success_page(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/billing/success")
    assert resp.status_code == 200
    assert b"all set" in resp.content


@pytest.mark.asyncio
async def test_billing_portal_without_subscription(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    await _signup(client)
    resp = await client.post("/billing/portal", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=no_subscription" in resp.headers["location"]


@pytest.mark.asyncio
async def test_billing_portal_redirects_to_stripe(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    await _signup(client)

    # Set stripe_customer_id
    from database import get_db
    user = _get_user()
    with get_db() as conn:
        conn.execute("UPDATE users SET stripe_customer_id = 'cus_portal_test' WHERE id = ?", (user.id,))

    mock_portal = MagicMock()
    mock_portal.url = "https://billing.stripe.com/session/portal_test"

    with patch("stripe.billing_portal.Session.create", return_value=mock_portal):
        resp = await client.post("/billing/portal", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://billing.stripe.com/session/portal_test"


@pytest.mark.asyncio
async def test_stripe_webhook_invalid_signature(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

    with patch("stripe.Webhook.construct_event", side_effect=Exception("Bad sig")):
        resp = await client.post(
            "/webhooks/stripe",
            content=b'{"type":"test"}',
            headers={"stripe-signature": "bad_sig"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_stripe_webhook_processes_subscription_created(client: AsyncClient, monkeypatch, _tmp_db):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_wh")

    # Create a user with a stripe customer ID
    from database import get_db
    from models import create_user, create_workspace, create_license_key
    with get_db() as conn:
        uid = create_user(conn, "wh@test.com", "hash")
        wsid = create_workspace(conn, uid, "ws")
        create_license_key(conn, wsid, "lic_wh")
        conn.execute("UPDATE users SET stripe_customer_id = 'cus_wh' WHERE id = ?", (uid,))

    event_payload = json.dumps({
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "customer": "cus_wh",
                "items": {"data": [{"price": {"id": "price_pro_wh"}}]},
            }
        },
    }).encode()

    mock_event = {
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "customer": "cus_wh",
                "items": {"data": [{"price": {"id": "price_pro_wh"}}]},
            }
        },
    }

    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        resp = await client.post(
            "/webhooks/stripe",
            content=event_payload,
            headers={"stripe-signature": "t=123,v1=abc"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "pro" in data["result"]

    from models import get_user_by_email
    with get_db() as conn:
        user = get_user_by_email(conn, "wh@test.com")
    assert user.tier == "pro"


@pytest.mark.asyncio
async def test_stripe_webhook_processes_real_stripe_object(client: AsyncClient, monkeypatch, _tmp_db):
    """REVUE-315 Defect B regression: real webhooks arrive as a stripe.Event
    (StripeObject), NOT a dict. Under stripe-python v15 `obj.get(...)` on a
    StripeObject raises `AttributeError: get` -> HTTP 500. The route must
    process the verified raw payload as a plain dict. The dict-based mock in
    the test above does NOT exercise this path — this one does."""
    import stripe
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_obj")

    from database import get_db
    from models import create_user, get_user_by_email
    with get_db() as conn:
        uid = create_user(conn, "obj@test.com", "hash")
        conn.execute("UPDATE users SET stripe_customer_id = 'cus_obj' WHERE id = ?", (uid,))

    event_payload = json.dumps({
        "type": "customer.subscription.created",
        "data": {"object": {
            "customer": "cus_obj",
            "items": {"data": [{"price": {"id": "price_pro_obj"}}]},
        }},
    }).encode()

    # Return a real StripeObject from verification, exactly as construct_event
    # produces at runtime — this is what the dict-based mock failed to model.
    def _as_stripe_object(payload, sig):
        return stripe.Event.construct_from(json.loads(payload), "k")

    with patch("routes.billing_routes.construct_webhook_event", side_effect=_as_stripe_object):
        resp = await client.post(
            "/webhooks/stripe",
            content=event_payload,
            headers={"stripe-signature": "t=123,v1=abc"},
        )

    assert resp.status_code == 200, resp.text
    assert "pro" in resp.json()["result"]
    with get_db() as conn:
        assert get_user_by_email(conn, "obj@test.com").tier == "pro"


# =====================================================================
# DB helpers
# =====================================================================

def test_update_user_tier_syncs_license_key(_tmp_db):
    from database import get_db, REVIEWS_LIMIT_BY_TIER
    from models import create_user, create_workspace, create_license_key, update_user_tier, get_license_for_user

    with get_db() as conn:
        uid = create_user(conn, "tier@test.com", "hash")
        wsid = create_workspace(conn, uid, "ws")
        create_license_key(conn, wsid, "lic_tier", tier="free", reviews_limit=25)

    with get_db() as conn:
        update_user_tier(conn, uid, "pro")

    with get_db() as conn:
        lic = get_license_for_user(conn, uid)
    assert lic.tier == "pro"
    assert lic.reviews_limit is None  # unlimited for pro


def test_update_stripe_customer_id(_tmp_db):
    from database import get_db
    from models import create_user, update_stripe_customer_id, get_user_by_email

    with get_db() as conn:
        create_user(conn, "cust@test.com", "hash")

    with get_db() as conn:
        user = get_user_by_email(conn, "cust@test.com")
        update_stripe_customer_id(conn, user.id, "cus_new123")

    with get_db() as conn:
        user = get_user_by_email(conn, "cust@test.com")
    assert user.stripe_customer_id == "cus_new123"


def test_dashboard_upgrade_link_points_to_billing(client):
    """Upgrade CTA on dashboard links to /billing (not 'Coming soon')."""
    # Resolve the template path relative to this file so the test passes
    # regardless of the cwd pytest was invoked from.
    template = (
        pathlib.Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"
    )
    content = template.read_text()
    assert 'href="/billing"' in content
    assert "Coming soon" not in content


@pytest.mark.asyncio
async def test_dashboard_reflects_db_tier_after_upgrade(client: AsyncClient, _tmp_db):
    """REVUE-315 Defect E: the dashboard badge must read tier from the DB, not
    the login session. A webhook upgrade (free->pro) updates the DB but not the
    already-issued session cookie, so the badge showed stale FREE until re-login."""
    await _signup(client)  # session tier == free
    from database import get_db
    user = _get_user()
    with get_db() as conn:
        conn.execute("UPDATE users SET tier='pro' WHERE id=?", (user.id,))

    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "Pro" in resp.text
    assert "$29/mo" in resp.text          # pro price badge (USD)
    assert "$0/mo" not in resp.text       # stale free badge gone


# =====================================================================
# REVUE-413 — persist current_period_end + subscription_status; real
# lapsed transition. Stripe-status → licence-state mapping:
#   active / trialing            → active  (tier from price_id, is_active=1)
#   past_due / unpaid            → LAPSED  (is_active=0, tier RETAINED)
#   canceled / deleted-no-status → free    (tier reset via update_user_tier)
#   incomplete / incomplete_expired / paused → no tier/is_active change
# =====================================================================

# A realistic Stripe subscription period-end: epoch seconds for a future
# renewal date (2025-12-31T00:00:00Z). Stripe sends current_period_end as a
# top-level epoch int on the subscription object.
_PERIOD_END_EPOCH = 1767139200
_PERIOD_END_ISO = "2025-12-31T00:00:00+00:00"


def _make_subscription_event_full(
    event_type: str,
    customer_id: str,
    price_id: str | None = None,
    status: str | None = None,
    current_period_end: int | None = None,
) -> dict:
    """Build a subscription event carrying status + current_period_end, the
    two fields process_webhook_event previously discarded."""
    obj: dict = {"customer": customer_id}
    if price_id is not None:
        obj["items"] = {"data": [{"price": {"id": price_id}}]}
    if status is not None:
        obj["status"] = status
    if current_period_end is not None:
        obj["current_period_end"] = current_period_end
    return {"type": event_type, "data": {"object": obj}}


def _seed_subscribed_user(email: str, customer_id: str, key: str, tier: str = "pro"):
    """Create a user + workspace + active licence wired to a Stripe customer."""
    from database import get_db
    from models import create_user, create_workspace, create_license_key

    with get_db() as conn:
        uid = create_user(conn, email, "hash")
        wsid = create_workspace(conn, uid, "ws")
        create_license_key(conn, wsid, key, tier=tier, reviews_limit=None)
        conn.execute(
            "UPDATE users SET tier = ?, stripe_customer_id = ? WHERE id = ?",
            (tier, customer_id, uid),
        )
    return uid


def test_webhook_subscription_updated_persists_current_period_end(monkeypatch, _tmp_db):
    """subscription.updated carrying current_period_end persists it onto the
    licence row and it is readable via the licence accessor."""
    # Arrange
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    uid = _seed_subscribed_user("renew@test.com", "cus_renew", "lic_renew")
    event = _make_subscription_event_full(
        "customer.subscription.updated",
        "cus_renew",
        price_id="price_pro_413",
        status="active",
        current_period_end=_PERIOD_END_EPOCH,
    )

    # Act
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        process_webhook_event(event, conn)

    # Assert — renewal date persisted and readable through the active accessor
    from models import get_license_for_user
    with get_db() as conn:
        lic = get_license_for_user(conn, uid)
    assert lic.current_period_end == _PERIOD_END_ISO
    assert lic.subscription_status == "active"
    assert lic.is_active is True
    assert lic.tier == "pro"


def test_webhook_past_due_marks_lapsed_and_retains_tier(monkeypatch, _tmp_db):
    """past_due status marks the licence is_active=False while RETAINING tier —
    the Lapsed state must be reachable and NOT forced to free."""
    # Arrange
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    _seed_subscribed_user("lapsed@test.com", "cus_lapsed", "lic_lapsed")
    event = _make_subscription_event_full(
        "customer.subscription.updated",
        "cus_lapsed",
        price_id="price_pro_413",
        status="past_due",
        current_period_end=_PERIOD_END_EPOCH,
    )

    # Act
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        result = process_webhook_event(event, conn)

    # Assert — lapsed: inactive, tier retained (read via unfiltered accessor)
    from models import get_license_by_key
    with get_db() as conn:
        lic = get_license_by_key(conn, "lic_lapsed")
    assert lic.is_active is False
    assert lic.tier == "pro"          # tier RETAINED, NOT forced to free
    assert lic.subscription_status == "past_due"
    assert "lapsed" in result


def test_webhook_unpaid_marks_lapsed_and_retains_tier(monkeypatch, _tmp_db):
    """unpaid status is treated identically to past_due: lapsed, tier retained."""
    # Arrange
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    _seed_subscribed_user("unpaid@test.com", "cus_unpaid", "lic_unpaid")
    event = _make_subscription_event_full(
        "customer.subscription.updated",
        "cus_unpaid",
        price_id="price_pro_413",
        status="unpaid",
    )

    # Act
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        process_webhook_event(event, conn)

    # Assert
    from models import get_license_by_key
    with get_db() as conn:
        lic = get_license_by_key(conn, "lic_unpaid")
    assert lic.is_active is False
    assert lic.tier == "pro"


def test_webhook_recovery_from_lapsed_reactivates_licence(monkeypatch, _tmp_db):
    """A lapsed licence that recovers (past_due → active) must flip is_active
    back to True, or a paid-up user stays locked out forever."""
    # Arrange — drive the licence into the lapsed state first
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    _seed_subscribed_user("recover@test.com", "cus_recover", "lic_recover")
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        process_webhook_event(
            _make_subscription_event_full(
                "customer.subscription.updated", "cus_recover",
                price_id="price_pro_413", status="past_due",
            ),
            conn,
        )

    # Act — payment recovers; Stripe sends status=active
    with get_db() as conn:
        process_webhook_event(
            _make_subscription_event_full(
                "customer.subscription.updated", "cus_recover",
                price_id="price_pro_413", status="active",
                current_period_end=_PERIOD_END_EPOCH,
            ),
            conn,
        )

    # Assert — reactivated, tier intact, renewal date refreshed
    from models import get_license_by_key
    with get_db() as conn:
        lic = get_license_by_key(conn, "lic_recover")
    assert lic.is_active is True
    assert lic.tier == "pro"
    assert lic.subscription_status == "active"
    assert lic.current_period_end == _PERIOD_END_ISO


def test_webhook_canceled_status_downgrades_to_free(monkeypatch, _tmp_db):
    """A genuine full cancellation (status=canceled) maps to free per the
    documented rule — distinct from past_due's lapsed state."""
    # Arrange — the cancelled subscription still carries a future
    # current_period_end (Stripe sends it); the free row must NOT keep it.
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    _seed_subscribed_user("canceled@test.com", "cus_canceled", "lic_canceled")
    event = _make_subscription_event_full(
        "customer.subscription.updated",
        "cus_canceled",
        price_id="price_pro_413",
        status="canceled",
        current_period_end=_PERIOD_END_EPOCH,
    )

    # Act
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        result = process_webhook_event(event, conn)

    # Assert — downgraded to free, renewal date cleared (no "renews on X")
    from models import get_user_by_email, get_license_by_key
    with get_db() as conn:
        user = get_user_by_email(conn, "canceled@test.com")
        lic = get_license_by_key(conn, "lic_canceled")
    assert user.tier == "free"
    assert "free" in result
    assert lic.current_period_end is None


def test_webhook_price_id_to_tier_derivation_unchanged_regression(monkeypatch, _tmp_db):
    """REVUE-413 Test Case 4 (explicit regression): the existing
    price_id → tier derivation is UNAFFECTED by the new status-dispatch path.

    An active subscription event carrying a known price_id must still resolve to
    the tier that ``tier_from_price_id`` maps that price to — i.e. the
    status-driven dispatch added in REVUE-413 routes an active event through the
    unchanged price→tier logic, not around it. Asserted for two distinct prices
    so a single hard-coded tier can't pass by accident."""
    # Arrange — two configured prices mapping to two different tiers
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_indie_tc4")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_tc4")
    from billing import tier_from_price_id, process_webhook_event
    from database import get_db
    from models import get_license_by_key
    # Sanity: the derivation function itself maps these prices as expected.
    assert tier_from_price_id("price_indie_tc4") == "indie"
    assert tier_from_price_id("price_pro_tc4") == "pro"

    _seed_subscribed_user("tc4indie@test.com", "cus_tc4_indie", "lic_tc4_indie", tier="free")
    _seed_subscribed_user("tc4pro@test.com", "cus_tc4_pro", "lic_tc4_pro", tier="free")

    # Act — active subscription events carrying each known price_id
    with get_db() as conn:
        process_webhook_event(
            _make_subscription_event_full(
                "customer.subscription.updated", "cus_tc4_indie",
                price_id="price_indie_tc4", status="active",
            ),
            conn,
        )
        process_webhook_event(
            _make_subscription_event_full(
                "customer.subscription.created", "cus_tc4_pro",
                price_id="price_pro_tc4", status="active",
            ),
            conn,
        )

    # Assert — each licence resolved to the tier its price_id derives to,
    # matching tier_from_price_id exactly (derivation unchanged by dispatch).
    with get_db() as conn:
        indie_lic = get_license_by_key(conn, "lic_tc4_indie")
        pro_lic = get_license_by_key(conn, "lic_tc4_pro")
    assert indie_lic.tier == tier_from_price_id("price_indie_tc4") == "indie"
    assert pro_lic.tier == tier_from_price_id("price_pro_tc4") == "pro"


def test_webhook_cancel_after_lapse_reactivates_free_licence(monkeypatch, _tmp_db):
    """REVUE-413 review finding: a licence that lapsed (is_active=0) and then
    cancels must land as an ACTIVE free-tier licence — otherwise the stale
    is_active=0 from the lapse permanently locks the user out of the free quota
    and the subscription_status stays a misleading 'past_due'."""
    # Arrange — drive the licence into the lapsed state first
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    _seed_subscribed_user("lapsecancel@test.com", "cus_lc", "lic_lc")
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        process_webhook_event(
            _make_subscription_event_full(
                "customer.subscription.updated", "cus_lc",
                price_id="price_pro_413", status="past_due",
            ),
            conn,
        )

    # Act — subscription is then cancelled (status=canceled)
    with get_db() as conn:
        process_webhook_event(
            _make_subscription_event_full(
                "customer.subscription.updated", "cus_lc",
                price_id="price_pro_413", status="canceled",
            ),
            conn,
        )

    # Assert — free AND active again, status no longer stale 'past_due'
    from models import get_license_by_key
    with get_db() as conn:
        lic = get_license_by_key(conn, "lic_lc")
    assert lic.tier == "free"
    assert lic.is_active is True       # NOT stuck at the lapsed is_active=0
    assert lic.subscription_status == "canceled"


def test_webhook_delete_after_lapse_reactivates_free_licence(monkeypatch, _tmp_db):
    """Same reactivation requirement as cancel, via the deleted event path:
    lapse then customer.subscription.deleted → active free licence."""
    # Arrange — lapse first
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    _seed_subscribed_user("lapsedel@test.com", "cus_ld", "lic_ld")
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        process_webhook_event(
            _make_subscription_event_full(
                "customer.subscription.updated", "cus_ld",
                price_id="price_pro_413", status="past_due",
            ),
            conn,
        )

    # Act — subscription deleted
    with get_db() as conn:
        process_webhook_event(
            {"type": "customer.subscription.deleted",
             "data": {"object": {"customer": "cus_ld", "items": {"data": []}}}},
            conn,
        )

    # Assert — free, active, status reset
    from models import get_license_by_key
    with get_db() as conn:
        lic = get_license_by_key(conn, "lic_ld")
    assert lic.tier == "free"
    assert lic.is_active is True
    assert lic.subscription_status == "canceled"


def test_webhook_incomplete_status_leaves_state_unchanged(monkeypatch, _tmp_db):
    """incomplete status (checkout not finished) must not change tier or
    is_active — the safest no-op per the documented mapping."""
    # Arrange
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_413")
    _seed_subscribed_user("incomplete@test.com", "cus_incomplete", "lic_incomplete")
    event = _make_subscription_event_full(
        "customer.subscription.updated",
        "cus_incomplete",
        price_id="price_pro_413",
        status="incomplete",
    )

    # Act
    from billing import process_webhook_event
    from database import get_db
    with get_db() as conn:
        process_webhook_event(event, conn)

    # Assert — unchanged from the seeded active pro state
    from models import get_license_by_key
    with get_db() as conn:
        lic = get_license_by_key(conn, "lic_incomplete")
    assert lic.is_active is True
    assert lic.tier == "pro"


def test_set_license_subscription_state_does_not_touch_tier(_tmp_db):
    """set_license_subscription_state writes is_active + period/status without
    altering tier or reviews_limit (unlike update_user_tier)."""
    # Arrange
    from database import get_db
    from models import (
        create_user, create_workspace, create_license_key,
        set_license_subscription_state, get_license_by_key,
    )
    with get_db() as conn:
        uid = create_user(conn, "state@test.com", "hash")
        wsid = create_workspace(conn, uid, "ws")
        create_license_key(conn, wsid, "lic_state", tier="pro", reviews_limit=None)

    # Act
    with get_db() as conn:
        set_license_subscription_state(
            conn, uid,
            is_active=False,
            subscription_status="past_due",
            current_period_end=_PERIOD_END_ISO,
        )

    # Assert
    with get_db() as conn:
        lic = get_license_by_key(conn, "lic_state")
    assert lic.is_active is False
    assert lic.tier == "pro"               # untouched
    assert lic.reviews_limit is None       # untouched
    assert lic.subscription_status == "past_due"
    assert lic.current_period_end == _PERIOD_END_ISO


def test_license_keys_migration_safe_on_existing_rows(_tmp_db, tmp_path):
    """The current_period_end + subscription_status migration applies cleanly
    forward and is safe on a pre-migration license_keys row (columns added,
    default NULL, existing data intact)."""
    # Arrange — build a DB whose license_keys lacks the two new columns,
    # simulating a row created before this migration shipped.
    import sqlite3
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE license_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL,
            key TEXT UNIQUE NOT NULL,
            tier TEXT NOT NULL DEFAULT 'free',
            reviews_used_this_month INTEGER DEFAULT 0,
            reviews_limit INTEGER DEFAULT 25,
            period_reset_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )"""
    )
    conn.execute(
        "INSERT INTO license_keys (workspace_id, key, tier) VALUES (1, 'legacy_key', 'indie')"
    )
    conn.commit()
    conn.close()

    # Act — run the migration forward against the legacy DB
    from database import _run_migrations
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _run_migrations(conn)
    conn.commit()

    # Assert — new columns exist, default NULL, legacy data preserved
    cols = {row[1] for row in conn.execute("PRAGMA table_info(license_keys)").fetchall()}
    assert "current_period_end" in cols
    assert "subscription_status" in cols
    row = conn.execute(
        "SELECT tier, current_period_end, subscription_status FROM license_keys WHERE key = 'legacy_key'"
    ).fetchone()
    conn.close()
    assert row["tier"] == "indie"                  # existing data intact
    assert row["current_period_end"] is None       # safe default
    assert row["subscription_status"] is None
