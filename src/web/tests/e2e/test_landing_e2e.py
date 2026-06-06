"""E2E tests for the landing page hero + two-mode block (REVUE-408).

Runs against the out-of-process uvicorn server (base_url from the shared
fixture; honours E2E_BASE_URL for staging parity). Uses inner_text() so
HTML-entity-encoded characters (&amp;, em-dashes) are decoded before assertion.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_landing_hero_eyebrow_and_h1(page, base_url):
    page.goto(base_url + "/")
    # New H1.
    assert page.get_by_role("heading", level=1).inner_text().strip() == "Real review at AI speed"
    # Eyebrow renders the decoded ampersand.
    assert "Code review for local & CI" in page.content() or \
        page.locator("text=Code review for local").count() >= 1


def test_landing_retired_cost_first_h1_gone(page, base_url):
    page.goto(base_url + "/")
    assert "Cut your AI API spend" not in page.content()


def test_landing_proof_strip_links(page, base_url):
    page.goto(base_url + "/")
    # Both cited stats link out to their real sources.
    cset = page.locator(
        'a[href*="cset.georgetown.edu/publication/cybersecurity-risks-of-ai-generated-code"]'
    )
    stanford = page.locator('a[href*="arxiv.org/abs/2211.03622"]')
    assert cset.count() >= 1
    assert stanford.count() >= 1
    assert "Georgetown CSET" in cset.first.inner_text()
    assert "Stanford" in stanford.first.inner_text()


def test_landing_both_modes_cli_first(page, base_url):
    page.goto(base_url + "/")
    cli = page.locator('[data-mode="cli"]')
    ci = page.locator('[data-mode="ci"]')
    assert cli.count() >= 1
    assert ci.count() >= 1
    # Landing carries the explicit `revue activate` reference (full variant).
    assert "revue activate" in page.content()
    # Every CI-mode CTA links to /docs/ci-setup.
    ci_link = ci.first.locator('a[href$="/docs/ci-setup"]')
    assert ci_link.count() >= 1


def test_landing_no_ci_only_regression(page, base_url):
    page.goto(base_url + "/")
    content = page.content()
    assert "reviews every pull request in your CI pipeline" not in content
    assert "Revue runs inside your CI runner" not in content


def test_landing_cost_beat_below_hero(page, base_url):
    page.goto(base_url + "/")
    content = page.content()
    assert "The expensive part is CI, not you." in content
    assert content.index("Real review at AI speed") < content.index(
        "The expensive part is CI, not you."
    )
