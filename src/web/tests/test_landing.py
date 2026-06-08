"""Landing page tests — new hero positioning + two-mode block (REVUE-408).

PART B retires the cost-first hero ("Cut your AI API spend by 79–88%") and
installs the review-quality hero:
  - eyebrow / kicker:  Code review for local & CI
  - H1:                Real review at AI speed
  - subhead + a two-stat proof strip, each cited with a real outbound link
  - cost DEMOTED to the second beat ("The expensive part is CI, not you.")
    with the precise 79–88% figure kept ON the cost table.

These assertions avoid pinning en-/em-dash characters (which round-trip through
HTML entities and fail byte comparisons); they key on dash-free fragments, the
attribution text, and the outbound link hosts.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_landing_eyebrow_and_new_h1_present(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.content.decode()
    # Eyebrow / kicker above the H1 (the '&' renders as the &amp; entity, so
    # assert a dash/amp-free fragment).
    assert "Code review for local" in html
    # New H1.
    assert "Real review at AI speed" in html


@pytest.mark.asyncio
async def test_landing_retired_cost_first_h1_is_gone(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # The retired cost-first headline must not appear anywhere on the page.
    assert "Cut your AI API spend" not in html


@pytest.mark.asyncio
async def test_landing_subhead_present(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # Dash-free fragments from the subhead (em-dashes are entity-encoded).
    assert "the independent reviewer" in html
    assert "more often insecure" in html


@pytest.mark.asyncio
async def test_landing_proof_strip_cited_stats_with_links(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # Stat 1 — Georgetown CSET, Nov 2024 — with its real outbound link.
    assert "Georgetown CSET" in html
    assert "cset.georgetown.edu/publication/cybersecurity-risks-of-ai-generated-code" in html
    # Stat 2 — Stanford, ACM CCS 2023 — with its real outbound link.
    assert "Stanford" in html
    assert "arxiv.org/abs/2211.03622" in html


@pytest.mark.asyncio
async def test_landing_cost_demoted_to_second_beat(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # The cost beat heading is present...
    assert "The expensive part is CI, not you." in html
    # ...and appears AFTER the H1 (cost is demoted, not the headline).
    assert html.index("Real review at AI speed") < html.index("The expensive part is CI, not you.")


@pytest.mark.asyncio
async def test_landing_cost_figure_kept_on_cost_section(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # The precise 79–88% figure survives — now in the cost section, NOT the H1.
    assert "79" in html and "88%" in html
    # It must NOT be the H1 anymore.
    h1_region = html[html.index("Real review at AI speed"):html.index("Real review at AI speed") + 200]
    assert "88%" not in h1_region


@pytest.mark.asyncio
async def test_landing_byok_disclaimer_not_in_hero(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # The BYOK / savings-assumptions disclaimer is relocated out of the hero
    # block (to reclaim mobile vertical space). If it still exists on the page,
    # it must sit AFTER the proof strip / hero — never between H1 and proof.
    if "BYOK users pay their chosen provider" in html:
        assert html.index("arxiv.org/abs/2211.03622") < html.index(
            "BYOK users pay their chosen provider"
        )


@pytest.mark.asyncio
async def test_landing_shows_both_modes(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # Two-mode block on landing: CLI/local primary, CI complementary.
    assert 'data-mode="cli"' in html
    assert 'data-mode="ci"' in html
    assert html.index('data-mode="cli"') < html.index('data-mode="ci"')
    # Landing carries the explicit `revue activate` reference (AC).
    assert "revue activate" in html
    # Every CI reference links to the canonical CI setup page.
    assert "/docs/ci-setup" in html


@pytest.mark.asyncio
async def test_landing_legacy_ci_only_strings_gone(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # REVUE-408 AC: no in-scope surface retains CI-only framing that excludes
    # or subordinates CLI mode.
    assert "reviews every pull request in your CI pipeline" not in html
    assert "Revue runs inside your CI runner" not in html


@pytest.mark.asyncio
async def test_landing_cta_and_free_offer_intact(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # Existing CTA + free offer must remain.
    assert "Get started free" in html
    assert "25 free reviews" in html
    assert "No credit card required" in html


# ---------------------------------------------------------------------------
# REVUE-365 — pricing feature comparison table
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pricing_comparison_table_all_rows_present(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.content.decode()
    # All 6 rows must appear (AC1: exactly these rows, in order)
    assert "Reviews / month" in html
    assert "Specialist agents" in html
    assert "Fix suggestions" in html
    assert "BYOK" in html
    assert "Custom rules (YAML)" in html
    assert "Support" in html


@pytest.mark.asyncio
async def test_pricing_comparison_table_values_correct(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # Spot-check key values to confirm the spec was followed
    assert "1 (code quality)" in html   # Free specialist agents
    assert "All 6" in html              # Indie/Pro specialist agents
    assert "Unlimited" in html          # Pro reviews/month
    assert "Priority" in html           # Pro support
    assert "Email" in html              # Indie support
    assert "Docs" in html               # Free support
    # Fix suggestions row: Free must show ✗, Indie/Pro must show ✓.
    # Extract the Fix suggestions row and verify the ✗ entity appears before the ✓ entities.
    fix_start = html.index("Fix suggestions")
    fix_row = html[fix_start:fix_start + 600]
    cross_pos = fix_row.index("&#10007;")   # ✗ — Free column
    check_pos = fix_row.index("&#10003;")   # ✓ — Indie column (first ✓ after the ✗)
    assert cross_pos < check_pos, "Free Fix suggestions must show ✗ before the ✓ for paid tiers"


@pytest.mark.asyncio
async def test_pricing_no_roadmap_hints(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # AC2: no roadmap, coming-soon, or future-feature hints
    assert "Coming to Pro" not in html
    assert "Coming soon" not in html
    assert "Roadmap" not in html
    assert "Future" not in html


@pytest.mark.asyncio
async def test_pricing_tooltips_on_four_ambiguous_rows(client: AsyncClient):
    resp = await client.get("/")
    html = resp.content.decode()
    # AC3: each ambiguous row has a tooltip (title attribute with spec copy)
    assert "Each specialist reviews your code from a different angle" in html
    assert "copy-paste fix snippets alongside each finding" in html
    assert "Revue never marks up inference costs" in html
    assert "Define project-specific rules in .revue.yml" in html


# ---------------------------------------------------------------------------
# REVUE-366 — "Claude Code only at launch" disclaimer on hero
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hero_claude_code_only_disclaimer_present(client: AsyncClient):
    """REVUE-366 AC1/TC1: hero shows 'Currently supports Claude Code'."""
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Claude Code" in html
    # Guard: Cursor and Windsurf must not appear as interactive options.
    assert 'href="#cursor"' not in html
    assert 'href="#windsurf"' not in html
    assert 'data-client="cursor"' not in html
    assert 'data-client="windsurf"' not in html
