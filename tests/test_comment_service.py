"""Tests for CommentResolutionService (dismissal detection, summary format)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.revue.comments.models import (
    CommentState,
    Platform,
    PRComment,
    SummaryComment,
)
from src.revue.comments.service import CommentResolutionService


@pytest.fixture
def service(tmp_path):
    """CommentResolutionService backed by a temp directory."""
    return CommentResolutionService(str(tmp_path))


# -- Dismissal detection --

class TestDismissalDetection:
    def test_wont_fix(self, service):
        assert service._is_dismissal("I won't fix this because it's intentional")

    def test_wontfix(self, service):
        assert service._is_dismissal("wontfix - legacy code")

    def test_not_fixing(self, service):
        assert service._is_dismissal("Not fixing, keeping as-is")

    def test_intentional(self, service):
        assert service._is_dismissal("This is intentional per our architecture")

    def test_keeping_as_is(self, service):
        assert service._is_dismissal("keeping as-is for now")

    def test_not_relevant(self, service):
        assert service._is_dismissal("not relevant to this PR")

    def test_positive_reply_not_dismissal(self, service):
        assert not service._is_dismissal("I fixed this in the latest commit")

    def test_acknowledgment_not_dismissal(self, service):
        assert not service._is_dismissal("Good catch, will update")


# -- Summary formatting --

class TestSummaryFormat:
    def test_summary_progress_percentage(self):
        summary = SummaryComment(
            id=1,
            platform=Platform.GITHUB,
            platform_comment_id="summary_123",
            pr_number=1,
            repo_owner="test",
            repo_name="repo",
            total_issues=10,
            fixed_count=7,
            discussed_count=2,
            remaining_count=1,
            last_updated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )

        formatted = summary.format_summary()

        assert "90% complete" in formatted
        assert "✅ 7 fixed" in formatted
        assert "💬 2 discussed" in formatted
        assert "⏳ 1 remaining" in formatted
        assert "🔍 Total reviewed: 10 issues" in formatted

    def test_summary_100_percent(self):
        summary = SummaryComment(
            id=1,
            platform=Platform.GITHUB,
            platform_comment_id="s1",
            pr_number=1,
            repo_owner="t",
            repo_name="r",
            total_issues=5,
            fixed_count=5,
            discussed_count=0,
            remaining_count=0,
            last_updated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        formatted = summary.format_summary()
        assert "100% complete" in formatted
        assert "Ready to merge" in formatted

    def test_summary_zero_issues(self):
        summary = SummaryComment(
            id=1,
            platform=Platform.GITHUB,
            platform_comment_id="s1",
            pr_number=1,
            repo_owner="t",
            repo_name="r",
            total_issues=0,
            fixed_count=0,
            discussed_count=0,
            remaining_count=0,
            last_updated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        assert summary.progress_percentage == 0
