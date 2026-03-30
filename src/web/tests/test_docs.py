"""Tests for Story [67] — Documentation site."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# =====================================================================
# Navigation / routing
# =====================================================================

@pytest.mark.asyncio
async def test_docs_root_redirects(client: AsyncClient):
    resp = await client.get("/docs", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/docs/quickstart-github"


@pytest.mark.asyncio
async def test_docs_unknown_slug_404(client: AsyncClient):
    resp = await client.get("/docs/nonexistent-page")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_docs_all_nav_pages_reachable(client: AsyncClient):
    slugs = [
        "quickstart-github",
        "quickstart-gitlab",
        "quickstart-bitbucket",
        "revue-yml-reference",
        "agents",
        "faq",
    ]
    for slug in slugs:
        resp = await client.get(f"/docs/{slug}")
        assert resp.status_code == 200, f"/docs/{slug} returned {resp.status_code}"


# =====================================================================
# Page content
# =====================================================================

@pytest.mark.asyncio
async def test_docs_github_quickstart_content(client: AsyncClient):
    resp = await client.get("/docs/quickstart-github")
    assert resp.status_code == 200
    assert b"GitHub Actions" in resp.content
    assert b"REVUE_LICENSE_KEY" in resp.content
    assert b"revue.yml" in resp.content


@pytest.mark.asyncio
async def test_docs_gitlab_quickstart_content(client: AsyncClient):
    resp = await client.get("/docs/quickstart-gitlab")
    assert resp.status_code == 200
    assert b"GitLab" in resp.content
    assert b"REVUE_LICENSE_KEY" in resp.content


@pytest.mark.asyncio
async def test_docs_bitbucket_quickstart_content(client: AsyncClient):
    resp = await client.get("/docs/quickstart-bitbucket")
    assert resp.status_code == 200
    assert b"Bitbucket" in resp.content
    assert b"bitbucket-pipelines.yml" in resp.content


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
    resp = await client.get("/docs/quickstart-github")
    assert b"Getting Started" in resp.content
    assert b"Reference" in resp.content
    assert b"Agent Catalogue" in resp.content
    assert b"FAQ" in resp.content


@pytest.mark.asyncio
async def test_docs_has_revue_branding(client: AsyncClient):
    resp = await client.get("/docs/quickstart-github")
    assert b"Revue" in resp.content
    assert b"Documentation" in resp.content


@pytest.mark.asyncio
async def test_docs_active_page_highlighted(client: AsyncClient):
    resp = await client.get("/docs/agents")
    # Active page should have brand colour class
    assert b"text-brand-400" in resp.content


@pytest.mark.asyncio
async def test_docs_has_prev_next_nav(client: AsyncClient):
    # Middle page should have both prev and next
    resp = await client.get("/docs/quickstart-gitlab")
    assert b"GitHub Actions" in resp.content  # prev
    assert b"Bitbucket" in resp.content       # next


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
    assert b"Revue.io Docs" in resp.content


@pytest.mark.asyncio
async def test_docs_get_started_link(client: AsyncClient):
    resp = await client.get("/docs/quickstart-github")
    assert b"Get started free" in resp.content
    assert b"/signup" in resp.content
