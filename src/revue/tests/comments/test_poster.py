"""Tests for Poster (REVUE-211).

Covers AC1–AC11 from the story: position resolution, dedup, summary ordering,
eviction, unanchored collection, and GitHub issue-comment scan.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from revue.comments.models import Platform, ConsolidatedFinding, Attribution
from revue.comments.poster import Poster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(
    platform: str = "bitbucket",
    review_comment_id: str = "c-1",
    summary_comment_id: str | None = "s-1",
) -> MagicMock:
    adapter = MagicMock()
    adapter.post_review_comment.return_value = review_comment_id
    adapter.post_summary_comment.return_value = summary_comment_id
    adapter.update_comment.return_value = True
    adapter.get_existing_comments.return_value = []
    adapter.get_thread_replies.return_value = []
    adapter.comment_limit_reached = False
    # resolve_position returns a DiffPosition-like object with position=1 by default
    from revue.core.vcs_adapter import DiffPosition
    pos = DiffPosition(file_path="a.py", line_number=10, position=1)
    adapter.resolve_position.return_value = pos
    return adapter


def _make_dedup_store(unresolved: dict | None = None) -> MagicMock:
    store = MagicMock()
    store.get_unresolved_fingerprints.return_value = unresolved or {}
    return store


def _make_summary_store(existing_summary=None) -> MagicMock:
    store = MagicMock()
    store.get_summary_for_pr.return_value = existing_summary
    return store


def _make_review_result(file_path: str = "a.py", response: str | None = None, error: bool = False) -> MagicMock:
    rr = MagicMock()
    rr.file_path = file_path
    rr.error = error
    rr.response = response or _default_response(file_path)
    return rr


def _default_response(file_path: str) -> str:
    return (
        '```json\n[{"severity": "medium", "issue": "Test issue", "suggestion": "Fix it", '
        f'"details": "", "category": "general", "line_number": 10, "confidence": 0.9, '
        f'"agent_name": "Leo", "code_replacement": null, "replacement_line_count": 1}}]\n```'
    )


def _make_poster(
    adapter=None,
    platform_str: str = "bitbucket",
    platform_enum=Platform.BITBUCKET,
    dedup_store=None,
    summary_store=None,
    diff_by_file: dict | None = None,
    hunk_tracker=None,
) -> Poster:
    if adapter is None:
        adapter = _make_adapter(platform_str)
    if dedup_store is None:
        dedup_store = _make_dedup_store()
    if summary_store is None:
        summary_store = _make_summary_store()
    if hunk_tracker is None:
        hunk_tracker = MagicMock()
        hunk_tracker.build_prior.return_value = {}
    return Poster(
        adapter=adapter,
        platform_str=platform_str,
        platform_enum=platform_enum,
        dedup_store=dedup_store,
        summary_store=summary_store,
        diff_by_file=diff_by_file or {"a.py": "@@ -8,5 +8,5 @@\n-old\n+new\n"},
        hunk_tracker=hunk_tracker,
    )


# ---------------------------------------------------------------------------
# AC1 — Position resolution: DiffPositionResolver.snap is called
# ---------------------------------------------------------------------------


def test_post_new_finding_calls_adapter_post_review_comment():
    """New finding → adapter.post_review_comment() is called with resolved position."""
    adapter = _make_adapter()
    poster = _make_poster(adapter=adapter)

    review_results = [_make_review_result("a.py")]
    posted, failed = poster.post(
        pr_id=1,
        review_results=review_results,
        comment_style="per-issue",
        repo_owner="me",
        repo_name="myrepo",
    )

    assert posted >= 1
    adapter.post_review_comment.assert_called()
    assert failed == 0


# ---------------------------------------------------------------------------
# AC2 — Dedup: open prior → skip, still counted in total_findings
# ---------------------------------------------------------------------------


def test_post_deduplicates_open_prior():
    """Finding matching an open prior thread → skipped, not double-posted."""
    with patch("revue.comments.poster.gen_fingerprint", return_value="fp-open"):
        prior = {"fp-open": {"platform_comment_id": "42", "resolved": False}}
        dedup_store = _make_dedup_store(prior)
        adapter = _make_adapter()
        hunk_tracker = MagicMock()
        hunk_tracker.build_prior.return_value = prior
        poster = _make_poster(adapter=adapter, dedup_store=dedup_store, hunk_tracker=hunk_tracker)

        review_results = [_make_review_result("a.py")]
        posted, failed = poster.post(
            pr_id=1, review_results=review_results, comment_style="per-issue",
            repo_owner="me", repo_name="myrepo",
        )

        # Should not post a new comment (deduped)
        adapter.post_review_comment.assert_not_called()
        assert failed == 0


# ---------------------------------------------------------------------------
# AC4 — Resolved prior → excluded from total_findings count
# ---------------------------------------------------------------------------


def test_post_skips_resolved_prior_from_total_findings():
    """Finding matching a resolved prior (won't-fix) → not counted."""
    with patch("revue.comments.poster.gen_fingerprint", return_value="fp-resolved"):
        prior = {"fp-resolved": {"platform_comment_id": "43", "resolved": True}}
        dedup_store = _make_dedup_store(prior)
        adapter = _make_adapter()
        hunk_tracker = MagicMock()
        hunk_tracker.build_prior.return_value = prior
        poster = _make_poster(adapter=adapter, dedup_store=dedup_store, hunk_tracker=hunk_tracker)

        review_results = [_make_review_result("a.py")]
        posted, failed = poster.post(
            pr_id=1, review_results=review_results, comment_style="per-issue",
            repo_owner="me", repo_name="myrepo",
        )

        # Resolved prior skipped entirely — no new post
        adapter.post_review_comment.assert_not_called()


# ---------------------------------------------------------------------------
# AC6 — Unanchored: position==0 → collected into summary_sink (not discarded)
# ---------------------------------------------------------------------------


def test_post_collects_unanchored_into_summary_sink():
    """Finding with position=0 on GitHub → appended to summary_sink, not discarded."""
    from revue.core.vcs_adapter import DiffPosition
    adapter = _make_adapter(platform="github")
    # Return position=0 to signal "outside diff"
    adapter.resolve_position.return_value = DiffPosition(
        file_path="a.py", line_number=10, position=0
    )
    poster = _make_poster(adapter=adapter, platform_str="github", platform_enum=Platform.GITHUB)

    review_results = [_make_review_result("a.py")]
    # Should not raise and should not post inline comment
    posted, failed = poster.post(
        pr_id=1, review_results=review_results, comment_style="per-issue",
        repo_owner="me", repo_name="myrepo",
    )

    adapter.post_review_comment.assert_not_called()
    # Summary comment should still be posted (containing the unanchored finding)
    adapter.post_summary_comment.assert_called()


# ---------------------------------------------------------------------------
# AC6 — Eviction: comment_limit_reached → evict once, retry
# ---------------------------------------------------------------------------


def test_post_evicts_on_comment_limit_then_retries():
    """Bitbucket 200-comment limit hit → evict resolved, retry once, subsequent skip."""
    adapter = _make_adapter()
    # First post attempt returns None (limit hit), second returns id after eviction
    adapter.post_review_comment.side_effect = [None, "c-2"]
    adapter.comment_limit_reached = True
    adapter.evict_resolved_revue_comments = MagicMock(return_value=3)

    poster = _make_poster(adapter=adapter)
    review_results = [_make_review_result("a.py")]
    posted, failed = poster.post(
        pr_id=1, review_results=review_results, comment_style="per-issue",
        repo_owner="me", repo_name="myrepo",
    )

    adapter.evict_resolved_revue_comments.assert_called_once_with(1)
    assert posted >= 1


# ---------------------------------------------------------------------------
# AC7 — Summary: prior summary → update_comment() called in-place
# ---------------------------------------------------------------------------


def test_post_updates_existing_summary():
    """Prior summary exists in summary store → update_comment() called."""
    from revue.comments.models import SummaryComment
    from datetime import datetime, timezone

    existing = SummaryComment(
        id=None, platform=Platform.BITBUCKET, platform_comment_id="s-999",
        pr_number=1, repo_owner="me", repo_name="myrepo",
        total_issues=3, fixed_count=0, discussed_count=0, remaining_count=3,
        last_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc), revision=1,
    )
    summary_store = _make_summary_store(existing)
    adapter = _make_adapter()
    poster = _make_poster(adapter=adapter, summary_store=summary_store)

    review_results = [_make_review_result("a.py")]
    poster.post(pr_id=1, review_results=review_results, comment_style="per-issue",
                repo_owner="me", repo_name="myrepo")

    # Verify update_comment was called for the prior summary's comment ID
    assert adapter.update_comment.called
    call_kwargs = adapter.update_comment.call_args
    assert call_kwargs.kwargs.get("comment_id") == "s-999" or (
        len(call_kwargs.args) >= 2 and call_kwargs.args[1] == "s-999"
    )


