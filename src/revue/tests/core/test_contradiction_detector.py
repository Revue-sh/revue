"""Tests for contradiction detection."""
from __future__ import annotations

import pytest

from revue.core.contradiction_detector import (
    Contradiction, ContradictionDetectionResult, ContradictionStrategy,
    SameLineSameFileStrategy, SimilarIssueOpposingConfidenceStrategy,
    detect_contradictions,
)
from revue.core.models import AIReview


def _review(
    file_path: str = "app.py",
    line_number: int = 10,
    severity: str = "minor",
    issue: str = "possible null pointer",
    confidence: float = 0.8,
) -> AIReview:
    return AIReview(
        file_path=file_path, line_number=line_number, severity=severity,
        issue=issue, suggestion="fix it", confidence=confidence,
    )


def test_no_contradictions_in_empty_list():
    result = detect_contradictions([])
    assert not result.has_contradictions
    assert result.contradiction_count == 0


def test_single_finding_no_contradiction():
    result = detect_contradictions([_review()])
    assert not result.has_contradictions


def test_same_line_opposing_severity_detected():
    a = _review(line_number=10, severity="critical")
    b = _review(line_number=10, severity="suggestion")
    result = detect_contradictions([a, b], strategies=[SameLineSameFileStrategy()])
    assert result.has_contradictions


def test_same_line_same_severity_not_contradictory():
    a = _review(line_number=10, severity="minor")
    b = _review(line_number=10, severity="minor")
    result = detect_contradictions([a, b], strategies=[SameLineSameFileStrategy()])
    assert not result.has_contradictions


def test_different_lines_not_contradictory_by_same_line_strategy():
    a = _review(line_number=10, severity="critical")
    b = _review(line_number=20, severity="suggestion")
    result = detect_contradictions([a, b], strategies=[SameLineSameFileStrategy()])
    assert not result.has_contradictions


def test_different_files_not_contradictory():
    a = _review(file_path="a.py", line_number=10, severity="critical")
    b = _review(file_path="b.py", line_number=10, severity="suggestion")
    result = detect_contradictions([a, b], strategies=[SameLineSameFileStrategy()])
    assert not result.has_contradictions


def test_similar_issue_opposing_confidence_detected():
    a = _review(line_number=10, issue="memory leak in loop", confidence=0.9)
    b = _review(line_number=12, issue="memory leak suspected", confidence=0.2)
    result = detect_contradictions([a, b], strategies=[SimilarIssueOpposingConfidenceStrategy()])
    assert result.has_contradictions


def test_no_duplicate_pairs():
    """Same pair should only produce one contradiction, not two."""
    a = _review(line_number=10, severity="critical")
    b = _review(line_number=10, severity="suggestion")
    result = detect_contradictions([a, b], strategies=[SameLineSameFileStrategy()])
    assert result.contradiction_count == 1


def test_multiple_contradictions():
    findings = [
        _review(line_number=5, severity="critical"),
        _review(line_number=5, severity="suggestion"),
        _review(line_number=15, severity="major"),
        _review(line_number=15, severity="suggestion"),
    ]
    result = detect_contradictions(findings, strategies=[SameLineSameFileStrategy()])
    assert result.contradiction_count == 2


def test_strategy_failure_is_non_fatal():
    class _BrokenStrategy:
        def are_contradictory(self, a, b):
            raise RuntimeError("strategy broke")

    result = detect_contradictions([_review(), _review()], strategies=[_BrokenStrategy()])
    assert isinstance(result, ContradictionDetectionResult)


def test_total_findings_count():
    findings = [_review() for _ in range(5)]
    result = detect_contradictions(findings)
    assert result.total_findings == 5
