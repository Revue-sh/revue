"""Tests for pr_context.py — PR context filtering and routing."""
import pytest
from revue.core.pr_context import (
    AgentContext,
    PRContextExtractor,
    AGENT_SECTION_MAP,
    estimate_token_savings,
)
from revue.core.pr_description_adapter import PRDescription


# ---------------------------------------------------------------------------
# AgentContext tests
# ---------------------------------------------------------------------------

def test_agent_context_to_prompt_context_empty():
    """Empty context returns just PR title."""
    context = AgentContext(
        agent_name="test-agent",
        pr_title="Test PR",
        relevant_sections={},
    )
    prompt = context.to_prompt_context()
    assert "Test PR" in prompt
    assert prompt.count("**") == 2  # Just the title formatting


def test_agent_context_to_prompt_context_single_section():
    """Single section formatted correctly."""
    context = AgentContext(
        agent_name="test-agent",
        pr_title="Test PR",
        relevant_sections={"summary": "This is a summary."},
    )
    prompt = context.to_prompt_context()
    assert "**PR Title:** Test PR" in prompt
    assert "**Summary:**" in prompt
    assert "This is a summary." in prompt


def test_agent_context_to_prompt_context_multiple_sections():
    """Multiple sections formatted with proper spacing."""
    context = AgentContext(
        agent_name="test-agent",
        pr_title="Test PR",
        relevant_sections={
            "summary": "Summary text",
            "changes": "Change details",
            "out_of_scope": "Not included",
        },
    )
    prompt = context.to_prompt_context()
    assert "**Summary:**" in prompt
    assert "**Changes:**" in prompt
    assert "**Out Of Scope:**" in prompt
    assert "Summary text" in prompt
    assert "Change details" in prompt


def test_agent_context_to_prompt_context_ignores_empty_sections():
    """Empty sections are not included in output."""
    context = AgentContext(
        agent_name="test-agent",
        pr_title="Test PR",
        relevant_sections={
            "summary": "Content",
            "changes": "   ",  # Whitespace-only
            "out_of_scope": "",
        },
    )
    prompt = context.to_prompt_context()
    assert "**Summary:**" in prompt
    assert "**Changes:**" not in prompt
    assert "**Out Of Scope:**" not in prompt


# ---------------------------------------------------------------------------
# PRContextExtractor tests
# ---------------------------------------------------------------------------

def test_pr_context_extractor_get_context_for_orchestrator():
    """Orchestrator gets summary, background, out_of_scope."""
    pr = PRDescription(
        title="Feature PR",
        raw_description="Full description",
        summary="Add feature X",
        background="Historical context",
        changes="Code changes",
        out_of_scope="Feature Y deferred",
        dependencies="Requires lib Z",
    )
    
    extractor = PRContextExtractor(pr)
    context = extractor.get_context_for_agent("orchestrator")
    
    assert context.agent_name == "orchestrator"
    assert context.pr_title == "Feature PR"
    assert "summary" in context.relevant_sections
    assert "background" in context.relevant_sections
    assert "out_of_scope" in context.relevant_sections
    # Should NOT include changes or dependencies
    assert "changes" not in context.relevant_sections
    assert "dependencies" not in context.relevant_sections


def test_pr_context_extractor_get_context_for_security_expert():
    """Security expert gets changes, dependencies, out_of_scope."""
    pr = PRDescription(
        title="Security PR",
        raw_description="Full description",
        summary="Security improvements",
        changes="Add input validation",
        dependencies="Requires crypto lib v2",
        out_of_scope="Rate limiting (separate PR)",
    )
    
    extractor = PRContextExtractor(pr)
    context = extractor.get_context_for_agent("security-expert")
    
    assert "changes" in context.relevant_sections
    assert "dependencies" in context.relevant_sections
    assert "out_of_scope" in context.relevant_sections
    # Should NOT include summary
    assert "summary" not in context.relevant_sections


def test_pr_context_extractor_get_context_for_code_quality_expert():
    """Code quality expert gets changes, AC, testing."""
    pr = PRDescription(
        title="Refactor PR",
        raw_description="Full description",
        changes="Extract helper functions",
        acceptance_criteria="All tests pass\nNo regressions",
        testing="Added 5 unit tests",
    )
    
    extractor = PRContextExtractor(pr)
    context = extractor.get_context_for_agent("code-quality-expert")
    
    assert "changes" in context.relevant_sections
    assert "acceptance_criteria" in context.relevant_sections
    assert "testing" in context.relevant_sections


def test_pr_context_extractor_get_context_for_unknown_agent():
    """Unknown agent gets empty sections (just title)."""
    pr = PRDescription(
        title="Test PR",
        raw_description="Full description",
        summary="Some summary",
    )
    
    extractor = PRContextExtractor(pr)
    context = extractor.get_context_for_agent("unknown-agent")
    
    assert context.agent_name == "unknown-agent"
    assert context.pr_title == "Test PR"
    assert len(context.relevant_sections) == 0


def test_pr_context_extractor_get_context_excludes_empty_sections():
    """Extractor doesn't include sections that are empty in PR."""
    pr = PRDescription(
        title="Minimal PR",
        raw_description="Full description",
        summary="Summary only",
        # All other sections empty
    )
    
    extractor = PRContextExtractor(pr)
    context = extractor.get_context_for_agent("orchestrator")
    
    # Should have summary, but not background or out_of_scope (empty)
    assert "summary" in context.relevant_sections
    assert "background" not in context.relevant_sections
    assert "out_of_scope" not in context.relevant_sections