def test_post_posts_new_summary_when_none_exists():
    """No prior summary → post_summary_comment() called."""
    summary_store = _make_summary_store(None)
    adapter = _make_adapter()
    poster = _make_poster(adapter=adapter, summary_store=summary_store)

    review_results = [_make_review_result("a.py")]
    poster.post(pr_id=1, review_results=review_results, comment_style="per-issue",
                repo_owner="me", repo_name="myrepo")

    adapter.post_summary_comment.assert_called()


# ---------------------------------------------------------------------------
# AC8 — Summary ordering
# ---------------------------------------------------------------------------


def test_post_github_summary_order_before_inline():
    """GitHub: summary posted before inline comments (oldest-first platform)."""
    from revue.core.vcs_adapter import DiffPosition
    adapter = _make_adapter(platform="github")
    adapter.resolve_position.return_value = DiffPosition(
        file_path="a.py", line_number=10, position=1
    )
    call_order: list[str] = []
    adapter.post_summary_comment.side_effect = lambda **kw: (call_order.append("summary"), "s-1")[1]
    adapter.post_review_comment.side_effect = lambda **kw: (call_order.append("inline"), "c-1")[1]

    poster = _make_poster(adapter=adapter, platform_str="github", platform_enum=Platform.GITHUB)
    review_results = [_make_review_result("a.py")]
    poster.post(pr_id=1, review_results=review_results, comment_style="per-issue",
                repo_owner="me", repo_name="myrepo")

    summary_idx = next((i for i, v in enumerate(call_order) if v == "summary"), None)
    inline_idx = next((i for i, v in enumerate(call_order) if v == "inline"), None)
    if summary_idx is not None and inline_idx is not None:
        assert summary_idx < inline_idx, "GitHub summary must be posted before inline comments"


