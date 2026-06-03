"""
REVUE-94 integration tests: pattern support end-to-end.

Tests:
- test_comparison_run_fp_reduction (AC4): mock-based comparison
- test_docs_configuration_updated (AC5): docs existence checks
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Resolve project root (tests/ is one level below project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# AC4: Comparison run — FP reduction
# ---------------------------------------------------------------------------

def test_comparison_run_fp_reduction():
    """AC4: Findings matching allowed patterns are absent when patterns configured.

    Uses a mock AI client to simulate a review. The mock returns findings that
    include the four known FP patterns. We verify that:
    1. Without patterns, the agent prompt does NOT contain pattern guidance.
    2. With patterns, the agent prompt DOES contain 'Do Not Flag' guidance.

    The actual FP filtering is done by the LLM interpreting the prompt — this
    test verifies the mechanism that makes it possible.
    """
    from revue_core.core.agent_loader import AgentDefinition, LoadedAgent
    from revue_core.core.pattern_injection import inject_patterns
    from revue_core.core.config_loader import load_config

    # Simulate "before" — no patterns
    defn = AgentDefinition(
        name="test-agent", display_name="Test Agent", role="test",
        system_prompt="Review the code for issues."
    )
    client = MagicMock()
    agent_before = LoadedAgent(defn, client, max_tokens=4096)
    inject_patterns([agent_before], allowed_patterns=[], disallowed_patterns=[])
    assert "Allowed Patterns" not in agent_before._def.system_prompt

    # Simulate "after" — with allowed patterns from project .revue.yml
    config = load_config(config_path=str(PROJECT_ROOT / ".revue.yml"))
    # .revue.yml grows over time; assert at least one pattern exists (not a magic count)
    assert len(config.allowed_patterns) >= 1

    defn_after = AgentDefinition(
        name="test-agent", display_name="Test Agent", role="test",
        system_prompt="Review the code for issues."
    )
    agent_after = LoadedAgent(defn_after, client, max_tokens=4096)
    inject_patterns([agent_after], config.allowed_patterns, config.disallowed_patterns)

    prompt = agent_after._def.system_prompt
    assert "## Allowed Patterns \u2014 Do Not Flag" in prompt
    # Only global patterns (no applies_to) are injected for an unknown agent name.
    # Agent-specific patterns are filtered out by inject_patterns \u2014 don't assert those.
    global_patterns = [p for p in config.allowed_patterns if not p.get("applies_to")]
    for pattern_entry in global_patterns:
        assert pattern_entry["pattern"] in prompt, (
            f"Global pattern not found in prompt: {pattern_entry['pattern']!r}"
        )
        assert pattern_entry["rationale"] in prompt, (
            f"Global rationale not found in prompt: {pattern_entry['rationale']!r}"
        )


# ---------------------------------------------------------------------------
# AC5: Documentation updated
# ---------------------------------------------------------------------------

def test_docs_configuration_updated():
    """AC5: Pattern configuration is documented.

    docs/configuration/ became a directory; the canonical pattern-config doc is now
    docs/guides/dismissing-findings.md. README.md also references allowed_patterns.
    """
    # The dismissing-findings guide is the canonical home for pattern config docs
    config_doc = PROJECT_ROOT / "docs" / "guides" / "dismissing-findings.md"
    assert config_doc.exists(), "docs/guides/dismissing-findings.md should exist"
    config_text = config_doc.read_text()
    assert "allowed_patterns" in config_text
    assert "disallowed_patterns" in config_text

    readme = PROJECT_ROOT / "README.md"
    assert readme.exists(), "README.md should exist"
    readme_text = readme.read_text()
    assert "allowed_patterns" in readme_text or "pattern" in readme_text.lower()
    # noise_filters is the live config section that holds allowed/disallowed
    # patterns — it is current (not deprecated) and documented in the README.
    assert "noise_filters" in readme_text
