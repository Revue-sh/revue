"""E2E tests for the CLI-first /activate paste-key fallback (REVUE-384).

The /activate page is unauthenticated. Step 1 is a single "Paste your licence
key" input validated client-side against ``^lic_[a-f0-9]{32}$``. On a valid
paste the reusable Activation Command-Box echoes ``revue activate <key>`` above
the fold; the legacy browser-mint form lives below the fold, collapsed inside a
native ``<details>`` ("Activate in browser (advanced)") — the only raw-JWT
surface on the page.

Run against the out-of-process uvicorn harness (REVUE-332). Local:
    cd src/web && python3 -m pytest tests/e2e
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

# A fixed viewport so "above/below the fold" assertions are deterministic.
VIEWPORT = {"width": 1280, "height": 800}

MINT_ENDPOINT = "/api/v2/licence/activate"


def _activate_page(page, base_url, *, suffix: str = ""):
    page.set_viewport_size(VIEWPORT)
    page.goto(base_url + "/activate" + suffix)
    page.wait_for_load_state("networkidle")
    return page


# ---------------------------------------------------------------------------
# TC1 — Unauth GET renders the paste input; no command-box, no mint form
# ---------------------------------------------------------------------------
def test_unauth_get_renders_paste_input(page, base_url):
    page = _activate_page(page, base_url)

    # Reachable without any session.
    assert page.url.endswith("/activate")

    paste = page.locator("#licence-key")
    assert paste.is_visible()

    # The command-box must not be shown before a valid paste.
    assert page.locator("#activation-command-box").count() == 0 or (
        page.locator("#activation-command-box").is_hidden()
    )
    # The mint form's inputs (the raw-JWT surface) stay collapsed/hidden; only
    # the <details> summary affordance is visible.
    assert page.locator("#mint-jwt[open]").count() == 0
    assert not page.locator("#mint-jwt input[name='key']").is_visible()


# ---------------------------------------------------------------------------
# TC2 — Valid paste echoes the command-box above the fold; no mint form above
# ---------------------------------------------------------------------------
def test_valid_paste_echoes_command_box_above_fold(page, base_url, seed_active_licence):
    key = seed_active_licence()
    page = _activate_page(page, base_url)

    page.locator("#licence-key").fill(key)
    page.locator("#licence-key").blur()

    box = page.locator("#activation-command-box")
    box.wait_for(state="visible", timeout=5000)

    # The visible command echoes `revue activate <pasted-key>`.
    cmd = page.locator("#activation-command-box .command-box-command")
    assert cmd.is_visible()
    assert cmd.text_content().strip() == f"revue activate {key}"

    # Command-box sits in the initial viewport (above the fold).
    box_box = box.bounding_box()
    assert box_box is not None
    assert box_box["y"] < VIEWPORT["height"], "command-box must be above the fold"

    # The mint form's inputs must not be visible above the fold (collapsed).
    assert not page.locator("#mint-jwt input[name='key']").is_visible()


# ---------------------------------------------------------------------------
# TC3 — Malformed paste: inline validation, no command-box, no mint request
# ---------------------------------------------------------------------------
def test_malformed_paste_shows_validation_no_request(page, base_url):
    page = _activate_page(page, base_url)

    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))

    page.locator("#licence-key").fill("lic_ZZZZ")
    page.locator("#licence-key").blur()

    # Inline validation message appears.
    err = page.locator("#licence-key-error")
    err.wait_for(state="visible", timeout=5000)
    assert err.text_content().strip() != ""

    # No command-box rendered/shown.
    assert page.locator("#activation-command-box").count() == 0 or (
        page.locator("#activation-command-box").is_hidden()
    )

    # No request was fired to the mint endpoint.
    assert not any(MINT_ENDPOINT in u for u in requests)


# ---------------------------------------------------------------------------
# TC4 — Empty paste: inline validation, no command-box, no mint request
# ---------------------------------------------------------------------------
def test_empty_paste_shows_validation_no_request(page, base_url):
    page = _activate_page(page, base_url)

    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))

    # Type then clear, then blur to trigger validation on empty.
    field = page.locator("#licence-key")
    field.fill("lic_")
    field.fill("")
    field.blur()

    err = page.locator("#licence-key-error")
    err.wait_for(state="visible", timeout=5000)
    assert err.text_content().strip() != ""

    assert page.locator("#activation-command-box").count() == 0 or (
        page.locator("#activation-command-box").is_hidden()
    )
    assert not any(MINT_ENDPOINT in u for u in requests)


# ---------------------------------------------------------------------------
# TC5 — Mint form collapsed below the fold; only raw-JWT element
# ---------------------------------------------------------------------------
def test_mint_form_collapsed_below_fold(page, base_url, seed_active_licence):
    key = seed_active_licence()
    page = _activate_page(page, base_url)

    page.locator("#licence-key").fill(key)
    page.locator("#licence-key").blur()
    page.locator("#activation-command-box").wait_for(state="visible", timeout=5000)

    details = page.locator("#mint-jwt")
    assert details.count() == 1, "mint form must be present"

    # Collapsed by default (native <details> without `open`).
    assert page.locator("#mint-jwt[open]").count() == 0

    # Its inputs are hidden while collapsed.
    assert not page.locator("#mint-jwt input[name='key']").is_visible()

    # Below the command-box (the recommended CLI path comes first).
    box_y = page.locator("#activation-command-box").bounding_box()["y"]
    details_y = details.bounding_box()["y"]
    assert details_y > box_y, "mint form must sit below the command-box"

    # The summary labels it as the advanced fallback.
    assert page.get_by_text("Activate in browser (advanced)").is_visible()

    # Expanding it reveals the only raw-JWT surface.
    page.get_by_text("Activate in browser (advanced)").click()
    assert page.locator("#mint-jwt input[name='key']").is_visible()


# ---------------------------------------------------------------------------
# TC6 — Copy flips to "Copied! ✓" then reverts after ~2s
# ---------------------------------------------------------------------------
def test_copy_flips_to_copied_then_reverts(page, base_url, seed_active_licence):
    # Clipboard permission so navigator.clipboard.writeText resolves.
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    key = seed_active_licence()
    page = _activate_page(page, base_url)

    page.locator("#licence-key").fill(key)
    page.locator("#licence-key").blur()
    page.locator("#activation-command-box").wait_for(state="visible", timeout=5000)

    copy_btn = page.locator("#activation-command-box .copy-btn")
    original = copy_btn.text_content().strip()
    copy_btn.click()

    # Flips to the checkmark label.
    page.wait_for_function(
        "() => document.querySelector('#activation-command-box .copy-btn')"
        ".textContent.trim() === 'Copied! ✓'",
        timeout=3000,
    )

    # Reverts after ~2s.
    page.wait_for_function(
        "(orig) => document.querySelector('#activation-command-box .copy-btn')"
        ".textContent.trim() === orig",
        arg=original,
        timeout=4000,
    )

    # The copied payload is the full command string (clipboard).
    clip = page.evaluate("() => navigator.clipboard.readText()")
    assert clip == f"revue activate {key}"


# ---------------------------------------------------------------------------
# TC7 — Masked state (lic_••••<last-4> visible; Copy yields the full key) is
# covered at the macro-render level in tests/test_command_box.py. The masked
# state is owned by the Account→Plan consumer (out of scope); /activate stays
# unauthenticated with no request input in its render context (AC1), so it has
# no masked surface to drive from a public page. See test_command_box.py
# ::test_masked_state_shows_dots_but_payload_is_full_key.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Edit-to-invalid hides the stale command-box and clears the payload
# (review finding #4 — displayed command and copy payload must never disagree
# with the field).
# ---------------------------------------------------------------------------
def test_editing_valid_key_to_invalid_hides_command_box(page, base_url, seed_active_licence):
    key = seed_active_licence()
    page = _activate_page(page, base_url)

    field = page.locator("#licence-key")
    field.fill(key)
    field.blur()
    page.locator("#activation-command-box").wait_for(state="visible", timeout=5000)

    # Edit to an invalid-but-non-empty value. The box must hide immediately
    # (on input) rather than keep showing the stale command.
    field.fill(key + "ZZZ")
    assert page.locator("#command-box-wrap").is_hidden()
    # The stale copy payload must be cleared so a later Copy cannot leak it.
    payload = page.locator("#activation-command-box").get_attribute("data-copy-payload")
    assert payload == ""


# ---------------------------------------------------------------------------
# AC: /activate is no longer a hero/marketing page
# ---------------------------------------------------------------------------
def test_activate_is_not_a_marketing_hero(page, base_url):
    page = _activate_page(page, base_url)
    # The old hero lede copy is gone.
    body = page.locator("body").text_content()
    assert "offline verification" not in body
