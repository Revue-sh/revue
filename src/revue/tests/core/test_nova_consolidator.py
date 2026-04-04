"""Tests for Nova consolidation."""
from __future__ import annotations

import json

import pytest

from revue.core.nova_consolidator import (
    consolidate, ConsolidationResult, SameFileLineStrategy,
)
from revue.core.models import AIReview


def _review(file_path="app.py", line_number=10, severity="minor",
            issue="test issue", confidence=0.8, suggestion="fix it",
            category="general") -> AIReview:
    return AIReview(
        file_path=file_path, line_number=line_number, severity=severity,
        issue=issue, suggestion=suggestion, confidence=confidence,
        category=category,
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


# ---------- coordinate-based merge tests ----------

class TestCoordinateMerge:
    """Tests for (file, line) coordinate-based deduplication."""

    def test_same_file_line_two_agents_merged_to_one(self):
        """Two agents flagging the same file+line → 1 merged comment."""
        leo = _review(
            file_path="app.py", line_number=42, severity="minor",
            issue="Variable is unused", suggestion="Remove it",
            confidence=0.8, category="Leo",
        )
        zara = _review(
            file_path="app.py", line_number=42, severity="major",
            issue="Dead code detected", suggestion="Delete the block",
            confidence=0.7, category="Zara",
        )
        result = consolidate([leo, zara], strategies=[SameFileLineStrategy()])

        assert len(result.findings) == 1
        assert result.duplicates_removed == 1
        merged = result.findings[0]
        assert merged.file_path == "app.py"
        assert merged.line_number == 42
        # Highest severity wins
        assert merged.severity == "major"
        # Max confidence
        assert merged.confidence == 0.8
        # Both agents attributed in issue text
        assert "*Zara:*" in merged.issue
        assert "*Leo:*" in merged.issue
        # Both suggestions attributed
        assert "Remove it" in merged.suggestion
        assert "Delete the block" in merged.suggestion

    def test_same_file_different_lines_stay_separate(self):
        """Same file but different lines → 2 separate comments."""
        a = _review(
            file_path="app.py", line_number=10, severity="minor",
            issue="Issue at line 10", category="Leo",
        )
        b = _review(
            file_path="app.py", line_number=20, severity="minor",
            issue="Issue at line 20", category="Zara",
        )
        result = consolidate([a, b], strategies=[SameFileLineStrategy()])

        assert len(result.findings) == 2
        assert result.duplicates_removed == 0

    def test_three_agents_same_coordinate_one_comment(self):
        """Three agents on the same line → still one comment."""
        findings = [
            _review(file_path="db.py", line_number=5, severity="critical",
                    issue="SQL injection risk", suggestion="Use params",
                    confidence=0.95, category="Zara"),
            _review(file_path="db.py", line_number=5, severity="major",
                    issue="Unsanitised input", suggestion="Validate input",
                    confidence=0.85, category="Leo"),
            _review(file_path="db.py", line_number=5, severity="minor",
                    issue="Consider using ORM", suggestion="Use SQLAlchemy",
                    confidence=0.6, category="Maya"),
        ]
        result = consolidate(findings, strategies=[SameFileLineStrategy()])

        assert len(result.findings) == 1
        assert result.duplicates_removed == 2
        merged = result.findings[0]
        assert merged.severity == "critical"
        assert merged.confidence == 0.95
        assert "*Zara:*" in merged.issue
        assert "*Leo:*" in merged.issue
        assert "*Maya:*" in merged.issue

    def test_merge_deduplicates_identical_issue_text(self):
        """If two agents produce identical issue text, it appears only once."""
        a = _review(file_path="x.py", line_number=1, severity="minor",
                    issue="Unused import", suggestion="Remove it",
                    confidence=0.8, category="Leo")
        b = _review(file_path="x.py", line_number=1, severity="minor",
                    issue="Unused import", suggestion="Remove it",
                    confidence=0.7, category="Zara")
        result = consolidate([a, b], strategies=[SameFileLineStrategy()])

        assert len(result.findings) == 1
        merged = result.findings[0]
        # Identical text → only one attributed entry (from the first agent seen)
        assert merged.issue.count("Unused import") == 1

    def test_different_severity_same_line_still_merged(self):
        """Different severities at the same coordinate must merge (the old bug)."""
        minor = _review(file_path="a.py", line_number=7, severity="minor",
                        issue="Style nit", category="Maya", confidence=0.5)
        critical = _review(file_path="a.py", line_number=7, severity="critical",
                           issue="Security flaw", category="Zara", confidence=0.9)
        result = consolidate([minor, critical], strategies=[SameFileLineStrategy()])

        # Must be 1, not 2 — this was the bug
        assert len(result.findings) == 1
        assert result.findings[0].severity == "critical"


# ---------- mock AI clients for batch synthesis tests ----------

class _MockAIClient:
    """Mock AI client that returns a canned response."""
    def __init__(self, response: str):
        self._response = response
        self.calls: list[list[dict]] = []

    def complete(self, messages, *, max_tokens=4096, temperature=0.3):
        self.calls.append(messages)
        return self._response


class _FailingAIClient:
    """Mock AI client that always raises."""
    def complete(self, messages, *, max_tokens=4096, temperature=0.3):
        raise RuntimeError("LLM unavailable")


# ---------- batch synthesis tests ----------

class TestBatchSynthesis:
    """Tests for the batch LLM synthesis pipeline."""

    def test_all_singletons_zero_llm_calls(self):
        """All singletons → zero LLM calls (ai_client.complete not called)."""
        client = _MockAIClient('should not be called')
        findings = [
            _review(file_path="a.py", line_number=1, category="Leo"),
            _review(file_path="b.py", line_number=2, category="Maya"),
            _review(file_path="c.py", line_number=3, category="Zara"),
        ]
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=client,
        )
        assert len(result.findings) == 3
        assert len(client.calls) == 0

    def test_one_conflict_group_one_llm_call(self):
        """One conflict group → one LLM call, result matched by file+line."""
        response = json.dumps([
            {"file": "app.py", "line": 42,
             "issue": "Synthesised issue", "suggestion": "Synthesised fix"},
        ])
        client = _MockAIClient(response)
        findings = [
            _review(file_path="app.py", line_number=42, severity="major",
                    issue="Issue A", suggestion="Fix A", confidence=0.8, category="Leo"),
            _review(file_path="app.py", line_number=42, severity="minor",
                    issue="Issue B", suggestion="Fix B", confidence=0.7, category="Maya"),
        ]
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=client,
        )
        assert len(result.findings) == 1
        assert result.findings[0].issue == "Synthesised issue"
        assert result.findings[0].suggestion == "Synthesised fix"
        assert result.findings[0].severity == "major"
        assert result.findings[0].confidence == 0.8
        assert result.findings[0].category == "nova"
        assert len(client.calls) == 1

    def test_two_conflict_groups_one_llm_call(self):
        """Two conflict groups in different files → one LLM call, both matched."""
        response = json.dumps([
            {"file": "app.py", "line": 10,
             "issue": "Synth A", "suggestion": "Fix A"},
            {"file": "db.py", "line": 20,
             "issue": "Synth B", "suggestion": "Fix B"},
        ])
        client = _MockAIClient(response)
        findings = [
            _review(file_path="app.py", line_number=10, severity="major",
                    issue="Issue A1", suggestion="Fix A1", confidence=0.8, category="Leo"),
            _review(file_path="app.py", line_number=10, severity="minor",
                    issue="Issue A2", suggestion="Fix A2", confidence=0.7, category="Maya"),
            _review(file_path="db.py", line_number=20, severity="critical",
                    issue="Issue B1", suggestion="Fix B1", confidence=0.9, category="Zara"),
            _review(file_path="db.py", line_number=20, severity="minor",
                    issue="Issue B2", suggestion="Fix B2", confidence=0.6, category="Leo"),
        ]
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=client,
        )
        assert len(client.calls) == 1
        assert len(result.findings) == 2
        by_file = {f.file_path: f for f in result.findings}
        assert by_file["app.py"].issue == "Synth A"
        assert by_file["app.py"].category == "nova"
        assert by_file["db.py"].issue == "Synth B"
        assert by_file["db.py"].severity == "critical"
        assert by_file["db.py"].confidence == 0.9

    def test_malformed_json_fallback_concatenation(self):
        """LLM returns malformed JSON → fallback concatenation for all."""
        client = _MockAIClient("This is not JSON at all")
        findings = [
            _review(file_path="app.py", line_number=10, severity="major",
                    issue="Issue A", suggestion="Fix A", confidence=0.9, category="Leo"),
            _review(file_path="app.py", line_number=10, severity="minor",
                    issue="Issue B", suggestion="Fix B", confidence=0.7, category="Maya"),
        ]
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=client,
        )
        assert len(result.findings) == 1
        merged = result.findings[0]
        assert "*Leo:*" in merged.issue
        assert "*Maya:*" in merged.issue
        assert merged.severity == "major"

    def test_missing_group_partial_fallback(self):
        """LLM response missing one group → that group falls back, others synthesised."""
        response = json.dumps([
            {"file": "app.py", "line": 10,
             "issue": "Synth A", "suggestion": "Fix A"},
            # db.py group is missing from response
        ])
        client = _MockAIClient(response)
        findings = [
            _review(file_path="app.py", line_number=10, severity="major",
                    issue="Issue A1", suggestion="Fix A1", confidence=0.8, category="Leo"),
            _review(file_path="app.py", line_number=10, severity="minor",
                    issue="Issue A2", suggestion="Fix A2", confidence=0.7, category="Maya"),
            _review(file_path="db.py", line_number=20, severity="critical",
                    issue="Issue B1", suggestion="Fix B1", confidence=0.9, category="Zara"),
            _review(file_path="db.py", line_number=20, severity="minor",
                    issue="Issue B2", suggestion="Fix B2", confidence=0.6, category="Leo"),
        ]
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=client,
        )
        assert len(result.findings) == 2
        by_file = {f.file_path: f for f in result.findings}
        # app.py was synthesised
        assert by_file["app.py"].issue == "Synth A"
        assert by_file["app.py"].category == "nova"
        # db.py fell back to concatenation
        assert "*Zara:*" in by_file["db.py"].issue
        assert by_file["db.py"].severity == "critical"

    def test_chunked_batches_over_max_batch_size(self):
        """Groups > MAX_BATCH_SIZE → chunked into multiple calls."""
        findings = []
        response_entries = []
        for i in range(51):
            findings.append(_review(
                file_path=f"file_{i}.py", line_number=1, severity="minor",
                issue=f"Issue {i}a", suggestion=f"Fix {i}a",
                confidence=0.8, category="Leo",
            ))
            findings.append(_review(
                file_path=f"file_{i}.py", line_number=1, severity="major",
                issue=f"Issue {i}b", suggestion=f"Fix {i}b",
                confidence=0.7, category="Maya",
            ))
            response_entries.append({
                "file": f"file_{i}.py", "line": 1,
                "issue": f"Synth {i}", "suggestion": f"Fix {i}",
            })

        client = _MockAIClient(json.dumps(response_entries))
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=client,
        )
        # 51 groups → 2 batches (50 + 1)
        assert len(client.calls) == 2
        assert len(result.findings) == 51

    def test_no_ai_client_concatenation_fallback(self):
        """No ai_client → concatenation fallback (no crash)."""
        findings = [
            _review(file_path="app.py", line_number=10, severity="major",
                    issue="Issue A", suggestion="Fix A", confidence=0.9, category="Leo"),
            _review(file_path="app.py", line_number=10, severity="minor",
                    issue="Issue B", suggestion="Fix B", confidence=0.7, category="Maya"),
        ]
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=None,
        )
        assert len(result.findings) == 1
        merged = result.findings[0]
        assert "*Leo:*" in merged.issue
        assert "*Maya:*" in merged.issue
        assert merged.severity == "major"

    def test_singleton_plus_conflict_mixed(self):
        """Singleton + conflict mixed → singleton passes through, conflict synthesised."""
        response = json.dumps([
            {"file": "app.py", "line": 10,
             "issue": "Synth", "suggestion": "Synth fix"},
        ])
        client = _MockAIClient(response)
        findings = [
            # Singleton
            _review(file_path="solo.py", line_number=5, severity="minor",
                    issue="Solo issue", suggestion="Solo fix",
                    confidence=0.6, category="Leo"),
            # Conflict group
            _review(file_path="app.py", line_number=10, severity="major",
                    issue="Issue A", suggestion="Fix A",
                    confidence=0.8, category="Leo"),
            _review(file_path="app.py", line_number=10, severity="minor",
                    issue="Issue B", suggestion="Fix B",
                    confidence=0.7, category="Maya"),
        ]
        result = consolidate(
            findings, strategies=[SameFileLineStrategy()], ai_client=client,
        )
        assert len(result.findings) == 2
        by_file = {f.file_path: f for f in result.findings}
        # Singleton passed through unchanged
        assert by_file["solo.py"].issue == "Solo issue"
        assert by_file["solo.py"].category == "Leo"
        # Conflict was synthesised
        assert by_file["app.py"].issue == "Synth"
        assert by_file["app.py"].category == "nova"
        assert len(client.calls) == 1
