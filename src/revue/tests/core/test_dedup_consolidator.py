"""Tests for deduplication consolidation."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from revue.core.dedup_consolidator import (
    consolidate,
    ConsolidationResult,
    SameFileLineStrategy,
    _parse_thread_decisions,
)
from revue.core.models import AIReview


def _review(file_path="app.py", line_number=10, severity="minor",
            issue="test issue", confidence=0.8) -> AIReview:
    return AIReview(
        file_path=file_path, line_number=line_number, severity=severity,
        issue=issue, suggestion="fix it", confidence=confidence,
    )


def test_empty_findings():
    result = consolidate([])
    assert result.findings == []
    assert result.duplicates_removed == 0


def test_no_duplicates_unchanged():
    findings = [_review(line_number=1), _review(line_number=2), _review(line_number=3)]
    result = consolidate(findings, strategies=[SameFileLineStrategy()])
    assert len(result.findings) == 3
    assert result.duplicates_removed == 0


def test_exact_duplicates_removed():
    a = _review(line_number=10, severity="minor", confidence=0.7)
    b = _review(line_number=10, severity="minor", confidence=0.9)
    result = consolidate([a, b], strategies=[SameFileLineStrategy()])
    assert len(result.findings) == 1
    assert result.duplicates_removed == 1


def test_keeps_highest_confidence_duplicate():
    low = _review(line_number=10, severity="minor", confidence=0.5)
    high = _review(line_number=10, severity="minor", confidence=0.9)
    result = consolidate([low, high], strategies=[SameFileLineStrategy()])
    assert result.findings[0].confidence == 0.9


def test_sorted_by_severity_then_confidence():
    low = _review(severity="low", line_number=1)
    high = _review(severity="high", line_number=2)
    medium = _review(severity="medium", line_number=3)
    result = consolidate([low, high, medium], strategies=[SameFileLineStrategy()])
    severities = [f.severity for f in result.findings]
    assert severities == ["high", "medium", "low"]


def test_normalised_severity_sort_order():
    """B1 regression: _SEVERITY_ORDER must use normalised keys so findings sort correctly."""
    low = _review(severity="low", line_number=1, confidence=0.9)
    high = _review(severity="high", line_number=2, confidence=0.9)
    medium = _review(severity="medium", line_number=3, confidence=0.9)
    info = _review(severity="info", line_number=4, confidence=0.9)
    result = consolidate([info, low, medium, high], strategies=[SameFileLineStrategy()])
    severities = [f.severity for f in result.findings]
    assert severities == ["high", "medium", "low", "info"], (
        "Findings must be sorted high→medium→low→info; old vocab keys get sort key 99"
    )


def test_min_confidence_filter():
    high = _review(confidence=0.9)
    low = _review(confidence=0.2, line_number=20)
    result = consolidate([high, low], min_confidence=0.5, strategies=[SameFileLineStrategy()])
    assert len(result.findings) == 1
    assert result.findings[0].confidence == 0.9


def test_deduplication_ratio():
    findings = [_review(line_number=i % 3, severity="minor") for i in range(6)]
    result = consolidate(findings, strategies=[SameFileLineStrategy()])
    assert result.original_count == 6
    assert result.deduplication_ratio > 0


def test_different_files_not_deduplicated():
    a = _review(file_path="a.py", line_number=10, severity="minor")
    b = _review(file_path="b.py", line_number=10, severity="minor")
    result = consolidate([a, b], strategies=[SameFileLineStrategy()])
    assert len(result.findings) == 2


# ---------------------------------------------------------------------------
# _parse_thread_decisions — fence stripping
# ---------------------------------------------------------------------------

def test_parse_thread_decisions_plain_json():
    raw = '[{"fingerprint": "abc", "decision": "wont_fix", "reply_draft": "ok"}]'
    result = _parse_thread_decisions(raw)
    assert result == [{"fingerprint": "abc", "decision": "wont_fix", "reply_draft": "ok"}]


def test_parse_thread_decisions_fenced_json():
    raw = '```json\n[{"fingerprint": "abc", "decision": "wont_fix", "reply_draft": ""}]\n```'
    result = _parse_thread_decisions(raw)
    assert result == [{"fingerprint": "abc", "decision": "wont_fix", "reply_draft": ""}]


def test_parse_thread_decisions_fence_with_trailing_rationale():
    """AI sometimes appends prose after the closing fence — must not crash."""
    raw = (
        '```json\n'
        '[{"fingerprint": "abc", "decision": "already_handled", "reply_draft": ""}]\n'
        '```\n'
        '\n'
        '**Rationale:** The thread contains a bot acknowledgment reply.\n'
    )
    result = _parse_thread_decisions(raw)
    assert result == [{"fingerprint": "abc", "decision": "already_handled", "reply_draft": ""}]


def test_parse_thread_decisions_missing_closing_fence():
    """Opening fence present but no closing fence — treat all inner lines as JSON."""
    raw = '```json\n[{"fingerprint": "abc", "decision": "wont_fix", "reply_draft": ""}]'
    result = _parse_thread_decisions(raw)
    assert result == [{"fingerprint": "abc", "decision": "wont_fix", "reply_draft": ""}]


# ---------------------------------------------------------------------------
# _REPLY_THREAD_SYSTEM_PROMPT — pattern guidance (REVUE-174)
# ---------------------------------------------------------------------------

from revue.core.dedup_consolidator import _REPLY_THREAD_SYSTEM_PROMPT  # noqa: E402


def test_prompt_contains_invariant_prose_guidance():
    """TC1 / AC1: Prompt must instruct Nova to write self-contained invariant prose."""
    prompt_lower = _REPLY_THREAD_SYSTEM_PROMPT.lower()
    assert any(word in prompt_lower for word in ("invariant", "self-contained", "design choice")), (
        "_REPLY_THREAD_SYSTEM_PROMPT must describe the invariant-prose requirement"
    )
    assert any(phrase in prompt_lower for phrase in ("generic", "not broad", "not generic")), (
        "_REPLY_THREAD_SYSTEM_PROMPT must explicitly prohibit generic labels"
    )


def test_prompt_prohibits_file_paths_and_line_numbers():
    """TC2 / AC2: Prompt must explicitly forbid file paths, line numbers, and variable names."""
    prompt_lower = _REPLY_THREAD_SYSTEM_PROMPT.lower()
    assert "line number" in prompt_lower or "line numbers" in prompt_lower, (
        "_REPLY_THREAD_SYSTEM_PROMPT must prohibit line numbers"
    )
    assert "file path" in prompt_lower or "file paths" in prompt_lower, (
        "_REPLY_THREAD_SYSTEM_PROMPT must prohibit file paths"
    )
    assert any(phrase in prompt_lower for phrase in ("stale", "refactor", "refactored")), (
        "_REPLY_THREAD_SYSTEM_PROMPT must explain why (becomes stale on refactor)"
    )


def test_prompt_contains_specificity_rule():
    """TC3 / AC3: Prompt must state that pattern applies only to the described design choice."""
    prompt_lower = _REPLY_THREAD_SYSTEM_PROMPT.lower()
    assert any(phrase in prompt_lower for phrase in ("specific enough", "only to", "unrelated")), (
        "_REPLY_THREAD_SYSTEM_PROMPT must include a specificity rule"
    )


def test_prompt_applies_rules_to_rationale():
    """TC4 / AC4: Prompt must apply the same prose rules to rationale field."""
    prompt_lower = _REPLY_THREAD_SYSTEM_PROMPT.lower()
    # Rationale section must appear after the pattern guidance
    pattern_idx = prompt_lower.find("pattern")
    rationale_idx = prompt_lower.find("rationale")
    assert rationale_idx != -1, "_REPLY_THREAD_SYSTEM_PROMPT must mention rationale"
    # The prompt must address rationale with the same no-line-number / no-path rules
    # Check that after the first mention of rationale, the same constraints appear
    tail = prompt_lower[rationale_idx:]
    assert any(phrase in tail for phrase in ("line number", "file path", "invariant", "refactor")), (
        "_REPLY_THREAD_SYSTEM_PROMPT must apply no-specifics / invariant rules to rationale"
    )


# ---------------------------------------------------------------------------
# REVUE-185 — AC3/TC1: synthesis runs before min_confidence filter
# ---------------------------------------------------------------------------

def _mock_client_returning(response_json: str) -> MagicMock:
    """Mock AIClient whose complete() returns a text attribute with the given JSON."""
    client = MagicMock()
    result = MagicMock()
    result.text = response_json
    client.complete.return_value = result
    return client


def test_synthesis_before_confidence_filter() -> None:
    """AC3/TC1: A low-confidence finding that forms a contradiction group with a
    high-confidence partner must not be dropped by the confidence filter before
    synthesis runs.

    Kai(confidence=0.6) + Zara(confidence=0.9) on the same file+line,
    min_confidence=0.8 → synthesised finding (confidence=0.9) is returned.
    """
    kai = AIReview(
        file_path="app.py",
        line_number=10,
        severity="high",
        issue="Kai issue",
        suggestion="Kai fix",
        confidence=0.6,
        agent_name="kai",
    )
    zara = AIReview(
        file_path="app.py",
        line_number=10,
        severity="high",
        issue="Zara issue",
        suggestion="Zara fix",
        confidence=0.9,
        agent_name="zara",
    )

    mock_response = (
        '[{"file": "app.py", "line": 10, '
        '"issue": "Synthesised issue", "suggestion": "Combined fix"}]'
    )
    client = _mock_client_returning(mock_response)

    result = consolidate([kai, zara], min_confidence=0.8, ai_client=client)

    assert len(result.findings) == 1, (
        "Synthesised finding (confidence=0.9) must survive min_confidence=0.8 filter; "
        "got empty result — synthesis likely ran after the filter dropped Kai first"
    )
    synthesised = result.findings[0]
    assert synthesised.confidence == 0.9
    assert synthesised.agent_name == "nova"
    assert result.original_count == 2
    assert result.duplicates_removed == 1, (
        "Synthesis collapsed 2 → 1 finding; that collapse must be counted in duplicates_removed "
        "so original_count - duplicates_removed == len(findings)"
    )
    assert len(result.synthesis_events) == 1
    # Invariant: original_count == len(findings) + duplicates_removed
    assert result.original_count == len(result.findings) + result.duplicates_removed
    client.complete.assert_called_once()


def test_synthesis_all_below_threshold_after_collapse() -> None:
    """Boundary: synthesis collapses two findings but the result is still below min_confidence.

    Kai(0.3) + Zara(0.4), min_confidence=0.8 → synthesised confidence=0.4 (max of group)
    which still fails the filter. Result must be empty.
    """
    kai = AIReview(
        file_path="app.py", line_number=5, severity="minor",
        issue="Kai", suggestion="fix", confidence=0.3, agent_name="kai",
    )
    zara = AIReview(
        file_path="app.py", line_number=5, severity="minor",
        issue="Zara", suggestion="fix", confidence=0.4, agent_name="zara",
    )
    mock_response = '[{"file": "app.py", "line": 5, "issue": "Synthesised", "suggestion": "fix"}]'
    client = _mock_client_returning(mock_response)

    result = consolidate([kai, zara], min_confidence=0.8, ai_client=client)

    assert result.findings == [], "Synthesised finding (confidence=0.4) must be dropped by filter"
    assert result.original_count == 2
    assert result.duplicates_removed == 2  # 1 synthesis collapse + 1 confidence filter removal
    # Invariant: original_count == len(findings) + duplicates_removed
    assert result.original_count == len(result.findings) + result.duplicates_removed
    client.complete.assert_called_once()


def test_synthesis_three_way_group_survives_filter() -> None:
    """Three-way contradiction group where only one contributor exceeds min_confidence.

    Kai(0.5) + Zara(0.9) + Maya(0.3), min_confidence=0.8 → synthesised confidence=0.9
    (max of group) — survives the filter.
    """
    kai = AIReview(
        file_path="app.py", line_number=20, severity="high",
        issue="Kai", suggestion="fix", confidence=0.5, agent_name="kai",
    )
    zara = AIReview(
        file_path="app.py", line_number=20, severity="high",
        issue="Zara", suggestion="fix", confidence=0.9, agent_name="zara",
    )
    maya = AIReview(
        file_path="app.py", line_number=20, severity="high",
        issue="Maya", suggestion="fix", confidence=0.3, agent_name="maya",
    )
    mock_response = '[{"file": "app.py", "line": 20, "issue": "Synthesised", "suggestion": "fix"}]'
    client = _mock_client_returning(mock_response)

    result = consolidate([kai, zara, maya], min_confidence=0.8, ai_client=client)

    assert len(result.findings) == 1
    assert result.findings[0].confidence == 0.9
    assert result.original_count == 3
    assert result.duplicates_removed == 2  # 3 → 1 collapse: 2 contributors removed
    # Invariant: original_count == len(findings) + duplicates_removed
    assert result.original_count == len(result.findings) + result.duplicates_removed
    client.complete.assert_called_once()
