"""Tests for ClassificationResult dataclass (REVUE-112 Phase 2, AC15)."""
from revue.core.models import ClassificationResult


def test_classification_result_fields():
    """ClassificationResult must have four list[dict] fields."""
    result = ClassificationResult(
        patterns_to_allow=[{"pattern": "null-deref in payment handler", "rationale": "legacy"}],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "abc123", "file_path": "payments.py", "decision": "allowed_pattern"}],
        decisions=[{"fingerprint": "abc123", "decision": "allowed_pattern", "reply_draft": "Noted."}],
    )
    assert result.patterns_to_allow[0]["pattern"] == "null-deref in payment handler"
    assert result.patterns_to_disallow == []
    assert result.state_updates[0]["fingerprint"] == "abc123"
    assert len(result.decisions) == 1


def test_classification_result_empty():
    """Empty ClassificationResult is valid and equals another empty one."""
    empty = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[],
    )
    assert empty == ClassificationResult([], [], [], [])


def test_classification_result_disallowed():
    """disallowed_patterns populated correctly."""
    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[{"pattern": "raw SQL in controllers", "rationale": "must use ORM"}],
        state_updates=[{"fingerprint": "dd99", "file_path": "ctrl.py", "decision": "disallowed_pattern"}],
        decisions=[{"fingerprint": "dd99", "decision": "disallowed_pattern", "reply_draft": "Enforced."}],
    )
    assert result.patterns_to_disallow[0]["rationale"] == "must use ORM"
    assert result.state_updates[0]["decision"] == "disallowed_pattern"
