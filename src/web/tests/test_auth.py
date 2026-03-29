"""Tests for authentication routes."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_signup_creates_user_and_redirects(client: AsyncClient):
    resp = await client.post(
        "/signup",
        data={"email": "alice@example.com", "password": "securepass1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding"
    assert "revue_session" in resp.cookies


@pytest.mark.asyncio
async def test_signup_duplicate_email(client: AsyncClient):
    await client.post("/signup", data={"email": "dup@test.com", "password": "password1"})
    resp = await client.post(
        "/signup",
        data={"email": "dup@test.com", "password": "password2"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.content


@pytest.mark.asyncio
async def test_signup_short_password(client: AsyncClient):
    resp = await client.post(
        "/signup",
        data={"email": "short@test.com", "password": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"at least 8 characters" in resp.content


@pytest.mark.asyncio
async def test_signup_invalid_email(client: AsyncClient):
    resp = await client.post(
        "/signup",
        data={"email": "not-an-email", "password": "password1"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"valid email" in resp.content


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    await client.post("/signup", data={"email": "bob@test.com", "password": "password1"})
    resp = await client.post(
        "/login",
        data={"email": "bob@test.com", "password": "password1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    assert "revue_session" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    await client.post("/signup", data={"email": "carol@test.com", "password": "password1"})
    resp = await client.post(
        "/login",
        data={"email": "carol@test.com", "password": "wrongpass"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"Invalid email or password" in resp.content


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    resp = await client.post(
        "/login",
        data={"email": "nobody@test.com", "password": "password1"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"Invalid email or password" in resp.content


@pytest.mark.asyncio
async def test_logout_clears_session(client: AsyncClient):
    await client.post("/signup", data={"email": "dave@test.com", "password": "password1"})
    resp = await client.get("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client: AsyncClient):
    resp = await client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_signup_page_redirects_if_logged_in(client: AsyncClient):
    # Sign up (sets cookie)
    signup_resp = await client.post(
        "/signup",
        data={"email": "eve@test.com", "password": "password1"},
        follow_redirects=False,
    )
    # Use the session cookie
    client.cookies.set("revue_session", signup_resp.cookies.get("revue_session"))

    resp = await client.get("/signup", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
