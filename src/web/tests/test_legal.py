"""Tests for REVUE-357 — Terms of Service + Privacy Policy pages.

AC1: /terms live with complete ToS.
AC2: /privacy live with complete Privacy Policy.
AC3: both linked from footer + activate flow.
AC4: live URLs for Stripe (operational; not code-tested).
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# =====================================================================
# Routing — AC1, AC2
# =====================================================================

@pytest.mark.asyncio
async def test_terms_page_loads(client: AsyncClient):
    resp = await client.get("/terms")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_privacy_page_loads(client: AsyncClient):
    resp = await client.get("/privacy")
    assert resp.status_code == 200


# =====================================================================
# Content completeness — AC1 (ToS must cover the key clauses)
# =====================================================================

@pytest.mark.asyncio
async def test_terms_covers_required_clauses(client: AsyncClient):
    resp = await client.get("/terms")
    body = resp.content
    assert b"Terms of Service" in body
    assert b"Limitation of Liability" in body
    assert b"Governing Law" in body
    # Payment / Stripe-as-processor and acceptable use are mandatory for a paid SaaS ToS.
    assert b"Stripe" in body
    assert b"Acceptable Use" in body
    # Legal contact must be the dedicated address.
    assert b"legal@revue.sh" in body


@pytest.mark.asyncio
async def test_terms_explains_no_refunds_and_cancel_at_period_end(client: AsyncClient):
    """Terms must explain cancellation timing for monthly and annual plans."""
    resp = await client.get("/terms")
    body = resp.content
    assert b"All subscription charges are final and non-refundable" in body
    assert b"at our discretion" in body
    assert b"end of your current billing period" in body
    assert b"monthly subscription" in body
    assert b"annual subscription" in body
    assert b"next billing cycle" in body


# =====================================================================
# Content completeness — AC2 (Privacy Policy must enumerate real data flows)
# =====================================================================

@pytest.mark.asyncio
async def test_privacy_covers_required_sections(client: AsyncClient):
    resp = await client.get("/privacy")
    body = resp.content
    assert b"Privacy Policy" in body
    # Named processors that genuinely handle user data.
    assert b"Stripe" in body
    # GDPR user rights must be present.
    assert b"Your Rights" in body
    assert b"legal@revue.sh" in body


@pytest.mark.asyncio
async def test_privacy_states_local_no_source_boundary(client: AsyncClient):
    """The local-execution boundary (we never receive customer source) is both a
    privacy fact and a differentiator — it must be stated explicitly. Asserts the
    concept, not a specific command token (which has been ambiguous across docs)."""
    resp = await client.get("/privacy")
    body = resp.content
    assert b"never leaves your machine" in body
    assert b"run Revue locally" in body
    # The ambiguous /revue-local command token must NOT leak into customer-facing legal copy.
    assert b"/revue-local" not in body


@pytest.mark.asyncio
async def test_legal_pages_use_mailto_links_for_contact(client: AsyncClient):
    """legal@revue.sh must be a clickable mailto link, not bare text."""
    for path in ("/terms", "/privacy"):
        resp = await client.get(path)
        assert b'href="mailto:legal@revue.sh"' in resp.content, f"{path} missing mailto link"


@pytest.mark.asyncio
async def test_terms_uk_jurisdiction_and_entity(client: AsyncClient):
    """Terms must identify the current operator, not a pending company."""
    resp = await client.get("/terms")
    body = resp.content
    assert b"UK sole trader trading as Revue" in body
    assert b"future company registration" in body
    assert b"Token Labs Ltd" not in body
    assert b"PENDING REGISTRATION" not in body
    assert b"England &amp; Wales" in body or b"England & Wales" in body
    assert b"Delaware" not in body
    assert b"Revue Inc." not in body


@pytest.mark.asyncio
async def test_privacy_identifies_current_data_controller(client: AsyncClient):
    """Privacy policy must name the active controller before incorporation."""
    resp = await client.get("/privacy")
    body = resp.content
    assert b"UK sole trader trading as Revue" in body
    assert b"data controller" in body
    assert b"future company registration" in body
    assert b"Token Labs Ltd" not in body
    assert b"PENDING REGISTRATION" not in body


@pytest.mark.asyncio
async def test_privacy_drops_inference_provider_names(client: AsyncClient):
    """Per product decision, specific AI inference vendors are not named in the policy."""
    resp = await client.get("/privacy")
    body = resp.content
    assert b"OpenRouter" not in body
    assert b"DeepSeek" not in body


# =====================================================================
# Markdown rendered to HTML — Option A render path
# =====================================================================

@pytest.mark.asyncio
async def test_legal_pages_render_markdown_as_html(client: AsyncClient):
    for path in ("/terms", "/privacy"):
        resp = await client.get(path)
        assert b"<h1" in resp.content or b"<h2" in resp.content, f"{path} not rendered as HTML"


# =====================================================================
# Footer links on all main pages — AC3 / TC3
# =====================================================================

@pytest.mark.asyncio
async def test_footer_links_present_on_main_pages(client: AsyncClient):
    # Public pages that must carry the legal footer links.
    for path in ("/", "/activate", "/docs/ci-setup", "/login", "/signup"):
        resp = await client.get(path, follow_redirects=True)
        assert b"/terms" in resp.content, f"missing /terms link on {path}"
        assert b"/privacy" in resp.content, f"missing /privacy link on {path}"


# =====================================================================
# Single footer (no duplicate from base.html + own include)
# =====================================================================

@pytest.mark.asyncio
async def test_legal_lists_render_as_html_lists_not_runon_paragraphs(client: AsyncClient):
    """A bulleted list directly under a colon-intro line with no blank line
    collapses into a run-on paragraph in Python-Markdown. Guard against that
    regression: every legal page must render real <ul> lists, and the known
    collapse artefacts must not appear as inline text."""
    for path in ("/terms", "/privacy"):
        resp = await client.get(path)
        body = resp.content
        assert b"<ul>" in body, f"{path} has no rendered list — markdown lists collapsed"
        # Collapse artefacts: a dash bullet rendered inline inside a paragraph.
        assert b"if you: -" not in body, f"{path} has a collapsed list"
        assert b"rights to: -" not in body, f"{path} has a collapsed list"


@pytest.mark.asyncio
async def test_legal_pages_render_footer_exactly_once(client: AsyncClient):
    """legal.html extends base.html (which renders the shared footer); it must
    not also include the footer itself, or the page shows two stacked footers."""
    for path in ("/terms", "/privacy"):
        resp = await client.get(path)
        assert resp.content.count(b"<footer") == 1, f"{path} has duplicate footer"


# =====================================================================
# Activate flow exposes both pre-submission — AC3 / TC4
# =====================================================================

@pytest.mark.asyncio
async def test_activate_exposes_terms_and_privacy_before_submit(client: AsyncClient):
    resp = await client.get("/activate")
    body = resp.content
    assert b"/terms" in body
    assert b"/privacy" in body
    # A visible consent statement, not just bare links.
    assert b"Terms" in body and b"Privacy" in body