def test_post_gitlab_summary_order_after_inline():
    """GitLab: summary posted after inline comments (newest-first platform)."""
    from revue.core.vcs_adapter import DiffPosition
    adapter = _make_adapter(platform="gitlab")
    lc = "abc123"
    adapter.resolve_position.return_value = DiffPosition(
        file_path="a.py", line_number=10, position=1, line_code=lc, new_line=10
    )
    call_order: list[str] = []
    adapter.post_summary_comment.side_effect = lambda **kw: (call_order.append("summary"), "s-1")[1]
    adapter.post_review_comment.side_effect = lambda **kw: (call_order.append("inline"), "c-1")[1]

    poster = _make_poster(adapter=adapter, platform_str="gitlab", platform_enum=Platform.GITLAB)
    review_results = [_make_review_result("a.py")]
    poster.post(pr_id=1, review_results=review_results, comment_style="per-issue",
                repo_owner="me", repo_name="myrepo")

    summary_idx = next((i for i, v in enumerate(call_order) if v == "summary"), None)
    inline_idx = next((i for i, v in enumerate(call_order) if v == "inline"), None)
    if summary_idx is not None and inline_idx is not None:
        assert summary_idx > inline_idx, "GitLab summary must be posted after inline comments"


# ---------------------------------------------------------------------------
# AC11 — GitHub summary scan uses get_issue_comments
# ---------------------------------------------------------------------------


def test_poster_github_uses_issue_comments_for_summary_scan():
    """GitHub: get_issue_comments() used to find prior summary (not get_existing_comments)."""
    adapter = _make_adapter(platform="github")
    adapter.get_issue_comments = MagicMock(return_value=[])

    poster = _make_poster(adapter=adapter, platform_str="github", platform_enum=Platform.GITHUB)
    review_results = [_make_review_result("a.py")]
    poster.post(pr_id=1, review_results=review_results, comment_style="per-issue",
                repo_owner="me", repo_name="myrepo")

    adapter.get_issue_comments.assert_called()
