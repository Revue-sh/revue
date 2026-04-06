"""Tests for Nova consolidation."""
from __future__ import annotations

import pytest

from revue.core.nova_consolidator import consolidate, ConsolidationResult, SameFileLineStrategy
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
    minor = _review(severity="minor", line_number=1)
    critical = _review(severity="critical", line_number=2)
    major = _review(severity="major", line_number=3)
    result = consolidate([minor, critical, major], strategies=[SameFileLineStrategy()])
    severities = [f.severity for f in result.findings]
    assert severities == ["critical", "major", "minor"]


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