def test_pr_context_extractor_get_full_context():
    """get_full_context returns complete PR description."""
    pr = PRDescription(
        title="Full PR",
        raw_description="Complete description with all sections.",
        summary="Summary",
    )
    
    extractor = PRContextExtractor(pr)
    full = extractor.get_full_context()
    
    assert "# Full PR" in full
    assert "Complete description" in full


# ---------------------------------------------------------------------------
# Section routing map validation
# ---------------------------------------------------------------------------

def test_agent_section_map_coverage():
    """All expected agents are in the routing map."""
    expected_agents = {
        "orchestrator",
        "code-quality-expert",
        "security-expert",
        "performance-expert",
        "architecture-expert",
        "documentation-expert",
        "consolidator",
    }
    assert set(AGENT_SECTION_MAP.keys()) == expected_agents


def test_agent_section_map_valid_sections():
    """All section names in map are valid PRDescription fields."""
    valid_sections = {
        "summary", "background", "changes", "acceptance_criteria",
        "testing", "out_of_scope", "dependencies",
    }
    
    for agent, sections in AGENT_SECTION_MAP.items():
        for section in sections:
            assert section in valid_sections, (
                f"Invalid section '{section}' for agent '{agent}'"
            )


def test_agent_section_map_no_duplicates():
    """Each agent has unique section list (no duplicates)."""
    for agent, sections in AGENT_SECTION_MAP.items():
        assert len(sections) == len(set(sections)), (
            f"Agent '{agent}' has duplicate sections: {sections}"
        )


# ---------------------------------------------------------------------------
# Token savings estimation tests
# ---------------------------------------------------------------------------

def test_estimate_token_savings_empty_pr():
    """Empty PR has zero savings."""
    pr = PRDescription(title="Empty", raw_description="")
    
    stats = estimate_token_savings(pr)
    
    assert stats["full_tokens"] >= 0
    assert stats["filtered_tokens"] >= 0
    assert stats["savings_percent"] >= 0


def test_estimate_token_savings_realistic_pr():
    """Realistic PR shows significant token savings."""
    pr = PRDescription(
        title="feat(auth)[REVUE-123]: Add JWT authentication",
        raw_description="""
## Summary
Implements JWT-based authentication for API endpoints.

## Background
Previous auth system used session cookies which don't work well with mobile clients.

## Changes
- Add JWT middleware
- Update login endpoint to issue tokens
- Add token refresh endpoint
- Update API docs

## Acceptance Criteria
1. Users can login and receive JWT
2. Authenticated requests include valid token
3. Token refresh works before expiry
4. Invalid tokens are rejected

## Testing
- Unit tests for JWT utils
- Integration tests for auth flow
- E2E tests for mobile client

## Out of Scope
- OAuth2 integration (deferred to REVUE-124)
- Rate limiting (separate story)

## Dependencies
- Requires `pyjwt>=2.8.0`
""",
        summary="Implements JWT-based authentication for API endpoints.",
        background="Previous auth system used session cookies which don't work well with mobile clients.",
        changes="""- Add JWT middleware
- Update login endpoint to issue tokens
- Add token refresh endpoint
- Update API docs""",
        acceptance_criteria="""1. Users can login and receive JWT
2. Authenticated requests include valid token
3. Token refresh works before expiry
4. Invalid tokens are rejected""",
        testing="""- Unit tests for JWT utils
- Integration tests for auth flow
- E2E tests for mobile client""",
        out_of_scope="""- OAuth2 integration (deferred to REVUE-124)
- Rate limiting (separate story)""",
        dependencies="- Requires `pyjwt>=2.8.0`",
    )
    
    stats = estimate_token_savings(pr)
    
    # Should show significant savings (target: 40-60%)
    assert stats["savings_percent"] > 30, (
        f"Expected >30% savings, got {stats['savings_percent']}%"
    )
    assert stats["filtered_tokens"] < stats["full_tokens_all_agents"]
    assert "per_agent_breakdown" in stats
    assert len(stats["per_agent_breakdown"]) == len(AGENT_SECTION_MAP)


def test_estimate_token_savings_all_agents_get_some_context():
    """All agents in map receive non-zero context."""
    pr = PRDescription(
        title="Complete PR",
        raw_description="Full description",
        summary="Summary",
        background="Background",
        changes="Changes",
        acceptance_criteria="AC",
        testing="Tests",
        out_of_scope="Out of scope",
        dependencies="Dependencies",
    )
    
    stats = estimate_token_savings(pr)
    
    # Every agent should get at least the title
    for agent, tokens in stats["per_agent_breakdown"].items():
        assert tokens > 0, f"Agent '{agent}' got zero tokens"


def test_estimate_token_savings_partial_sections():
    """Works correctly when some sections are missing."""
    pr = PRDescription(
        title="Minimal PR",
        raw_description="Just summary",
        summary="Brief summary",
        # All other sections empty
    )
    
    stats = estimate_token_savings(pr)
    
    # Should still calculate without errors
    assert stats["savings_percent"] >= 0
    assert stats["filtered_tokens"] > 0


def test_estimate_token_savings_per_agent_breakdown():
    """Per-agent breakdown shows different token counts."""
    pr = PRDescription(
        title="Test PR",
        raw_description="Description",
        summary="Short summary",
        changes="Very long detailed list of changes " * 50,  # Make changes section large
    )
    
    stats = estimate_token_savings(pr)
    breakdown = stats["per_agent_breakdown"]
    
    # Agents that get 'changes' should have more tokens
    quality_tokens = breakdown.get("code-quality-expert", 0)
    orchestrator_tokens = breakdown.get("orchestrator", 0)
    
    # code-quality-expert gets changes, orchestrator doesn't
    assert quality_tokens > orchestrator_tokens, (
        "code-quality-expert should have more tokens (gets 'changes' section)"
    )
