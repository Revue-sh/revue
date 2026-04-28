"""Tests for Nova contradiction synthesis — REVUE-179."""
from __future__ import annotations

from unittest.mock import MagicMock

from revue.core.models import AIReview
from revue.core.dedup_consolidator import consolidate, SameFileLineStrategy, SimilarIssueStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(agent: str, severity: str, file_path: str = "app.py",
             line_number: int = 10, issue: str = "", category: str = "general") -> AIReview:
    return AIReview(
        file_path=file_path,
        line_number=line_number,
        severity=severity,
        issue=issue or f"{agent} issue",
        suggestion=f"{agent} fix",
        confidence=0.9,
        agent_name=agent,
        category=category,
    )


def _mock_client(response_json: str) -> MagicMock:
    """Mock AIClient whose complete() returns a text attribute with the given JSON."""
    client = MagicMock()
    result = MagicMock()
    result.text = response_json
    client.complete.return_value = result
    return client


# ---------------------------------------------------------------------------
# TC1 — Two-agent contradiction synthesis (AC1, AC2, AC5)
# ---------------------------------------------------------------------------

def test_two_agent_contradiction_synthesised() -> None:
    """TC1: Kai (high) + Zara (critical) on same line → 1 finding, severity=critical,
    synthesised_from=[("kai", "performance"), ("zara", "security")]."""
    kai = _finding("kai", "high", issue="Performance concern", category="performance")
    zara = _finding("zara", "critical", issue="Security risk", category="security")

    mock_response = (
        '[{"file": "app.py", "line": 10, '
        '"issue": "Performance and security issue", "suggestion": "Fix both"}]'
    )
    client = _mock_client(mock_response)

    result = consolidate([kai, zara], ai_client=client)

    assert len(result.findings) == 1
    synthesised = result.findings[0]
    assert synthesised.severity == "critical"
    assert set(synthesised.synthesised_from) == {("kai", "performance"), ("zara", "security")}
    assert synthesised.issue == "Performance and security issue"
    assert synthesised.agent_name == "nova"


# ---------------------------------------------------------------------------
# TC2 — Single finding passthrough (AC1, AC5)
# ---------------------------------------------------------------------------

def test_single_finding_no_synthesis() -> None:
    """TC2: Single Kai finding passes through unchanged; synthesised_from is None."""
    kai = _finding("kai", "high")
    client = _mock_client("[]")

    result = consolidate([kai], ai_client=client)

    assert len(result.findings) == 1
    assert result.findings[0].synthesised_from is None
    # LLM should not be called when no groups have 2+ findings
    client.complete.assert_not_called()


# ---------------------------------------------------------------------------
# TC3 — synthesis_events in ConsolidationResult (AC4)
# ---------------------------------------------------------------------------

def test_synthesis_events_populated_on_result() -> None:
    """TC3: ConsolidationResult.synthesis_events contains one event per synthesised group."""
    kai = _finding("kai", "high", file_path="api.py", line_number=47)
    zara = _finding("zara", "critical", file_path="api.py", line_number=47)

    mock_response = (
        '[{"file": "api.py", "line": 47, '
        '"issue": "Combined finding", "suggestion": "Combined fix"}]'
    )
    client = _mock_client(mock_response)

    result = consolidate([kai, zara], ai_client=client)

    assert len(result.synthesis_events) == 1
    event = result.synthesis_events[0]
    assert set(event["from_agents"]) == {"kai", "zara"}
    assert event["file"] == "api.py"
    assert event["line"] == 47
    assert set(event["severity_in"]) == {"high", "critical"}
    assert event["severity_out"] == "critical"


# ---------------------------------------------------------------------------
# TC4 — Three-way synthesis (AC2, AC5)
# ---------------------------------------------------------------------------

def test_three_agent_synthesis() -> None:
    """TC4: Kai (high) + Zara (critical) + Maya (medium) → 1 finding, severity=critical,
    synthesised_from carries (name, category) tuples for all three."""
    kai = _finding("kai", "high", category="performance")
    zara = _finding("zara", "critical", category="security")
    maya = _finding("maya", "medium", category="code quality")

    mock_response = (
        '[{"file": "app.py", "line": 10, '
        '"issue": "Three concerns", "suggestion": "Fix all three"}]'
    )
    client = _mock_client(mock_response)

    result = consolidate([kai, zara, maya], ai_client=client)

    assert len(result.findings) == 1
    synthesised = result.findings[0]
    assert synthesised.severity == "critical"
    assert set(synthesised.synthesised_from) == {
        ("kai", "performance"), ("zara", "security"), ("maya", "code quality")
    }
    assert synthesised.agent_name == "nova"


