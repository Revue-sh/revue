"""E2E tests for the CLI-first post-purchase activation handoff (REVUE-361).

Both /billing/success and /onboarding now lead with the shared Activation
Command-Box (``revue activate <key>``) as the hero, pre-filled with the
*authenticated* user's real licence key. CI mode is demoted to a compact
"Reviewing in CI? Set up your pipeline →" card linking to /docs/ci-setup; the
inline CI YAML that used to live on /onboarding is gone.

Run against the out-of-process uvicorn harness (REVUE-332). Local:
    cd src/web && python3 -m pytest tests/e2e/test_cli_first_activation_e2e.py

Staging parity (REVUE-409): these tests run against staging too. The
seed+cookie path no longer skips — the ``seed_user_with_licence`` and
``auth_cookie`` fixtures branch on ``E2E_BASE_URL``: on staging they resolve a
pre-provisioned account and establish the session via the real UI login (no
local DB, no shared SECRET_KEY required). The test bodies below are unchanged.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e

# Fixed viewport so DOM-order ("the command-box comes first") is deterministic.
VIEWPORT = {"width": 1280, "height": 900}

# The canonical licence-key shape the copy payload must always match.
KEY_RE = re.compile(r"^lic_[a-f0-9]{32}$")

# Stable DOM ids the templates assign to the two command-box instances.
HERO_ID = "activation-command-box"
SHARE_ID = "share-key-box"


def _authed(page, base_url, identity, auth_cookie, path):
    """Inject the seeded user's session cookie, then navigate to ``path``."""
    page.set_viewport_size(VIEWPORT)
    # A cookie needs an origin in the context; touch the host once first.
    page.goto(base_url + "/health")
    auth_cookie(page, identity)
    page.goto(base_url + path)
    page.wait_for_load_state("networkidle")
    return page


# ---------------------------------------------------------------------------
# TC-01 — /billing/success hero shows the seeded user's full key
# ---------------------------------------------------------------------------
def test_success_hero_shows_full_activate_command(
    page, base_url, seed_user_with_licence, auth_cookie
):
    identity = seed_user_with_licence()
    page = _authed(page, base_url, identity, auth_cookie, "/billing/success")

    # Not bounced to /login — the cookie round-tripped.
    assert page.url.endswith("/billing/success")

    hero = page.locator(f"#{HERO_ID}")
    assert hero.count() == 1
    cmd = hero.locator(".command-box-command").inner_text()
    assert cmd == f"revue activate {identity['key']}"
    assert KEY_RE.match(identity["key"])


# ---------------------------------------------------------------------------
# TC-02 — masked handoff line: masked display, Copy yields the full key
# ---------------------------------------------------------------------------
def test_success_handoff_masked_copy_yields_full_key(
    page, base_url, seed_user_with_licence, auth_cookie
):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    identity = seed_user_with_licence()
    page = _authed(page, base_url, identity, auth_cookie, "/billing/success")

    share = page.locator(f"#{SHARE_ID}")
    assert share.count() == 1

    # Visible text is masked (lic_••••<last4>), NOT the full key.
    visible = share.locator(".command-box-command").inner_text()
    assert "••••" in visible
    assert identity["key"] not in visible
    assert identity["key"][-4:] in visible

    # The labelled affordance for handing the key to a developer.
    assert page.get_by_text("Copy key to share with your developer").is_visible()

    # Copy still writes the FULL key. The label flip confirms the async
    # clipboard write resolved before we read it back.
    from playwright.sync_api import expect

    btn = share.locator(f"#{SHARE_ID}-copy")
    btn.click()
    expect(btn).to_have_text("Copied! ✓")
    clip = page.evaluate("navigator.clipboard.readText()")
    assert clip == identity["key"]


# ---------------------------------------------------------------------------
# TC-03 — compact CI card links to ci_setup; no REVUE_LICENSE_KEY YAML
# ---------------------------------------------------------------------------
def test_success_ci_card_links_out_no_inline_yaml(
    page, base_url, seed_user_with_licence, auth_cookie
):
    identity = seed_user_with_licence()
    page = _authed(page, base_url, identity, auth_cookie, "/billing/success")

    assert page.get_by_text(re.compile("Reviewing in CI")).is_visible()
    link = page.locator("a[href$='/docs/ci-setup']")
    assert link.count() >= 1

    # No inline CI YAML on the page — the secret name lives on /docs/ci-setup.
    assert "REVUE_LICENSE_KEY" not in page.content()


