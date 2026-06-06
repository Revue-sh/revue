"""Tests for authentication routes."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from auth import (
    SESSION_COOKIE_BASE,
    cookie_secure,
    host_prefixed,
    session_cookie_name,
)


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


# =====================================================================
# Cookie hardening — __Host- prefix + Secure (REVUE-418 round 2)
# =====================================================================
# Default (insecure) mode is exercised by every test above: the session cookie
# is the plain ``revue_session`` name with no Secure flag. These tests pin the
# secure-mode behaviour with ``COOKIE_SECURE`` forced on.

def test_cookie_secure_defaults_off(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert cookie_secure() is False
    assert session_cookie_name() == SESSION_COOKIE_BASE  # plain name in dev


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on", "TRUE", "On"])
def test_cookie_secure_on_for_truthy_flag(monkeypatch, flag):
    monkeypatch.setenv("COOKIE_SECURE", flag)
    assert cookie_secure() is True
    assert session_cookie_name() == f"__Host-{SESSION_COOKIE_BASE}"
    assert host_prefixed("anything") == "__Host-anything"


@pytest.mark.parametrize("flag", ["0", "false", "no", "off", "", "  "])
def test_cookie_secure_off_for_falsey_flag(monkeypatch, flag):
    monkeypatch.setenv("COOKIE_SECURE", flag)
    assert cookie_secure() is False
    assert session_cookie_name() == SESSION_COOKIE_BASE


def _session_set_cookie_header(set_cookie_values: list[str]) -> str:
    """Return the single Set-Cookie line that carries the session cookie."""
    name = session_cookie_name()
    matches = [v for v in set_cookie_values if v.startswith(f"{name}=")]
    assert matches, f"no Set-Cookie for {name!r} in {set_cookie_values!r}"
    return matches[0]


@pytest.mark.asyncio
async def test_session_cookie_insecure_mode_is_plain_no_secure(client: AsyncClient, monkeypatch):
    """Default mode: session cookie uses the plain name and is NOT Secure."""
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    resp = await client.post(
        "/signup",
        data={"email": "plain-cookie@test.com", "password": "password1"},
        follow_redirects=False,
    )
    header = _session_set_cookie_header(resp.headers.get_list("set-cookie"))
    assert header.startswith("revue_session=")
    assert "Secure" not in header
    assert "__Host-" not in header


@pytest.mark.asyncio
async def test_session_cookie_secure_mode_is_host_prefixed_secure(monkeypatch):
    """Secure mode: session cookie is ``__Host-`` prefixed, Secure, Path=/, and
    has NO Domain (the browser requirement for the ``__Host-`` prefix)."""
    monkeypatch.setenv("COOKIE_SECURE", "1")
    from auth import reset_serializer
    reset_serializer()
    from main import app
    transport = ASGITransport(app=app)
    # https base_url so httpx will accept + echo the Secure cookie.
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        # GET first to obtain a CSRF token (also __Host- in secure mode).
        from csrf import CSRF_FORM_FIELD, csrf_cookie_name
        await ac.get("/signup")
        csrf = ac.cookies.get(csrf_cookie_name())
        resp = await ac.post(
            "/signup",
            data={
                "email": "secure-cookie@test.com",
                "password": "password1",
                CSRF_FORM_FIELD: csrf,
            },
            follow_redirects=False,
        )
    headers = resp.headers.get_list("set-cookie")
    session_lines = [h for h in headers if h.startswith("__Host-revue_session=")]
    assert session_lines, f"expected __Host- session cookie, got {headers!r}"
    line = session_lines[0]
    assert "Secure" in line
    assert "Path=/" in line
    assert "Domain=" not in line  # __Host- forbids Domain
    assert "HttpOnly" in line  # session stays httponly
    # Guard against the removed-alias footgun: in secure mode NO code path may
    # set the plain insecure session cookie name.
    assert not any(h.startswith("revue_session=") for h in headers), (
        f"secure mode must NOT emit a plain revue_session cookie, got {headers!r}"
    )


@pytest.mark.asyncio
async def test_session_round_trip_in_secure_mode(monkeypatch):
    """Login → authenticated read round-trips in secure mode over https."""
    monkeypatch.setenv("COOKIE_SECURE", "1")
    from auth import reset_serializer
    reset_serializer()
    from csrf import CSRF_FORM_FIELD, csrf_cookie_name
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        await ac.get("/signup")
        csrf = ac.cookies.get(csrf_cookie_name())
        signup = await ac.post(
            "/signup",
            data={
                "email": "rt-secure@test.com",
                "password": "password1",
                CSRF_FORM_FIELD: csrf,
            },
            follow_redirects=False,
        )
        assert signup.status_code == 303
        # The __Host- session cookie is now in the jar; a protected page must
        # recognise the session (no redirect to /login).
        dash = await ac.get("/dashboard", follow_redirects=False)
        assert dash.status_code == 200, "secure-mode session must round-trip"


@pytest.mark.asyncio
async def test_logout_clears_host_prefixed_session_in_secure_mode(monkeypatch):
    """Logout must clear the ``__Host-`` session (delete uses the same name)."""
    monkeypatch.setenv("COOKIE_SECURE", "1")
    from auth import reset_serializer
    reset_serializer()
    from csrf import CSRF_FORM_FIELD, csrf_cookie_name
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        await ac.get("/signup")
        csrf = ac.cookies.get(csrf_cookie_name())
        await ac.post(
            "/signup",
            data={
                "email": "logout-secure@test.com",
                "password": "password1",
                CSRF_FORM_FIELD: csrf,
            },
            follow_redirects=False,
        )
        resp = await ac.get("/logout", follow_redirects=False)
    headers = resp.headers.get_list("set-cookie")
    # The delete sets an expiring cookie under the __Host- name.
    cleared = [h for h in headers if h.startswith("__Host-revue_session=")]
    assert cleared, f"logout must clear the __Host- session cookie, got {headers!r}"