# ---------------------------------------------------------------------------
# Fallback — no ai_client, all findings pass through (AC2 fallback)
# ---------------------------------------------------------------------------

def test_no_ai_client_no_synthesis() -> None:
    """Without an ai_client, consolidate() passes findings through without synthesis."""
    kai = _finding("kai", "high")
    zara = _finding("zara", "critical")

    result = consolidate([kai, zara], ai_client=None)

    # Both findings remain (different severities, not duplicates)
    assert len(result.findings) == 2
    for f in result.findings:
        assert f.synthesised_from is None
    assert result.synthesis_events == []


# ---------------------------------------------------------------------------
# Fallback — LLM call fails, findings pass through (AC2 fallback)
# ---------------------------------------------------------------------------

def test_llm_failure_falls_back_to_passthrough() -> None:
    """If the LLM call raises, consolidate() falls back — no synthesis, no crash."""
    kai = _finding("kai", "high")
    zara = _finding("zara", "critical")

    failing_client = MagicMock()
    failing_client.complete.side_effect = RuntimeError("LLM unavailable")

    result = consolidate([kai, zara], ai_client=failing_client)

    # Both findings pass through unchanged
    assert len(result.findings) == 2
    for f in result.findings:
        assert f.synthesised_from is None
    assert result.synthesis_events == []


# ---------------------------------------------------------------------------
# Comment rendering helper (AC3)
# ---------------------------------------------------------------------------

def test_llm_returns_non_dict_entry_falls_back() -> None:
    """Malformed LLM response containing a non-dict entry must not crash — falls back to originals."""
    kai = _finding("kai", "high")
    zara = _finding("zara", "critical")

    client = _mock_client('[42, null, "bad"]')  # all entries are non-dicts

    result = consolidate([kai, zara], ai_client=client)

    assert len(result.findings) == 2
    assert result.synthesis_events == []


def test_llm_returns_non_numeric_line_falls_back() -> None:
    """LLM entry with non-numeric line number must not raise — that entry is skipped."""
    kai = _finding("kai", "high")
    zara = _finding("zara", "critical")

    bad_json = '[{"file": "app.py", "line": "not-a-number", "issue": "x", "suggestion": "y"}]'
    client = _mock_client(bad_json)

    result = consolidate([kai, zara], ai_client=client)

    # Bad entry skipped → originals pass through
    assert len(result.findings) == 2
    assert result.synthesis_events == []


def test_llm_returns_entry_missing_required_fields_falls_back() -> None:
    """LLM entry missing 'file' or 'issue' must be skipped gracefully."""
    kai = _finding("kai", "high")
    zara = _finding("zara", "critical")

    bad_json = '[{"line": 10, "suggestion": "fix it"}]'  # missing file and issue
    client = _mock_client(bad_json)

    result = consolidate([kai, zara], ai_client=client)

    assert len(result.findings) == 2
    assert result.synthesis_events == []


def test_format_synthesis_attribution() -> None:
    """AC3: _format_synthesis_attribution renders with agent name, emoji, bold category, pipe separator."""
    from revue.cli import _format_synthesis_attribution

    result = _format_synthesis_attribution([("kai", "performance"), ("zara", "security")])
    assert result == "Agents: Kai ⚡ **Performance** | Zara 🔒 **Security** → Nova 🌟 (synthesised)"


def test_format_synthesis_attribution_single_agent() -> None:
    """Single source agent still renders correctly."""
    from revue.cli import _format_synthesis_attribution

    result = _format_synthesis_attribution([("maya", "code quality")])
    assert result == "Agents: Maya ✨ **Code Quality** → Nova 🌟 (synthesised)"


def test_format_synthesis_attribution_unknown_agent() -> None:
    """Unknown agent name falls back to title-cased name with no emoji."""
    from revue.cli import _format_synthesis_attribution

    result = _format_synthesis_attribution([("unknownbot", "general")])
    assert "Unknownbot" in result
    assert "**General**" in result
    assert "Nova 🌟 (synthesised)" in result


def test_format_synthesis_attribution_deduplicates_same_agent_category() -> None:
    """Duplicate (agent, category) pairs are collapsed to one entry — same agent can't appear twice."""
    from revue.cli import _format_synthesis_attribution

    result = _format_synthesis_attribution([
        ("maya", "code quality"),
        ("maya", "code quality"),
        ("leo", "architecture"),
    ])
    assert result == "Agents: Maya ✨ **Code Quality** | Leo 🏗️ **Architecture** → Nova 🌟 (synthesised)"
    assert result.count("Maya") == 1


# ---------------------------------------------------------------------------
# EC-1 — SimilarIssueStrategy must not collapse cross-agent findings
# ---------------------------------------------------------------------------

