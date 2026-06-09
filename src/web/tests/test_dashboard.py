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
async def test_dashboard_renders_both_modes(client: AsyncClient):
    """REVUE-408: the dashboard surfaces both ways to use Revue — CLI/local
    (primary) and CI (team automation) — via the shared two-mode partial."""
    await _signup_and_get_cookies(client, email="twomode-dash@test.com")
    resp = await client.get("/dashboard")
    html = resp.content.decode()
    assert 'data-mode="cli"' in html
    assert 'data-mode="ci"' in html
    # CLI block describes the local/pre-commit mode.
    assert "before you commit" in html.lower()
    # CI reference links to the canonical CI setup page.
    assert "/docs/ci-setup" in html
    # CLI (primary) is rendered before CI.
    assert html.index('data-mode="cli"') < html.index('data-mode="ci"')


@pytest.mark.asyncio
async def test_onboarding_renders_cli_hero(client: AsyncClient):
    """REVUE-428: onboarding Step 1 shows a single personalised curl command
    that installs and activates in one step."""
    await _signup_and_get_cookies(client)
    resp = await client.get("/onboarding")
    assert resp.status_code == 200
    assert b'id="install-command-box"' in resp.content
    assert b"curl -fsSL" in resp.content
    assert b"--key lic_" in resp.content


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
    assert b"CI pipeline" in body


@pytest.mark.asyncio
async def test_onboarding_requires_auth(client: AsyncClient):
    resp = await client.get("/onboarding", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_onboarding_claude_code_only_disclaimer_present(client: AsyncClient):
    """REVUE-366 AC2/TC2: install page explicitly names Claude Code as the only
    supported AI client; Cursor/Windsurf are not presented as selectable options."""
    await _signup_and_get_cookies(client)
    resp = await client.get("/onboarding")
    assert resp.status_code == 200
    html = resp.content.decode()
    # Claude Code is named as the supported client.
    assert "Claude Code" in html
    # Cursor and Windsurf must not be presented as selectable/clickable options
    # (they may appear in a "coming soon" disclaimer, but not in a button, link,
    # or form element that would prompt a Cursor/Windsurf user to install).
    assert 'href="#cursor"' not in html
    assert 'href="#windsurf"' not in html
    assert 'data-client="cursor"' not in html
    assert 'data-client="windsurf"' not in html
    # The disclaimer must clarify what IS supported.
    assert "supported" in html.lower()


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
