"""E2E tests for the consolidated /docs/ci-setup page (REVUE-407).

Covers TC-1..TC-11 from the ticket. The page is the single authoritative
CI-setup source, replacing the per-platform quickstart-* slugs.

Run locally:
    python3 -m pytest src/web/tests/e2e/test_ci_setup_page.py

Staging parity (TC-11): set E2E_BASE_URL to a deployed instance and the whole
suite runs against it instead of a local subprocess.
"""
from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.e2e

CI_SETUP_PATH = "/docs/ci-setup"

# The unified provider-key secret/variable name (AC6). One name, all platforms.
UNIFIED_PROVIDER_KEY = "AI_API_KEY"

PLATFORMS = ("Bitbucket Pipelines", "GitHub Actions", "GitLab CI")

LEGACY_SLUGS = (
    "/docs/quickstart-github",
    "/docs/quickstart-gitlab",
    "/docs/quickstart-bitbucket",
)


def _get(base_url: str, path: str, follow_redirects: bool = True) -> httpx.Response:
    return httpx.get(base_url + path, follow_redirects=follow_redirects, timeout=10.0)


def test_tc1_smoke_returns_200_with_ci_mode(base_url):
    """TC-1: GET /docs/ci-setup returns 200; body contains 'CI mode'."""
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    assert "CI mode" in resp.text


def test_tc2_two_mode_framing_cli_crosslink(base_url):
    """TC-2: body contains the CLI cross-link ('revue activate') and a link to CLI/activation docs."""
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    assert "revue activate" in resp.text
    # A visible cross-link to the CLI / activation docs must be present.
    assert "/docs/activate" in resp.text or "/activate" in resp.text


@pytest.mark.parametrize("platform", PLATFORMS)
def test_tc345_platform_sections_present(base_url, platform):
    """TC-3/4/5: each platform section is present with REVUE_LICENSE_KEY in scope."""
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    assert platform in resp.text
    assert "REVUE_LICENSE_KEY" in resp.text


def test_tc6_unified_provider_key_name(base_url):
    """TC-6: each section shows the unified provider-key secret name (AI_API_KEY)."""
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    # The unified key must appear at least once per platform (3 sections).
    assert resp.text.count(UNIFIED_PROVIDER_KEY) >= 3


def test_tc7_yaml_snippets_have_pipeline_keyword(base_url):
    """TC-7: each platform has a <pre>/<code> block running the CI review command.

    The CI review binary is ``revue-ci review`` (the revue-ci package); the
    plain ``revue`` command is CLI-mode activation only.
    """
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    assert "<pre" in resp.text
    assert "<code" in resp.text
    # One review invocation per platform snippet.
    assert resp.text.count("revue-ci review") >= 3


def test_tc8_copy_buttons_invoke_copy_to_clipboard(base_url):
    """TC-8: each platform has an element invoking copyToClipboard."""
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    assert resp.text.count("copyToClipboard") >= 3


def test_tc9_unauthenticated_returns_200(base_url):
    """TC-9: GET with no session returns 200 (not 401/403/302)."""
    resp = _get(base_url, CI_SETUP_PATH, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("slug", LEGACY_SLUGS)
def test_tc10_legacy_quickstart_redirects(base_url, slug):
    """TC-10: each legacy /docs/quickstart-* redirects (301/302) to /docs/ci-setup,
    OR renders a clear pointer to it."""
    resp = _get(base_url, slug, follow_redirects=False)
    if resp.status_code in (301, 302):
        location = resp.headers.get("location", "")
        assert CI_SETUP_PATH in location
    else:
        # Demoted-but-rendered: must point at the consolidated page.
        assert resp.status_code == 200
        assert CI_SETUP_PATH in resp.text


def test_tc345_each_platform_has_license_key_in_its_section(base_url):
    """TC-3/4/5 stronger: REVUE_LICENSE_KEY appears once per platform section (>=3)."""
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    assert resp.text.count("REVUE_LICENSE_KEY") >= 3


def test_tc11_staging_parity(base_url):
    """TC-11: with E2E_BASE_URL set, the smoke + core checks pass against staging.

    Skipped when E2E_BASE_URL is unset (local/CI pre-merge): staging will not have
    the page until 407 deploys. Post-merge staging validation re-runs this with
    E2E_BASE_URL set.
    """
    if not os.environ.get("E2E_BASE_URL"):
        pytest.skip("E2E_BASE_URL unset — staging parity is a post-merge validation step")
    resp = _get(base_url, CI_SETUP_PATH)
    assert resp.status_code == 200
    assert "CI mode" in resp.text
    for platform in PLATFORMS:
        assert platform in resp.text
    assert resp.text.count(UNIFIED_PROVIDER_KEY) >= 3
