"""Tests for deduplication consolidation."""
from __future__ import annotations

import pytest

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
