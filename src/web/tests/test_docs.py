"""Tests for Story [67] — Documentation site."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# =====================================================================
# Navigation / routing
# =====================================================================

@pytest.mark.asyncio
async def test_docs_root_redirects(client: AsyncClient):
    # REVUE-407: the docs index now lands on the consolidated CI-setup page.
    resp = await client.get("/docs", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/docs/ci-setup"


@pytest.mark.asyncio
async def test_docs_unknown_slug_404(client: AsyncClient):
    resp = await client.get("/docs/nonexistent-page")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_docs_all_nav_pages_reachable(client: AsyncClient):
    # REVUE-407: the three quickstart slugs were consolidated into ci-setup.
    slugs = [
        "ci-setup",
        "revue-yml-reference",
        "agents",
        "faq",
    ]
    for slug in slugs:
        resp = await client.get(f"/docs/{slug}")
        assert resp.status_code == 200, f"/docs/{slug} returned {resp.status_code}"


# =====================================================================
# Legacy quickstart redirects (REVUE-407 AC5 — single source of truth)
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("slug", [
    "quickstart-github",
    "quickstart-gitlab",
    "quickstart-bitbucket",
])
async def test_legacy_quickstart_redirects_to_ci_setup(client: AsyncClient, slug: str):
    resp = await client.get(f"/docs/{slug}", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/docs/ci-setup"


# =====================================================================
# Page content — consolidated CI-setup page
# =====================================================================

@pytest.mark.asyncio
async def test_ci_setup_covers_all_three_platforms(client: AsyncClient):
    resp = await client.get("/docs/ci-setup")
    assert resp.status_code == 200
    assert b"Bitbucket Pipelines" in resp.content
    assert b"GitHub Actions" in resp.content
    assert b"GitLab CI" in resp.content
    assert b"REVUE_LICENSE_KEY" in resp.content
    # Unified provider-key name (AC6).
    assert b"AI_API_KEY" in resp.content


@pytest.mark.asyncio
async def test_ci_setup_two_mode_framing(client: AsyncClient):
    resp = await client.get("/docs/ci-setup")
    assert resp.status_code == 200
    assert b"CI mode" in resp.content
    assert b"revue activate" in resp.content


@pytest.mark.asyncio
async def test_docs_revue_yml_reference_content(client: AsyncClient):
    resp = await client.get("/docs/revue-yml-reference")
    assert resp.status_code == 200
    assert b"max_diff_lines" in resp.content
    assert b"min_confidence" in resp.content
    assert b"agent_timeout_seconds" in resp.content
    assert b"ignore_patterns" in resp.content


@pytest.mark.asyncio
async def test_docs_agents_content(client: AsyncClient):
    resp = await client.get("/docs/agents")
    assert resp.status_code == 200
    assert b"Zara" in resp.content
    assert b"Kai" in resp.content
    assert b"Maya" in resp.content
    assert b"Leo" in resp.content
    assert b"Nova" in resp.content
    assert b"Sage" in resp.content


@pytest.mark.asyncio
async def test_docs_faq_content(client: AsyncClient):
    resp = await client.get("/docs/faq")
    assert resp.status_code == 200
    assert b"BYOK" in resp.content
    assert b"diff limit" in resp.content or b"Diff limit" in resp.content
    assert b"Sage" in resp.content


# =====================================================================
# Layout / structure
# =====================================================================

@pytest.mark.asyncio
async def test_docs_has_sidebar_nav(client: AsyncClient):
    resp = await client.get("/docs/ci-setup")
    assert b"Getting Started" in resp.content
    assert b"Reference" in resp.content
    assert b"Agent Catalogue" in resp.content
    assert b"FAQ" in resp.content


@pytest.mark.asyncio
async def test_docs_has_revue_branding(client: AsyncClient):
    resp = await client.get("/docs/ci-setup")
    assert b"Revue" in resp.content
    assert b"Documentation" in resp.content


@pytest.mark.asyncio
async def test_docs_active_page_highlighted(client: AsyncClient):
    resp = await client.get("/docs/agents")
    # Active page should have brand colour class
    assert b"text-brand-400" in resp.content


@pytest.mark.asyncio
async def test_docs_has_prev_next_nav(client: AsyncClient):
    # Markdown docs still carry prev/next links. revue-yml-reference sits between
    # ci-setup (prev) and agents (next) in the NAV order.
    resp = await client.get("/docs/revue-yml-reference")
    assert b"CI Setup" in resp.content        # prev
    assert b"Agent Catalogue" in resp.content  # next


@pytest.mark.asyncio
async def test_docs_markdown_rendered_as_html(client: AsyncClient):
    resp = await client.get("/docs/faq")
    # Markdown headers should be rendered as HTML h tags
    assert b"<h1" in resp.content or b"<h2" in resp.content
    # Code blocks should be rendered
    assert b"<code" in resp.content


@pytest.mark.asyncio
async def test_docs_page_title_set(client: AsyncClient):
    resp = await client.get("/docs/agents")
    assert b"Agent Catalogue" in resp.content
    assert b"Revue Docs" in resp.content


@pytest.mark.asyncio
async def test_docs_get_started_link(client: AsyncClient):
    resp = await client.get("/docs/ci-setup")
    assert b"Get started free" in resp.content
    assert b"/signup" in resp.content
