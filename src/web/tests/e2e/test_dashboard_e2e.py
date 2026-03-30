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

    # Onboarding page should show the license key
    assert page.locator("text=license key").first.is_visible()


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
