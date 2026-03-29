"""Tests for license validation API and usage tracking."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_license_key_format(client: AsyncClient):
    """License keys generated on signup start with lic_ and are 36 chars."""
    await client.post("/signup", data={"email": "lic1@test.com", "password": "password1"})
    from database import get_db
    from models import get_user_by_email, get_license_for_user

    with get_db() as conn:
        user = get_user_by_email(conn, "lic1@test.com")
        lic = get_license_for_user(conn, user.id)
    assert lic is not None
    assert lic.key.startswith("lic_")
    assert len(lic.key) == 36  # "lic_" + 32 hex chars


@pytest.mark.asyncio
async def test_validate_valid_key(client: AsyncClient):
    await client.post("/signup", data={"email": "v1@test.com", "password": "password1"})
    from database import get_db
    from models import get_user_by_email, get_license_for_user

    with get_db() as conn:
        user = get_user_by_email(conn, "v1@test.com")
        lic = get_license_for_user(conn, user.id)

    resp = await client.post("/api/license/validate", json={"key": lic.key, "repo_id": "org/repo", "ci_run_id": "123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["tier"] == "free"
    assert "orchestrator" in data["agents_allowed"]
    assert "code-quality-expert" in data["agents_allowed"]
    assert data["reviews_left"] == 25


@pytest.mark.asyncio
async def test_validate_invalid_key(client: AsyncClient):
    resp = await client.post("/api/license/validate", json={"key": "lic_bogus", "repo_id": "", "ci_run_id": ""})
    assert resp.status_code == 401
    data = resp.json()
    assert data["valid"] is False
    assert "Invalid license key" in data["message"]


@pytest.mark.asyncio
async def test_validate_limit_reached(client: AsyncClient):
    """When reviews_used >= reviews_limit, validate returns limit-reached error."""
    await client.post("/signup", data={"email": "lim@test.com", "password": "password1"})
    from database import get_db
    from models import get_user_by_email, get_license_for_user

    with get_db() as conn:
        user = get_user_by_email(conn, "lim@test.com")
        lic = get_license_for_user(conn, user.id)
        # Exhaust the limit
        conn.execute(
            "UPDATE license_keys SET reviews_used_this_month = ? WHERE id = ?",
            (lic.reviews_limit, lic.id),
        )

    resp = await client.post("/api/license/validate", json={"key": lic.key})
    data = resp.json()
    assert data["valid"] is False
    assert "Review limit reached" in data["message"]


@pytest.mark.asyncio
async def test_usage_track(client: AsyncClient):
    await client.post("/signup", data={"email": "trk@test.com", "password": "password1"})
    from database import get_db
    from models import get_user_by_email, get_license_for_user

    with get_db() as conn:
        user = get_user_by_email(conn, "trk@test.com")
        lic = get_license_for_user(conn, user.id)

    resp = await client.post("/api/usage/track", json={
        "key": lic.key,
        "repo_id": "org/repo",
        "agents_used": ["orchestrator", "code-quality-expert"],
        "duration_ms": 1500,
    })
    assert resp.status_code == 204

    # Verify counter incremented
    with get_db() as conn:
        lic2 = get_license_for_user(conn, user.id)
    assert lic2.reviews_used_this_month == 1


@pytest.mark.asyncio
async def test_usage_track_invalid_key(client: AsyncClient):
    resp = await client.post("/api/usage/track", json={"key": "lic_nope"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_monthly_reset(client: AsyncClient):
    """Validate resets the counter when period_reset_at is in the past."""
    await client.post("/signup", data={"email": "rst@test.com", "password": "password1"})
    from database import get_db
    from models import get_user_by_email, get_license_for_user

    with get_db() as conn:
        user = get_user_by_email(conn, "rst@test.com")
        lic = get_license_for_user(conn, user.id)
        # Simulate: used 20 reviews, reset time in the past
        past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=35)).isoformat()
        conn.execute(
            "UPDATE license_keys SET reviews_used_this_month = 20, period_reset_at = ? WHERE id = ?",
            (past, lic.id),
        )

    resp = await client.post("/api/license/validate", json={"key": lic.key})
    data = resp.json()
    assert data["valid"] is True
    assert data["reviews_left"] == 25  # reset to 0 used, limit 25
