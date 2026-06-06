"""E2E tests for dashboard and authenticated pages."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_dashboard_redirects_when_unauthenticated(page, base_url):
    page.goto(base_url + "/dashboard")
    page.wait_for_url(f"**{'/login'}")
    assert "/login" in page.url


def test_dashboard_shows_license_key(logged_in_page, base_url):
    logged_in_page.goto(base_url + "/dashboard")
    page = logged_in_page

    # License key section should be visible
    assert page.locator("text=License").first.is_visible()


def test_dashboard_shows_tier_label(logged_in_page, base_url):
    logged_in_page.goto(base_url + "/dashboard")
    page = logged_in_page

    assert page.get_by_text("Free").first.is_visible()


def test_onboarding_shows_license_key(logged_in_page, base_url):
    logged_in_page.goto(base_url + "/onboarding")
    page = logged_in_page

    # REVUE-361: onboarding now leads with the Activation Command-Box hero,
    # which renders the user's key inside `revue activate <key>`.
    hero = page.locator("#activation-command-box")
    assert hero.count() == 1
    assert "revue activate lic_" in hero.locator(".command-box-command").inner_text()


def test_runs_page_loads(logged_in_page, base_url):
    logged_in_page.goto(base_url + "/runs")
    page = logged_in_page

    page.wait_for_load_state("networkidle")
    assert page.url.endswith("/runs")


def test_analytics_page_loads(logged_in_page, base_url):
    logged_in_page.goto(base_url + "/analytics")
    page = logged_in_page

    page.wait_for_load_state("networkidle")
    assert page.url.endswith("/analytics")



def test_conversion_page_loads(logged_in_page, base_url):
    logged_in_page.goto(base_url + "/conversion")
    page = logged_in_page

    page.wait_for_load_state("networkidle")
    assert page.locator("h1").is_visible()
