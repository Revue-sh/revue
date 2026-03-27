"""Tests for noise filters."""
from __future__ import annotations

import pytest

from AIReviewer.core.noise_filters import (
    apply_noise_filters, LowConfidenceFilter, TestFileFilter,
    GeneratedFileFilter, EmptyIssueFilter, FilterResult,
)
from AIReviewer.core.models import AIReview


def _review(file_path="app.py", severity="minor",
            issue="test issue", confidence=0.8) -> AIReview:
    return AIReview(
        file_path=file_path, line_number=10, severity=severity,
        issue=issue, suggestion="fix it", confidence=confidence,
    )


def test_no_filters_keeps_all():
    findings = [_review(), _review(file_path="b.py")]
    result = apply_noise_filters(findings, filters=[])
    assert result.kept_count == 2
    assert result.suppressed_count == 0


def test_low_confidence_suppressed():
    low = _review(confidence=0.3)
    high = _review(confidence=0.9, file_path="b.py")
    result = apply_noise_filters([low, high], filters=[LowConfidenceFilter(threshold=0.5)])
    assert result.kept_count == 1
    assert result.suppressed_count == 1
    assert result.suppressed[0][1] == "low-confidence"


def test_test_file_suppresses_non_critical():
    test_finding = _review(file_path="tests/test_app.py", severity="minor")
    result = apply_noise_filters([test_finding], filters=[TestFileFilter()])
    assert result.suppressed_count == 1


def test_test_file_keeps_critical():
    critical = _review(file_path="tests/test_app.py", severity="critical")
    result = apply_noise_filters([critical], filters=[TestFileFilter()])
    assert result.kept_count == 1


def test_generated_file_suppressed():
    gen = _review(file_path="src/api.pb.go")
    result = apply_noise_filters([gen], filters=[GeneratedFileFilter()])
    assert result.suppressed_count == 1


def test_empty_issue_suppressed():
    empty = _review(issue="   ")
    result = apply_noise_filters([empty], filters=[EmptyIssueFilter()])
    assert result.suppressed_count == 1


def test_filter_failure_is_non_fatal():
    class _BrokenFilter:
        name = "broken"
        def should_suppress(self, review):
            raise RuntimeError("filter broke")

    findings = [_review()]
    result = apply_noise_filters(findings, filters=[_BrokenFilter()])
    assert isinstance(result, FilterResult)
    assert result.kept_count == 1  # not suppressed because filter failed


def test_first_matching_filter_wins():
    """Short-circuit: only first matching filter name recorded."""
    low = _review(confidence=0.3, issue="")
    result = apply_noise_filters(
        [low],
        filters=[LowConfidenceFilter(threshold=0.5), EmptyIssueFilter()]
    )
    assert result.suppressed[0][1] == "low-confidence"


def test_multiple_findings_partial_suppression():
    findings = [
        _review(confidence=0.9),
        _review(confidence=0.2, file_path="b.py"),
        _review(confidence=0.8, file_path="c.py"),
    ]
    result = apply_noise_filters(findings, filters=[LowConfidenceFilter(0.5)])
    assert result.kept_count == 2
    assert result.suppressed_count == 1
