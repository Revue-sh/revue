"""Integration test for Nova pattern guidance (REVUE-174).

Verifies that when given a realistic won't-fix reply thread, Nova generates
pattern and rationale fields that do not contain line-number references.
"""
from __future__ import annotations

import os
import re

import pytest

from revue.core.dedup_consolidator import NovaConsolidator

# Try to import the factory; if it fails (Python 3.9 type annotation compat
# issue in ai_config.py), mark unavailable and skip at test time.
try:
    from revue.core.ai_client import create_ai_client
    from revue.core.ai_config import AIConfig
    _AI_CLIENT_AVAILABLE = True
    _AI_CLIENT_ERROR = ""
except TypeError as e:
    _AI_CLIENT_AVAILABLE = False
    _AI_CLIENT_ERROR = str(e)


@pytest.mark.integration
def test_nova_pattern_lacks_line_numbers():
    """TC5 / AC5: Nova-generated pattern and rationale must not contain 'line X' refs."""
    if not _AI_CLIENT_AVAILABLE:
        pytest.skip(f"AI client not importable: {_AI_CLIENT_ERROR}")

    if not os.getenv("REVUE_MODEL"):
        pytest.skip("REVUE_MODEL env var not set — skipping integration test")

    # Fixture: a realistic thread based on PR #72 fence-detection finding.
    # The finding: Revue flagged _parse_thread_decisions fence logic.
    # Developer reply: "It's safe because valid JSON cannot start with
    # triple-backtick — branches are exhaustive and non-overlapping."
    thread = {
        "fingerprint": "test-fence-detection-2f8c7",
        "file_path": "src/revue/core/dedup_consolidator.py",
        "line": 145,  # metadata sent TO Nova — test verifies Nova does not echo it back
        "issue_type": "logic_correctness",
        "severity": "medium",
        "original_finding_summary": "Fence detection logic may miss edge cases",
        "replies": [
            "It's safe because valid JSON cannot start with triple-backtick — the branches are exhaustive and non-overlapping.",
        ],
    }

    # Resolve client from config — honours REVUE_PROVIDER, not just Anthropic
    config = AIConfig.from_env()
    client = create_ai_client(config)
    nova = NovaConsolidator(client)

    # Analyse the thread
    decisions = nova.analyse_reply_threads([thread])

    # Verify we got a decision back
    assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"
    decision = decisions[0]

    # Verify fingerprint matches
    assert decision.get("fingerprint") == "test-fence-detection-2f8c7"

    # Verify decision is allowed_pattern (developer approved the finding with reasoning)
    assert decision.get("decision") == "allowed_pattern", (
        f"Expected allowed_pattern, got {decision.get('decision')}. "
        "Thread should be classified as allowed_pattern because it has "
        "both acknowledgement and reasoning."
    )

    # Verify pattern exists and contains no line-number references
    pattern = decision.get("pattern", "")
    assert pattern, "Pattern field must be present for allowed_pattern decision"
    assert not re.search(r"(?:at |on )?line\s+\d+", pattern), (
        f"Pattern contains line-number reference: {pattern}"
    )

    # Verify rationale exists and contains no line-number references
    rationale = decision.get("rationale", "")
    assert rationale, "Rationale field must be present for allowed_pattern decision"
    assert not re.search(r"(?:at |on )?line\s+\d+", rationale), (
        f"Rationale contains line-number reference: {rationale}"
    )

    # Verify pattern and rationale describe the design invariant, not the code
    # Both should mention concepts like "exhaustive", "branches", "JSON", "fence"
    invariant_words = ("exhaustive", "branches", "json", "fence", "guard", "invariant")
    pattern_lower = pattern.lower()
    rationale_lower = rationale.lower()

    assert any(word in pattern_lower for word in invariant_words), (
        f"Pattern should describe the design invariant, not just a code location. "
        f"Pattern: {pattern}"
    )

    assert any(word in rationale_lower for word in invariant_words), (
        f"Rationale should describe the design invariant, not just a code location. "
        f"Rationale: {rationale}"
    )
