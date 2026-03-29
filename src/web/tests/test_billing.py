"""Tests for Story [64] — Stripe billing integration."""
from __future__ import annotations

import json
import os
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
    mock_session = MagicMock()
    mock_session.url = "https://billing.stripe.com/session/test"

    with patch("stripe.billing_portal.Session.create", return_value=mock_session) as mock_create:
        from billing import create_billing_portal_session
        url = create_billing_portal_session("cus_test123")

    assert url == "https://billing.stripe.com/session/test"
    mock_create.assert_called_once_with(
        customer="cus_test123",
        return_url="https://revue-io.fly.dev/dashboard",
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
async def test_billing_page_shows_prices(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/billing")
    assert b"$9" in resp.content
    assert b"$29" in resp.content
    assert b"$59" in resp.content
    assert b"$149" in resp.content


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
    # This is a sync check on the template content
    with open("templates/dashboard.html") as f:
        content = f.read()
    assert 'href="/billing"' in content
    assert "Coming soon" not in content