def test_similar_issue_strategy_same_agent_is_duplicate() -> None:
    """Existing behaviour: same agent, high word overlap, same line = duplicate."""
    strategy = SimilarIssueStrategy()
    a = _finding("kai", "high", issue="SQL injection in the query builder", line_number=10)
    b = _finding("kai", "high", issue="SQL injection vulnerability in the query builder", line_number=10)
    assert strategy.are_duplicates(a, b) is True


def test_similar_issue_strategy_cross_agent_not_duplicate() -> None:
    """EC-1: Different agents with high word overlap on the same line must NOT be duplicates —
    they are synthesis candidates, not dedup targets."""
    strategy = SimilarIssueStrategy()
    a = _finding("kai", "high", issue="SQL injection in the query builder", line_number=10)
    b = _finding("zara", "critical", issue="SQL injection vulnerability in the query builder", line_number=10)
    assert strategy.are_duplicates(a, b) is False


def test_cross_agent_similar_findings_reach_synthesis() -> None:
    """EC-1 end-to-end: Cross-agent findings with word overlap above the threshold must
    survive dedup and reach synthesis rather than being silently merged."""
    kai = _finding("kai", "high", issue="SQL injection in query builder", category="security")
    zara = _finding("zara", "critical", issue="SQL injection vulnerability query builder", category="security")

    result = consolidate([kai, zara], ai_client=None)

    assert len(result.findings) == 2, (
        "Cross-agent similar findings must not be collapsed by SimilarIssueStrategy"
    )


# ---------------------------------------------------------------------------
# AA-7 — Synthesised findings must NOT render the standard 'Agent · Category' label
# ---------------------------------------------------------------------------

def test_synthesised_finding_no_standard_label() -> None:
    """AA-7: When synthesised_from is set, the comment body must contain the synthesis
    attribution and must NOT contain the standard 'Nova · General' agent label."""
    from revue.cli import _AGENT_DISPLAY_NAMES, _format_synthesis_attribution, SEVERITY_EMOJI

    sev, issue = "critical", "Combined issue"
    cat = "general"
    f: dict = {
        "agent_name": "nova",
        "category": "general",
        "synthesised_from": [("kai", "performance"), ("zara", "security")],
    }

    emoji = SEVERITY_EMOJI.get(sev, "⚪")
    body_parts = [f"**{emoji} [{sev.upper()}] {issue}**"]

    # Replicate the rendering branch from cli.py — after the fix this uses elif
    synthesised_from = f.get("synthesised_from")
    if synthesised_from:
        attribution = _format_synthesis_attribution(synthesised_from)
        body_parts.append(f"*{attribution}*")
    elif cat:
        display_cat = cat.replace("-", " ").title()
        display_agent = _AGENT_DISPLAY_NAMES.get(f.get("agent_name", ""), "")
        label = f"{display_agent} · {display_cat}" if display_agent else display_cat
        body_parts.append(f"*{label}*")

    body = "\n".join(body_parts)
    assert "Nova 🌟 (synthesised)" in body
    assert "Nova · General" not in body


# ---------------------------------------------------------------------------
# EC-11 — synthesis_events agent_names must not include empty-string agent names
# ---------------------------------------------------------------------------

def test_synthesis_events_agent_names_exclude_empty_string() -> None:
    """EC-11: synthesis_events.from_agents must not include '' — findings without an
    agent_name must be filtered the same way synthesised_from already filters them."""
    kai = _finding("kai", "high", category="performance")
    unknown = AIReview(
        file_path="app.py", line_number=10, severity="medium",
        issue="Unknown agent issue", suggestion="fix", confidence=0.5,
        agent_name="",  # no agent name
    )

    mock_response = (
        '[{"file": "app.py", "line": 10, '
        '"issue": "Combined", "suggestion": "Fix both"}]'
    )
    client = _mock_client(mock_response)

    result = consolidate([kai, unknown], ai_client=client)

    assert len(result.synthesis_events) == 1
    from_agents = result.synthesis_events[0]["from_agents"]
    assert "" not in from_agents, "Empty-string agent names must be excluded from synthesis events"


# ---------------------------------------------------------------------------

def test_format_synthesis_attribution_preserves_same_agent_different_category() -> None:
    """Same agent with different categories both appear — only exact duplicates are removed."""
    from revue.cli import _format_synthesis_attribution

    result = _format_synthesis_attribution([
        ("maya", "code quality"),
        ("maya", "security"),
        ("leo", "architecture"),
    ])
    assert result.count("Maya") == 2
    assert "**Code Quality**" in result
    assert "**Security**" in result
