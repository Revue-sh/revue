"""Tests for dashboard, onboarding, and usage_bar partial."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _signup_and_get_cookies(client: AsyncClient, email: str = "dash@test.com") -> dict:
    """Helper: sign up and return cookies dict for authenticated requests."""
    resp = await client.post(
        "/signup",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)
    return {"revue_session": cookie}


@pytest.mark.asyncio
async def test_dashboard_renders(client: AsyncClient):
    await _signup_and_get_cookies(client)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.content
    assert b"Free" in resp.content


@pytest.mark.asyncio
async def test_dashboard_shows_license_key(client: AsyncClient):
    await _signup_and_get_cookies(client)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert b"License Key" in resp.content
    assert b"lic_" in resp.content


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client: AsyncClient):
    resp = await client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_onboarding_renders_cli_hero(client: AsyncClient):
    """REVUE-361: onboarding leads with the Activation Command-Box hero
    pre-filled with the signed-up (free-tier) user's key."""
    await _signup_and_get_cookies(client)
    resp = await client.get("/onboarding")
    assert resp.status_code == 200
    assert b'id="activation-command-box"' in resp.content
    assert b"revue activate lic_" in resp.content


@pytest.mark.asyncio
async def test_onboarding_demotes_ci_to_linkout_card(client: AsyncClient):
    """REVUE-361 onboarding-refactor: the inline CI YAML and platform tabs are
    removed; CI is a compact card linking to /docs/ci-setup."""
    await _signup_and_get_cookies(client)
    resp = await client.get("/onboarding")
    body = resp.content
    # Inline CI scaffolding is gone.
    assert b"REVUE_LICENSE_KEY" not in body
    assert b".github/workflows/revue.yml" not in body
    assert b".gitlab-ci.yml" not in body
    assert b"switchTab" not in body
    # CI mode is a link-out to the consolidated CI-setup page.
    assert b"/docs/ci-setup" in body
    assert b"Reviewing in CI" in body


@pytest.mark.asyncio
async def test_onboarding_requires_auth(client: AsyncClient):
    resp = await client.get("/onboarding", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_usage_bar_partial(client: AsyncClient):
    await _signup_and_get_cookies(client)
    resp = await client.get("/partials/usage_bar")
    assert resp.status_code == 200
    assert b"reviews used" in resp.content


@pytest.mark.asyncio
async def test_usage_bar_requires_auth(client: AsyncClient):
    resp = await client.get("/partials/usage_bar")
    assert resp.status_code == 200
    assert resp.content == b""


@pytest.mark.asyncio
async def test_landing_page(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    # The landing hero is title-cased ("AI Code Review"); assert case-insensitively
    # so the smoke check survives marketing copy casing (REVUE-281 cost-messaging).
    assert b"ai code review" in resp.content.lower()


@pytest.mark.asyncio
async def test_landing_redirects_if_logged_in(client: AsyncClient):
    await _signup_and_get_cookies(client)
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
