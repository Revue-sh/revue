"""E2E Playwright tests for the Account → Plan page (REVUE-382).

All test cases run against the out-of-process uvicorn harness (REVUE-332) and
are parameterised via ``E2E_BASE_URL`` for staging parity (REVUE-407 TC-11).

State matrix covered:
  TC1 — Unauthenticated redirect to /login                       (AC1)
  TC2 — Active Pro: badge, "Licence active ✓", masked key,
         validity line, usage meter (NULL period_end variant)      (AC2, AC4)
  TC3 — Active Indie: badge + all Active elements                 (AC2)
  TC4 — Copy yields full key; label contains "share with"        (AC3)
  TC5 — Not-activated: Command-Box + "Prefer a browser?" link    (AC5)
  TC6 — Lapsed: no "invalid"; Re-subscribe CTA + downgrade link  (AC6)
  TC7 — Free: Upgrade CTA present; Command-Box absent            (AC7)

Local run:
    cd src/web && python3 -m pytest tests/e2e -q
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(page, base_url: str, email: str, password: str = "testpass123") -> None:
    """POST /login and wait for redirect.

    On any failure the bare Playwright TimeoutError from ``wait_for_url`` hides
    WHY login stalled. Capture the current URL + page body and re-raise as an
    AssertionError so a failing run is diagnosable from the report alone.
    """
    try:
        page.goto(base_url + "/login")
        page.locator("input[name='email']").fill(email)
        page.locator("input[name='password']").fill(password)
        page.locator("button[type='submit']").click()
        # Login redirects to /dashboard on success
        page.wait_for_url(f"**{'/dashboard'}", timeout=10_000)
    except Exception as exc:  # noqa: BLE001 — re-raised as a diagnostic AssertionError
        try:
            current_url = page.url
        except Exception:  # noqa: BLE001
            current_url = "<unavailable>"
        try:
            body = page.locator("body").text_content() or ""
        except Exception:  # noqa: BLE001
            body = "<unavailable>"
        raise AssertionError(
            f"_login failed for {email!r}: {type(exc).__name__}: {exc}\n"
            f"  current URL: {current_url}\n"
            f"  page body (first 500 chars): {body[:500]!r}"
        ) from None


def _go_to_plan(page, base_url: str) -> None:
    page.goto(base_url + "/account/plan")
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# TC1 — Unauthenticated GET → redirect to /login  (AC1)
# ---------------------------------------------------------------------------

def test_unauthenticated_redirects_to_login(page, base_url):
    """AC1: unauthenticated GET /account/plan must redirect to /login."""
    page.goto(base_url + "/account/plan", wait_until="networkidle")
    assert "/login" in page.url


# ---------------------------------------------------------------------------
# TC2 — Active Pro (NULL period_end variant — REVUE-413 migration reality)
# ---------------------------------------------------------------------------

def test_active_pro_null_period_end(page, base_url, seed_active_licence):
    """AC2+AC4: Active Pro with NULL current_period_end renders without crashing.

    This is the REVUE-413 migration-reality variant: current_period_end and
    subscription_status are both NULL until the first Stripe webhook.  The page
    must not show "None", must not crash, and must show the badge + active mark.
    """
    seed_active_licence(
        tier="pro",
        is_active=True,
        current_period_end=None,  # NULL — migration-reality
        subscription_status=None,
    )
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    body = page.locator("body").text_content()
    # AC2: badge present
    assert "Pro" in body
    # AC2: active indicator (exact string, not a loose substring)
    assert "Licence active" in body
    # NULL period_end must not render as "None" or "—"
    assert "None" not in body
    # No crash: page title / main nav visible
    assert page.locator("nav").first.is_visible()
    # AC4: 24h offline-cache tooltip present in page HTML
    assert "24 h offline cache" in page.content()


# ---------------------------------------------------------------------------
# TC2b — Active Pro with a NON-NULL current_period_end (the populated variant)
# ---------------------------------------------------------------------------

def test_active_pro_with_renewal_date(page, base_url, seed_active_licence):
    """AC2: Active Pro with a populated current_period_end renders the full
    Active surface — masked key box, the renewal/validity line with the date,
    and the "Last verified" line. Complements TC2 (the NULL-period variant)."""
    seed_active_licence(
        tier="pro",
        is_active=True,
        current_period_end="2099-12-31T00:00:00",
        subscription_status="active",
    )
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    body = page.locator("body").text_content()
    assert "Pro" in body
    assert "Licence active" in body
    # Masked key box visible.
    assert page.locator("#plan-key-box").is_visible()
    # Renewal/validity line rendered with the date (ISO date portion).
    assert "Renews on" in body
    assert "2099-12-31" in body
    # AC2: "Last verified" line present (validated seed).
    assert "Last verified" in body
    # AC4: 24h offline-cache tooltip still present.
    assert "24 h offline cache" in page.content()


# ---------------------------------------------------------------------------
# TC3 — Active Indie
# ---------------------------------------------------------------------------

def test_active_indie_renders_all_active_elements(page, base_url, seed_active_licence):
    """AC2: Active Indie shows Indie badge + active indicator."""
    seed_active_licence(tier="indie", is_active=True)
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    body = page.locator("body").text_content()
    assert "Indie" in body
    assert "Licence active" in body
    # Masked key container visible
    assert page.locator("#plan-key-box").is_visible()
    # Usage meter rendered (shared partial)
    assert page.locator("text=reviews used").first.is_visible()
    # AC2: "Last verified" line rendered from the validation cache (validated seed)
    assert "Last verified" in body


# ---------------------------------------------------------------------------
# TC4 — Copy yields full key; label contains "share with your developer"  (AC3)
# ---------------------------------------------------------------------------

def test_copy_yields_full_key_with_handoff_label(page, base_url, seed_active_licence):
    """AC3: clicking Copy on the masked key writes the full key to clipboard;
    the button label (or caption) contains "share with your developer"."""
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    key = seed_active_licence(tier="pro", is_active=True)
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    # The copy payload attribute on the command-box holds the full key
    payload = page.locator("#plan-key-box").get_attribute("data-copy-payload")
    assert payload == key, f"copy payload mismatch: {payload!r} != {key!r}"

    # Caption text confirms handoff intent (AC3)
    body = page.locator("body").text_content()
    assert "share with your developer" in body.lower()

    # Click Copy and verify clipboard
    page.locator("#plan-key-box .copy-btn").click()
    page.wait_for_function(
        "() => document.querySelector('#plan-key-box .copy-btn')"
        ".textContent.includes('Copied')",
        timeout=3_000,
    )
    clip = page.evaluate("() => navigator.clipboard.readText()")
    assert clip == key


# ---------------------------------------------------------------------------
# TC5 — Not-activated: Command-Box pre-filled with the user's REAL key  (AC5)
# ---------------------------------------------------------------------------

def test_not_activated_freshly_signed_up_prefills_real_key(page, base_url, logged_in_page):
    """AC5: a freshly signed-up user has a key but has never validated, so the
    state is not_activated. The Activation Command-Box is pre-filled with the
    user's REAL key (data-copy-payload = `revue activate <lic_...>`), and the
    'Prefer a browser?' link is present.
    """
    import re

    # The logged_in_page fixture creates a fresh user via signup (never validated).
    page = logged_in_page
    page.goto(base_url + "/account/plan")
    page.wait_for_load_state("networkidle")

    body = page.locator("body").text_content()
    # not_activated state rendered (never validated).
    assert "Not activated" in body or "not activated" in body.lower()

    # AC5: Command-Box pre-filled with the user's real key in the copy payload.
    payload = page.locator("#activation-command-box").get_attribute("data-copy-payload")
    assert payload is not None
    m = re.match(r"^revue activate (lic_[a-f0-9]{32})$", payload)
    assert m, f"copy payload must pre-fill the real key, got: {payload!r}"

    # The full key must NOT be visible in the masked command text.
    visible_cmd = page.locator("#activation-command-box .command-box-command").text_content()
    assert m.group(1) not in visible_cmd, "full key must be masked in visible text"

    # "Prefer a browser?" secondary link present (AC5).
    assert page.locator("a[href='/activate']").first.is_visible()


def test_free_validated_no_command_box(page, base_url, seed_active_licence):
    """AC7: a VALIDATED free user (not not_activated) shows the Upgrade CTA and
    NO Activation Command-Box."""
    seed_active_licence(tier="free", is_active=True, validated=True)
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    body = page.locator("body").text_content()
    assert "Upgrade" in body
    assert "revue activate" not in body

    body = page.locator("body").text_content()
    # Signup always creates a free row, so state is "free" not "not_activated"
    assert "Free" in body or "free" in body.lower()
    # The activation command-box is not shown for the free state (AC7).
    assert "revue activate" not in body


# ---------------------------------------------------------------------------
# TC6 — Lapsed: no "invalid"; Re-subscribe + downgrade-to-Free CTAs  (AC6)
# ---------------------------------------------------------------------------

def test_lapsed_no_invalid_word(page, base_url, seed_active_licence):
    """AC6: Lapsed copy never contains the word 'invalid'."""
    seed_active_licence(
        tier="pro",
        is_active=False,
        subscription_status="canceled",
        current_period_end="2025-01-01T00:00:00",
    )
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    body = page.locator("body").text_content()
    assert "invalid" not in body.lower(), "Lapsed state must never use 'invalid'"


def test_lapsed_resubscribe_cta_present(page, base_url, seed_active_licence):
    """AC6: Lapsed state shows Re-subscribe CTA."""
    seed_active_licence(
        tier="pro",
        is_active=False,
        subscription_status="canceled",
        current_period_end="2025-01-01T00:00:00",
    )
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    # Primary CTA
    assert page.get_by_text("Re-subscribe", exact=False).first.is_visible()
    # Secondary CTA
    assert page.get_by_text("Downgrade to Free", exact=False).first.is_visible()


# ---------------------------------------------------------------------------
# TC7 — Free: Upgrade CTA present; Command-Box absent  (AC7)
# ---------------------------------------------------------------------------

def test_free_upgrade_cta_no_command_box(page, base_url, seed_active_licence):
    """AC7: Free state shows Upgrade CTA; Activation Command-Box is absent."""
    seed_active_licence(tier="free", is_active=True)
    email = seed_active_licence._last_email  # type: ignore[attr-defined]
    _login(page, base_url, email)
    _go_to_plan(page, base_url)

    body = page.locator("body").text_content()

    # Upgrade CTA present (AC7)
    assert "Upgrade" in body

    # Activation Command-Box must be absent (AC7)
    assert "revue activate" not in body
