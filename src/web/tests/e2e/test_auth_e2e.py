"""E2E tests for authentication flows."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def test_landing_page_shows_auth_links(page, base_url):
    page.goto(base_url + "/")
    assert page.get_by_text("Log in").is_visible()
    assert page.get_by_text("Sign up free").first.is_visible()


def test_signup_flow(page, base_url):
    email = f"signup-{uuid.uuid4().hex[:8]}@test.com"

    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill("securepass1")
    page.locator("button[type='submit']").click()

    page.wait_for_url(f"**{'/onboarding'}")
    assert "/onboarding" in page.url


def test_signup_short_password_shows_error(page, base_url):
    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill("short@test.com")
    pw_input = page.locator("input[name='password']")
    pw_input.fill("abc")
    # Remove HTML5 minlength so the form submits and the server validates
    page.evaluate("document.querySelector('input[name=\"password\"]').removeAttribute('minlength')")
    page.locator("button[type='submit']").click()

    error = page.locator("div.bg-red-900\\/50")
    error.wait_for(state="visible", timeout=5000)
    assert "at least 8 characters" in error.text_content()


def test_signup_duplicate_email_shows_error(page, base_url):
    email = f"dup-{uuid.uuid4().hex[:8]}@test.com"
    password = "securepass1"

    # First signup
    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill(password)
    page.locator("button[type='submit']").click()
    page.wait_for_url(f"**{'/onboarding'}")

    # Clear cookies to sign out, then try again with same email
    page.context.clear_cookies()
    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill(password)
    page.locator("button[type='submit']").click()

    error = page.locator("div.bg-red-900\\/50")
    assert error.is_visible()
    assert "already exists" in error.text_content()


def test_login_flow(page, base_url):
    email = f"login-{uuid.uuid4().hex[:8]}@test.com"
    password = "securepass1"

    # Sign up first
    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill(password)
    page.locator("button[type='submit']").click()
    page.wait_for_url(f"**{'/onboarding'}")

    # Log out
    page.context.clear_cookies()

    # Log in
    page.goto(base_url + "/login")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill(password)
    page.locator("button[type='submit']").click()

    page.wait_for_url(f"**{'/dashboard'}")
    assert "/dashboard" in page.url


def test_login_wrong_password_shows_error(page, base_url):
    email = f"wrong-{uuid.uuid4().hex[:8]}@test.com"
    password = "securepass1"

    # Sign up first
    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill(password)
    page.locator("button[type='submit']").click()
    page.wait_for_url(f"**{'/onboarding'}")

    # Log out and try wrong password
    page.context.clear_cookies()
    page.goto(base_url + "/login")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill("wrongpass1")
    page.locator("button[type='submit']").click()

    error = page.locator("div.bg-red-900\\/50")
    assert error.is_visible()
    assert "Invalid email or password" in error.text_content()


def test_logout_redirects_to_landing(page, base_url):
    email = f"logout-{uuid.uuid4().hex[:8]}@test.com"

    # Sign up
    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill("securepass1")
    page.locator("button[type='submit']").click()
    page.wait_for_url(f"**{'/onboarding'}")

    # Logout
    page.goto(base_url + "/logout")

    page.wait_for_url(f"**{base_url}/")
    assert page.url.rstrip("/") == base_url.rstrip("/") or page.url == base_url + "/"
