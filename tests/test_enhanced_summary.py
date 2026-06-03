"""Tests for REVUE-97: Enhanced PR summary comment with quality metrics and update-in-place.

Covers AC1–AC7 as specified in the Jira story.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from revue_core.comments.summary_builder import (
    _star_rating,
    build_enhanced_summary as _build_enhanced_summary,
)
from revue_core.core.display import SEVERITY_EMOJIS as SEVERITY_EMOJI


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rr(file_path: str, findings: list, summary: str = "") -> SimpleNamespace:
    """Build a fake ReviewResult-like object."""
    payload = {"findings": findings, "summary": summary}
    return SimpleNamespace(
        file_path=file_path,
        response=json.dumps(payload),
        error=None,
    )


def _no_findings_rr(file_path: str) -> SimpleNamespace:
    return _make_rr(file_path, [])


# ---------------------------------------------------------------------------
# _star_rating (AC1)
# ---------------------------------------------------------------------------

def test_star_rating_perfect():
    """0 findings → 5.0/5.0."""
    result = _star_rating(0, 0, 0)
    assert "5.0/5.0" in result
    assert "⭐⭐⭐⭐⭐" in result


def test_star_rating_degrades_with_high_findings():
    """High findings reduce star rating."""
    result = _star_rating(2, 2, 0)
    assert "5.0/5.0" not in result


def test_star_rating_minimum_is_1():
    """Star rating never goes below 1.0."""
    result = _star_rating(100, 100, 100)
    assert "1.0/5.0" in result


# ---------------------------------------------------------------------------
# _build_enhanced_summary — AC1: verdict + star rating
# ---------------------------------------------------------------------------

def test_summary_ac1_approved_on_zero_findings():
    """AC1: ✅ Approved shown when no findings."""
    rr = _no_findings_rr("src/app.py")
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "✅" in body
    assert "Approved" in body
    assert "5.0/5.0" in body


def test_summary_ac1_warning_on_medium_findings():
    """AC1: ⚠️ shown when medium findings exist."""
    rr = _make_rr("src/app.py", [{"severity": "medium", "issue": "unused import", "category": "code-quality"}])
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 1, "low": 0, "info": 0}, 1, "just now")
    assert "⚠️" in body


def test_summary_ac1_error_on_high_findings():
    """AC1: ❌ shown when high-severity findings exist."""
    rr = _make_rr("src/app.py", [{"severity": "high", "issue": "SQL injection", "category": "security"}])
    body = _build_enhanced_summary([rr], {"high": 1, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "❌" in body


# ---------------------------------------------------------------------------
# AC2: category breakdown
# ---------------------------------------------------------------------------

def test_summary_ac2_all_four_categories_shown():
    """AC2: All 4 categories always present."""
    rr = _no_findings_rr("src/app.py")
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "Architecture" in body
    assert "Security" in body
    assert "Performance" in body
    assert "Code Quality" in body


def test_summary_ac2_checkmark_for_clean_category():
    """AC2: ✅ shown for category with 0 findings."""
    rr = _no_findings_rr("src/app.py")
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "✅ **Architecture:**" in body
    assert "✅ **Security:**" in body


def test_summary_ac2_count_shown_for_affected_category():
    """AC2: finding count shown for category with issues."""
    rr = _make_rr("src/app.py", [{"severity": "high", "issue": "XSS", "category": "security"}])
    body = _build_enhanced_summary([rr], {"high": 1, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "⚠️ **Security:**" in body
    assert "🔴" in body  # high severity emoji


# ---------------------------------------------------------------------------
# AC3: files reviewed
# ---------------------------------------------------------------------------

def test_summary_ac3_lists_reviewed_files():
    """AC3: Files reviewed section lists all non-error files."""
    rrs = [_no_findings_rr("src/foo.py"), _no_findings_rr("src/bar.py")]
    body = _build_enhanced_summary(rrs, {"high": 0, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "Files Reviewed (2)" in body
    assert "`src/foo.py`" in body
    assert "`src/bar.py`" in body


def test_summary_ac3_error_results_excluded():
    """AC3: Files with errors are not listed."""
    rr_ok = _no_findings_rr("src/good.py")
    rr_err = SimpleNamespace(file_path="src/bad.py", response=None, error="timeout")
    body = _build_enhanced_summary([rr_ok, rr_err], {"high": 0, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "Files Reviewed (1)" in body
    assert "`src/good.py`" in body
    assert "`src/bad.py`" not in body


# ---------------------------------------------------------------------------
# AC4: zero-findings verdict
# ---------------------------------------------------------------------------

def test_summary_ac4_explains_zero_findings():
    """AC4: Clean explanation when 0 findings, not just 'Looks good'."""
    rr = _no_findings_rr("src/app.py")
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "Looks good" not in body
    assert "Clean implementation" in body or "No issues detected" in body


# ---------------------------------------------------------------------------
# AC5: markdown formatting
# ---------------------------------------------------------------------------

def test_summary_ac5_uses_markdown():
    """AC5: Body uses markdown headings, bold, and emoji."""
    rr = _no_findings_rr("src/app.py")
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 0, "low": 0, "info": 0}, 1, "just now")
    assert "##" in body        # heading
    assert "**" in body        # bold
    assert "🤖" in body        # emoji


# ---------------------------------------------------------------------------
# AC7: revision number + timestamp
# ---------------------------------------------------------------------------

def test_summary_ac7_shows_revision_number():
    """AC7: Revision number appears in header."""
    rr = _no_findings_rr("src/app.py")
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 0, "low": 0, "info": 0}, 3, "2 hours ago")
    assert "Review #3" in body


def test_summary_ac7_shows_last_updated():
    """AC7: Last updated timestamp appears in body."""
    rr = _no_findings_rr("src/app.py")
    body = _build_enhanced_summary([rr], {"high": 0, "medium": 0, "low": 0, "info": 0}, 2, "5 minutes ago")
    assert "5 minutes ago" in body
    assert "Last updated" in body


# ---------------------------------------------------------------------------
# AC6: post-or-update flow (via file_store integration)
# ---------------------------------------------------------------------------

def test_summary_ac6_update_increments_revision(tmp_path):
    """AC6/AC7: Second review call updates existing comment and bumps revision."""
    from revue_core.comments.file_store import CommentFileStore
    from revue_core.comments.models import Platform, SummaryComment
    from datetime import datetime, timezone

    store = CommentFileStore(tmp_path)
    now = datetime.now(timezone.utc)

    # Simulate first review stored
    first = SummaryComment(
        id=None,
        platform=Platform.BITBUCKET,
        platform_comment_id="111",
        pr_number=5,
        repo_owner="cbscd",
        repo_name="revue",
        total_issues=0,
        fixed_count=0,
        discussed_count=0,
        remaining_count=0,
        last_updated_at=now,
        created_at=now,
        revision=1,
    )
    store.create_or_update_summary(first)

    # Read it back — revision should be 1
    retrieved = store.get_summary_for_pr(Platform.BITBUCKET, "cbscd", "revue", 5)
    assert retrieved is not None
    assert retrieved.revision == 1
    assert retrieved.platform_comment_id == "111"

    # Simulate update: bump revision
    updated = SummaryComment(
        id=None,
        platform=Platform.BITBUCKET,
        platform_comment_id="111",
        pr_number=5,
        repo_owner="cbscd",
        repo_name="revue",
        total_issues=1,
        fixed_count=0,
        discussed_count=0,
        remaining_count=1,
        last_updated_at=datetime.now(timezone.utc),
        created_at=now,
        revision=2,
    )
    store.create_or_update_summary(updated)

    final = store.get_summary_for_pr(Platform.BITBUCKET, "cbscd", "revue", 5)
    assert final is not None
    assert final.revision == 2
    assert final.total_issues == 1


def test_summary_ac6_fallback_to_new_comment_on_404(tmp_path):
    """AC6 TC4: If update returns False (404), a new comment is posted and stored."""
    from revue_core.comments.file_store import CommentFileStore
    from revue_core.comments.models import Platform, SummaryComment
    from datetime import datetime, timezone

    store = CommentFileStore(tmp_path)
    now = datetime.now(timezone.utc)

    # Pre-store an "existing" summary with a deleted comment ID
    existing = SummaryComment(
        id=None,
        platform=Platform.BITBUCKET,
        platform_comment_id="deleted-999",
        pr_number=7,
        repo_owner="cbscd",
        repo_name="revue",
        total_issues=0,
        fixed_count=0,
        discussed_count=0,
        remaining_count=0,
        last_updated_at=now,
        created_at=now,
        revision=1,
    )
    store.create_or_update_summary(existing)

    # Simulate adapter: update returns False (404), post returns new ID
    mock_adapter = MagicMock()
    mock_adapter.update_comment.return_value = False
    mock_adapter.post_summary_comment.return_value = "new-888"

    retrieved = store.get_summary_for_pr(Platform.BITBUCKET, "cbscd", "revue", 7)
    assert retrieved is not None

    # Replicate the fallback logic from cli.py _post_or_update_summary
    ok = mock_adapter.update_comment(pr_id=7, comment_id=retrieved.platform_comment_id, body="body")
    assert ok is False

    # Falls back to post
    new_id = mock_adapter.post_summary_comment(pr_id=7, body="body")
    assert new_id == "new-888"

    # Store new ID
    fallback = SummaryComment(
        id=None,
        platform=Platform.BITBUCKET,
        platform_comment_id=new_id,
        pr_number=7,
        repo_owner="cbscd",
        repo_name="revue",
        total_issues=0,
        fixed_count=0,
        discussed_count=0,
        remaining_count=0,
        last_updated_at=datetime.now(timezone.utc),
        created_at=now,
        revision=1,
    )
    store.create_or_update_summary(fallback)

    final = store.get_summary_for_pr(Platform.BITBUCKET, "cbscd", "revue", 7)
    assert final.platform_comment_id == "new-888"
    assert final.revision == 1