# ---------------------------------------------------------------------------
# TC-04 — /onboarding hero is the first major interactive element
# ---------------------------------------------------------------------------
def test_onboarding_hero_first_with_full_key(
    page, base_url, seed_user_with_licence, auth_cookie
):
    identity = seed_user_with_licence()
    page = _authed(page, base_url, identity, auth_cookie, "/onboarding")

    assert page.url.endswith("/onboarding")
    hero = page.locator(f"#{HERO_ID}")
    assert hero.count() == 1
    cmd = hero.locator(".command-box-command").inner_text()
    assert cmd == f"revue activate {identity['key']}"

    # The hero is the first command-box / copy affordance in DOM order.
    first_copy = page.locator(".copy-btn").first
    assert first_copy.evaluate(
        "el => el.closest('.command-box').id"
    ) == HERO_ID


# ---------------------------------------------------------------------------
# TC-05 — free-tier user sees a non-blank command-box with their key
# ---------------------------------------------------------------------------
def test_onboarding_free_tier_renders_key(
    page, base_url, seed_user_with_licence, auth_cookie
):
    identity = seed_user_with_licence(tier="free")
    page = _authed(page, base_url, identity, auth_cookie, "/onboarding")

    hero = page.locator(f"#{HERO_ID}")
    cmd = hero.locator(".command-box-command").inner_text()
    assert cmd == f"revue activate {identity['key']}"
    # Never a blank / placeholder box for a signed-up user.
    assert identity["key"] in cmd
    assert "No key" not in page.content()


# ---------------------------------------------------------------------------
# TC-06 — onboarding CI card links out; no inline GitHub/GitLab YAML remains
# ---------------------------------------------------------------------------
def test_onboarding_ci_card_no_inline_yaml(
    page, base_url, seed_user_with_licence, auth_cookie
):
    identity = seed_user_with_licence()
    page = _authed(page, base_url, identity, auth_cookie, "/onboarding")

    link = page.locator("a[href$='/docs/ci-setup']")
    assert link.count() >= 1

    content = page.content()
    # The retired inline CI scaffolding must be gone.
    assert "REVUE_LICENSE_KEY" not in content
    assert ".github/workflows/revue.yml" not in content
    assert ".gitlab-ci.yml" not in content
    assert "switchTab" not in content


# ---------------------------------------------------------------------------
# TC-07 — Copy flips to "Copied! ✓" then reverts (~2s)
# ---------------------------------------------------------------------------
def test_copy_label_flips_then_reverts(
    page, base_url, seed_user_with_licence, auth_cookie
):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    identity = seed_user_with_licence()
    page = _authed(page, base_url, identity, auth_cookie, "/billing/success")

    from playwright.sync_api import expect

    btn = page.locator(f"#{HERO_ID}-copy")
    expect(btn).to_have_text("Copy")
    btn.click()
    # The flip happens in copyToClipboard's async clipboard .then() — auto-retry.
    expect(btn).to_have_text("Copied! ✓")

    # [copy-command] AC: the hero Copy writes the FULL `revue activate <key>`
    # command (not just the bare key) to the clipboard.
    clip = page.evaluate("navigator.clipboard.readText()")
    assert clip == f"revue activate {identity['key']}"

    # Reverts after the 2s timer owned by copyToClipboard.
    expect(btn).to_have_text("Copy", timeout=4000)


# ---------------------------------------------------------------------------
# TC-08 — "Two ways to use Revue" framing; CI card after the command-box in DOM
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/billing/success", "/onboarding"])
def test_two_ways_framing_ci_card_after_hero(
    page, base_url, seed_user_with_licence, auth_cookie, path
):
    identity = seed_user_with_licence()
    page = _authed(page, base_url, identity, auth_cookie, path)

    assert page.get_by_text(re.compile("Two ways to use Revue")).is_visible()

    # The CI card appears AFTER the hero command-box in document order.
    order = page.evaluate(
        """(heroId) => {
            const hero = document.getElementById(heroId);
            const card = document.querySelector("a[href$='/docs/ci-setup']");
            if (!hero || !card) return null;
            // 4 == DOCUMENT_POSITION_FOLLOWING: card follows hero.
            return !!(hero.compareDocumentPosition(card) & 4);
        }""",
        HERO_ID,
    )
    assert order is True
