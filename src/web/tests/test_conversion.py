"""Tests for Story [65] — Conversion analytics dashboard."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _signup(client: AsyncClient, email: str = "conv@test.com", ref: str = "") -> None:
    data = {"email": email, "password": "password1"}
    if ref:
        data["ref"] = ref
    resp = await client.post(
        "/signup",
        data=data,
        follow_redirects=False,
    )
    client.cookies.set("revue_session", resp.cookies.get("revue_session"))


# =====================================================================
# models.get_conversion_analytics unit tests
# =====================================================================

def test_conversion_analytics_no_users(_tmp_db):
    from database import get_db
    from models import get_conversion_analytics
    with get_db() as conn:
        data = get_conversion_analytics(conn, days=30)
    assert data["total_users"] == 0
    assert data["paid_users"] == 0
    assert data["conversion_rate"] == 0.0
    assert data["tier_breakdown"]["free"] == 0
    assert data["reviews_per_month_buckets"]["0"] == 0
    assert data["referral_sources"] == []
    assert data["signups_over_time"] == []


def test_conversion_analytics_tier_breakdown(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, get_conversion_analytics
    with get_db() as conn:
        uid1 = create_user(conn, "free@test.com", "h")
        create_workspace(conn, uid1, "ws")
        uid2 = create_user(conn, "indie@test.com", "h")
        create_workspace(conn, uid2, "ws")
        conn.execute("UPDATE users SET tier = 'indie' WHERE id = ?", (uid2,))
        uid3 = create_user(conn, "pro@test.com", "h")
        create_workspace(conn, uid3, "ws")
        conn.execute("UPDATE users SET tier = 'pro' WHERE id = ?", (uid3,))
    with get_db() as conn:
        data = get_conversion_analytics(conn, days=30)
    assert data["total_users"] == 3
    assert data["paid_users"] == 2
    assert data["conversion_rate"] == round((2 / 3) * 100, 1)
    assert data["tier_breakdown"]["free"] == 1
    assert data["tier_breakdown"]["indie"] == 1
    assert data["tier_breakdown"]["pro"] == 1


def test_conversion_analytics_review_buckets(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, create_review_run, get_conversion_analytics
    with get_db() as conn:
        # User A: 0 reviews
        uid_a = create_user(conn, "a@test.com", "h")
        ws_a = create_workspace(conn, uid_a, "ws")
        create_license_key(conn, ws_a, "lic_a")
        # User B: 3 reviews
        uid_b = create_user(conn, "b@test.com", "h")
        ws_b = create_workspace(conn, uid_b, "ws")
        lic_b = create_license_key(conn, ws_b, "lic_b")
        for _ in range(3):
            create_review_run(conn, lic_b)
        # User C: 10 reviews
        uid_c = create_user(conn, "c@test.com", "h")
        ws_c = create_workspace(conn, uid_c, "ws")
        lic_c = create_license_key(conn, ws_c, "lic_c")
        for _ in range(10):
            create_review_run(conn, lic_c)
    with get_db() as conn:
        data = get_conversion_analytics(conn, days=30)
    bk = data["reviews_per_month_buckets"]
    assert bk["0"] == 1      # user A
    assert bk["1-5"] == 1    # user B (3 reviews)
    assert bk["6-25"] == 1   # user C (10 reviews)
    assert bk["26-100"] == 0
    assert bk["100+"] == 0


def test_conversion_analytics_referral_sources(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, get_conversion_analytics
    with get_db() as conn:
        uid1 = create_user(conn, "a@test.com", "h", referral_source="github")
        create_workspace(conn, uid1, "ws")
        uid2 = create_user(conn, "b@test.com", "h", referral_source="github")
        create_workspace(conn, uid2, "ws")
        uid3 = create_user(conn, "c@test.com", "h")  # no referral → "direct"
        create_workspace(conn, uid3, "ws")
    with get_db() as conn:
        data = get_conversion_analytics(conn, days=30)
    sources = {s["source"]: s["count"] for s in data["referral_sources"]}
    assert sources["github"] == 2
    assert sources["direct"] == 1


# =====================================================================
# GET /conversion page
# =====================================================================

@pytest.mark.asyncio
async def test_conversion_redirects_when_unauthenticated(client: AsyncClient):
    resp = await client.get("/conversion", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_conversion_page_returns_200(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/conversion")
    assert resp.status_code == 200
    assert b"Conversion" in resp.content


# =====================================================================
# Referral source captured on signup
# =====================================================================

@pytest.mark.asyncio
async def test_referral_source_captured_on_signup(client: AsyncClient):
    # Signup with ?ref= via hidden form field
    await _signup(client, email="ref@test.com", ref="producthunt")
    from database import get_db
    from models import get_user_by_email
    with get_db() as conn:
        user = get_user_by_email(conn, "ref@test.com")
    assert user is not None
    assert user.referral_source == "producthunt"
