"""Tests for contradiction resolution."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from revue.core.contradiction_resolver import (
    resolve_contradictions, ResolutionResult, ContradictionResolutionResult,
)
from revue.core.contradiction_detector import (
    Contradiction, ContradictionDetectionResult,
)
from revue.core.models import AIReview


def _review(file_path="app.py", line_number=10, severity="minor",
            issue="test issue", confidence=0.8) -> AIReview:
    return AIReview(
        file_path=file_path, line_number=line_number, severity=severity,
        issue=issue, suggestion="fix it", confidence=confidence,
    )


def _mock_client(response: str) -> MagicMock:
    c = MagicMock()
    c.complete.return_value = response
    return c


def _detection(a: AIReview, b: AIReview) -> ContradictionDetectionResult:
    return ContradictionDetectionResult(
        contradictions=[Contradiction(finding_a=a, finding_b=b, reason="test")],
        total_findings=2,
    )


_VALID_RESOLUTION = json.dumps({
    "winner": "A", "severity": "minor",
    "issue": "resolved issue", "confidence": 0.85, "rationale": "A is more specific",
})


def test_resolve_returns_resolved_finding():
    a, b = _review(), _review(severity="critical")
    detection = _detection(a, b)
    final, result = resolve_contradictions(detection, [a, b], _mock_client(_VALID_RESOLUTION))
    assert any(f.issue == "resolved issue" for f in final)


def test_resolve_removes_contradicted_originals():
    a, b = _review(issue="issue a"), _review(issue="issue b")
    detection = _detection(a, b)
    final, _ = resolve_contradictions(detection, [a, b], _mock_client(_VALID_RESOLUTION))
    issues = [f.issue for f in final]
    assert "issue a" not in issues
    assert "issue b" not in issues


def test_resolve_neither_produces_no_finding():
    response = json.dumps({"winner": "neither", "severity": "minor",
                           "issue": "", "confidence": 0.5, "rationale": "both wrong"})
    a, b = _review(), _review()
    detection = _detection(a, b)
    final, result = resolve_contradictions(detection, [a, b], _mock_client(response))
    assert result.resolutions[0].winner == "neither"
    assert result.resolutions[0].resolved is None


def test_resolve_fallback_on_api_error():
    c = MagicMock()
    c.complete.side_effect = RuntimeError("API down")
    a, b = _review(), _review()
    detection = _detection(a, b)
    final, result = resolve_contradictions(detection, [a, b], c)
    assert result.resolutions[0].error == "API down"
    assert result.unresolved


def test_resolve_keeps_non_contradicted_findings():
    a, b = _review(issue="contradicted a"), _review(issue="contradicted b")
    c = _review(issue="not contradicted", line_number=99)
    detection = _detection(a, b)
    final, _ = resolve_contradictions(detection, [a, b, c], _mock_client(_VALID_RESOLUTION))
    assert any(f.issue == "not contradicted" for f in final)


def test_resolution_result_success_property():
    ok = ResolutionResult(original_a=_review(), original_b=_review(),
                          resolved=None, winner="neither", rationale="")
    err = ResolutionResult(original_a=_review(), original_b=_review(),
                           resolved=None, winner="neither", rationale="", error="boom")
    assert ok.success is True
    assert err.success is False
