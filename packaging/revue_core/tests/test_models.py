"""Test revue_core.models exports and structure."""

import pytest


def test_models_severity_enum_exists():
    """Severity enum is exported from revue_core.models."""
    from revue_core.core.models import Severity
    assert Severity.CRITICAL.value == "critical"
    assert Severity.MAJOR.value == "major"
    assert Severity.MINOR.value == "minor"
    assert Severity.SUGGESTION.value == "suggestion"


def test_models_file_change_dataclass_exists():
    """FileChange dataclass is exported and usable."""
    from revue_core.core.models import FileChange
    fc = FileChange(
        file_path="src/main.py",
        change_type="M",
        additions=10,
        deletions=5,
        diff="---\n+++\n",
    )
    assert fc.file_path == "src/main.py"
    assert fc.language == "unknown"


def test_models_ai_review_dataclass_exists():
    """AIReview dataclass is exported and usable."""
    from revue_core.core.models import AIReview
    review = AIReview(
        file_path="src/main.py",
        line_number=10,
        severity="major",
        issue="Unused variable",
        suggestion="Remove unused variable",
        confidence=0.95,
    )
    assert review.file_path == "src/main.py"
    assert review.line_number == 10


def test_models_pr_context_dataclass_exists():
    """PRContext dataclass is exported."""
    from revue_core.core.models import PRContext
    ctx = PRContext(
        platform="github",
        pr_number=123,
        repo_owner="owner",
        repo_name="repo",
        repo_path="/local/path",
    )
    assert ctx.pr_number == 123
