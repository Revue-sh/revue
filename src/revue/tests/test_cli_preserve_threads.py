#!/usr/bin/env python3
"""Tests for CLI duplicate comment deduplication (REVUE-110 AC1/AC2/AC4/AC5).

The preserve_threads feature flag was removed in REVUE-110. Deduplication is
now always on via PerPRCommentStore. These tests verify that behaviour.

Strategy:
- Real PerPRCommentStore rooted at tmp_path (via patched os.getcwd)
- Mocked BitbucketAdapter (no real API calls)
- Mocked CommentFileStore (summary tracking — not under test here)
- Mocked parse_diff_file (returns empty list → fingerprint falls back to line_number)
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from revue.comments.json_store import PerPRCommentStore
from revue.comments.models import CommentState


# =====================================================================
# Helpers
# =====================================================================

@dataclass
class _FakeReviewResult:
    file_path: str
    response: str
    error: str = ""


def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        pr_id="42",
        workspace="ws",
        repo_slug="repo",
        bb_username="user",
        bb_token="tok",
        comment_style="per-issue",
        diff="/tmp/fake.diff",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_review_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings, "summary": "ok"})


_FINDING_A = {"severity": "high", "issue": "SQL injection", "line": 10,
              "details": "Unsanitised input", "recommendation": "Use parameterised queries"}
_FINDING_B = {"severity": "medium", "issue": "Unused import", "line": 20,
              "details": "Remove it", "recommendation": "Delete line"}


def _run(args, review_results, *, tmp_path, adapter=None, pre_seed=None, existing_comments=None):
    """Call _post_to_bitbucket with real PerPRCommentStore (rooted at tmp_path).

    pre_seed:           optional callable(PerPRCommentStore) to add entries before the call.
    existing_comments:  list of raw comment dicts returned by get_existing_comments (default []).
    """
    from revue.cli import _post_to_bitbucket

    mock_adapter = adapter or MagicMock()
    mock_adapter.post_review_comment.return_value = "new-id-1"
    mock_adapter.post_summary_comment.return_value = "summary-id"
    mock_adapter.update_comment.return_value = False
    mock_adapter.resolve_inline_comment.return_value = True
    mock_adapter.get_existing_comments.return_value = existing_comments or []

    mock_summary_store = MagicMock()
    mock_summary_store.get_summary_for_pr.return_value = None

    if pre_seed:
        pre_seed(PerPRCommentStore(tmp_path))

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch("revue.core.bitbucket_adapter.BitbucketAdapter", return_value=mock_adapter),
        patch("revue.comments.file_store.CommentFileStore", return_value=mock_summary_store),
        patch("revue.core.diff_parser.parse_diff_file", return_value=[]),
    ):
        _post_to_bitbucket(args, review_results)

    return mock_adapter


def _run_github(review_results, *, tmp_path, adapter=None, pre_seed=None, pr_id="42"):
    """Call _post_to_github with real PerPRCommentStore (rooted at tmp_path)."""
    from revue.cli import _post_to_github

    args = argparse.Namespace(
        pr_id=pr_id,
        comment_style="per-issue",
        diff="/tmp/fake.diff",
    )
    mock_adapter = adapter or MagicMock()
    mock_adapter.post_review_comment.return_value = "gh-id-1"
    mock_adapter.post_summary_comment.return_value = "gh-summary-id"
    mock_adapter.update_comment.return_value = False
    mock_adapter.resolve_inline_comment.return_value = True
    mock_adapter.get_existing_comments.return_value = []

    mock_summary_store = MagicMock()
    mock_summary_store.get_summary_for_pr.return_value = None

    if pre_seed:
        pre_seed(PerPRCommentStore(tmp_path))

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch.dict(os.environ, {"GITHUB_TOKEN": "tok", "GITHUB_REPOSITORY": "ws/repo"}, clear=False),
        patch("revue.core.github_adapter.GitHubAdapter", return_value=mock_adapter),
        patch("revue.comments.file_store.CommentFileStore", return_value=mock_summary_store),
        patch("revue.core.diff_parser.parse_diff_file", return_value=[]),
    ):
        _post_to_github(args, review_results)

    return mock_adapter


def _run_gitlab(review_results, *, tmp_path, adapter=None, pre_seed=None, pr_id="42"):
    """Call _post_to_gitlab with real PerPRCommentStore (rooted at tmp_path)."""
    from revue.cli import _post_to_gitlab

    args = argparse.Namespace(
        pr_id=pr_id,
        comment_style="per-issue",
        diff="/tmp/fake.diff",
    )
    mock_adapter = adapter or MagicMock()
    mock_adapter.post_review_comment.return_value = "gl-id-1"
    mock_adapter.post_summary_comment.return_value = "gl-summary-id"
    mock_adapter.update_comment.return_value = False
    mock_adapter.resolve_inline_comment.return_value = True
    mock_adapter.get_existing_comments.return_value = []

    mock_summary_store = MagicMock()
    mock_summary_store.get_summary_for_pr.return_value = None

    if pre_seed:
        pre_seed(PerPRCommentStore(tmp_path))

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch.dict(os.environ, {"GITLAB_TOKEN": "tok", "CI_PROJECT_PATH": "ws/repo"}, clear=False),
        patch("revue.core.gitlab_adapter.GitLabAdapter", return_value=mock_adapter),
        patch("revue.comments.file_store.CommentFileStore", return_value=mock_summary_store),
        patch("revue.core.diff_parser.parse_diff_file", return_value=[]),
    ):
        _post_to_gitlab(args, review_results)

    return mock_adapter


# =====================================================================
# TC2: No store file → all findings posted, file created
# =====================================================================

def test_no_existing_store_all_findings_posted(tmp_path) -> None:
    """First review: no .json file exists → all findings posted and file created."""
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A, _FINDING_B]),
        )
    ]
    adapter = _run(_make_args(), review_results, tmp_path=tmp_path)

    assert adapter.post_review_comment.call_count == 2
    # Store file must now exist
    store_file = tmp_path / ".revue" / "comments" / "bitbucket-PR-42.json"
    assert store_file.exists()


# =====================================================================
# TC1: Existing fingerprint → post NOT called (AC1)
# =====================================================================

def test_existing_fingerprint_skips_post(tmp_path, capsys) -> None:
    """Re-review: fingerprint already in store → post_review_comment NOT called."""
    # Pre-seed the store with FINDING_A's fingerprint.
    # fingerprint(file_path, line, diff="") → sha256("src/app.py:10")[:16]
    from revue.comments.fingerprint import fingerprint as fp_func

    pre_fp = fp_func("src/app.py", 10, "")

    def pre_seed(store: PerPRCommentStore):
        store.save_finding(
            platform="bitbucket", pr_number=42,
            file_path="src/app.py", fingerprint=pre_fp,
            platform_comment_id="old-id-99", line_number=10,
            comment_body="old body",
        )

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),  # same finding
        )
    ]
    adapter = _run(_make_args(), review_results, tmp_path=tmp_path, pre_seed=pre_seed)

    # Already posted → must be skipped
    adapter.post_review_comment.assert_not_called()

    # Output must mention "preserved"
    captured = capsys.readouterr()
    assert "preserved" in captured.out.lower()


# =====================================================================
# TC4: New finding → posted and saved to store (AC2)
# =====================================================================

def test_new_finding_posted_and_saved_to_store(tmp_path) -> None:
    """New finding: posted to platform and saved to PerPRCommentStore."""
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        )
    ]
    adapter = _run(_make_args(), review_results, tmp_path=tmp_path)

    assert adapter.post_review_comment.call_count == 1

    store = PerPRCommentStore(tmp_path)
    from revue.comments.fingerprint import fingerprint as fp_func
    fp = fp_func("src/app.py", 10, "")
    assert store.has_fingerprint("bitbucket", 42, "src/app.py", fp)


# =====================================================================
# TC_FRESH_CI: Empty local store + API sentinel → finding skipped (no re-post)
# =====================================================================

def test_api_sentinel_deduplicates_on_fresh_ci(tmp_path) -> None:
    """Fresh CI: local store is empty but the finding was already posted (detected
    via inline comment metadata).  post_review_comment must NOT be called again."""
    from revue.comments.fingerprint import fingerprint as fp_func

    fp = fp_func("src/app.py", 10, "")

    # API already has this finding from a prior run — detected via inline metadata
    existing = [
        {
            "id": 999,
            "inline": {"path": "src/app.py", "to": 10},
            "content": {"raw": "**🔴 [HIGH] SQL injection"},
        }
    ]

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        )
    ]
    adapter = _run(_make_args(), review_results, tmp_path=tmp_path, existing_comments=existing)

    # Already posted in a previous run — must be skipped even with empty local store
    adapter.post_review_comment.assert_not_called()


# =====================================================================
# TC5: Fixed finding → auto-resolve called (AC5)
# =====================================================================

def test_fixed_finding_triggers_auto_resolve(tmp_path) -> None:
    """Finding in store + absent from new review → resolve_inline_comment called."""
    from revue.comments.fingerprint import fingerprint as fp_func

    old_fp = fp_func("src/app.py", 10, "")

    def pre_seed(store: PerPRCommentStore):
        store.save_finding(
            platform="bitbucket", pr_number=42,
            file_path="src/app.py", fingerprint=old_fp,
            platform_comment_id="old-comment-77", line_number=10,
            comment_body="old finding",
        )

    # New review has NO findings for this file → old finding is now fixed
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([]),
        )
    ]
    adapter = _run(_make_args(), review_results, tmp_path=tmp_path, pre_seed=pre_seed)

    adapter.resolve_inline_comment.assert_called_once_with(
        pr_id=42,
        comment_id="old-comment-77",
        reply_body="✅ Issue appears to be resolved in latest commit.",
    )

    # State must be updated in store
    store = PerPRCommentStore(tmp_path)
    unresolved = store.get_unresolved_fingerprints("bitbucket", 42)
    assert old_fp not in unresolved


# =====================================================================
# Separate PRs are isolated
# =====================================================================

def test_separate_pr_numbers_are_isolated(tmp_path) -> None:
    """Findings for PR 42 do not bleed into PR 43."""
    from revue.comments.fingerprint import fingerprint as fp_func
    fp = fp_func("src/app.py", 10, "")

    def pre_seed(store: PerPRCommentStore):
        store.save_finding(
            platform="bitbucket", pr_number=42,
            file_path="src/app.py", fingerprint=fp,
            platform_comment_id="id-pr42", line_number=10, comment_body="x",
        )

    # Review against PR 43 — store has nothing for it → should post
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        )
    ]
    adapter = _run(_make_args(pr_id="43"), review_results, tmp_path=tmp_path, pre_seed=pre_seed)

    assert adapter.post_review_comment.call_count == 1


# =====================================================================
# GitHub: AC2 new finding posted + AC1 duplicate skipped (TC4/TC1)
# =====================================================================

def test_github_new_finding_posted(tmp_path) -> None:
    """GitHub: first review → finding posted, stored under platform='github'."""
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        )
    ]
    adapter = _run_github(review_results, tmp_path=tmp_path)

    assert adapter.post_review_comment.call_count == 1

    store = PerPRCommentStore(tmp_path)
    from revue.comments.fingerprint import fingerprint as fp_func
    fp = fp_func("src/app.py", 10, "")
    assert store.has_fingerprint("github", 42, "src/app.py", fp)


def test_github_existing_fingerprint_skips_post(tmp_path) -> None:
    """GitHub: fingerprint already in store → post_review_comment NOT called."""
    from revue.comments.fingerprint import fingerprint as fp_func

    pre_fp = fp_func("src/app.py", 10, "")

    def pre_seed(store: PerPRCommentStore):
        store.save_finding(
            platform="github", pr_number=42,
            file_path="src/app.py", fingerprint=pre_fp,
            platform_comment_id="gh-old-99", line_number=10,
            comment_body="old body",
        )

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        )
    ]
    adapter = _run_github(review_results, tmp_path=tmp_path, pre_seed=pre_seed)

    adapter.post_review_comment.assert_not_called()


def test_github_fixed_finding_triggers_auto_resolve(tmp_path) -> None:
    """GitHub: finding in store + absent from new review → resolve_inline_comment called."""
    from revue.comments.fingerprint import fingerprint as fp_func

    old_fp = fp_func("src/app.py", 10, "")

    def pre_seed(store: PerPRCommentStore):
        store.save_finding(
            platform="github", pr_number=42,
            file_path="src/app.py", fingerprint=old_fp,
            platform_comment_id="gh-comment-77", line_number=10,
            comment_body="old finding",
        )

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([]),
        )
    ]
    adapter = _run_github(review_results, tmp_path=tmp_path, pre_seed=pre_seed)

    adapter.resolve_inline_comment.assert_called_once_with(
        pr_id=42,
        comment_id="gh-comment-77",
        reply_body="✅ Issue appears to be resolved in latest commit.",
    )

    store = PerPRCommentStore(tmp_path)
    unresolved = store.get_unresolved_fingerprints("github", 42)
    assert old_fp not in unresolved


# =====================================================================
# GitLab: AC2 new finding posted + AC1 duplicate skipped (TC4/TC1)
# =====================================================================

def test_gitlab_new_finding_posted(tmp_path) -> None:
    """GitLab: first review → finding posted, stored under platform='gitlab'."""
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        )
    ]
    adapter = _run_gitlab(review_results, tmp_path=tmp_path)

    assert adapter.post_review_comment.call_count == 1

    store = PerPRCommentStore(tmp_path)
    from revue.comments.fingerprint import fingerprint as fp_func
    fp = fp_func("src/app.py", 10, "")
    assert store.has_fingerprint("gitlab", 42, "src/app.py", fp)


def test_gitlab_existing_fingerprint_skips_post(tmp_path) -> None:
    """GitLab: fingerprint already in store → post_review_comment NOT called."""
    from revue.comments.fingerprint import fingerprint as fp_func

    pre_fp = fp_func("src/app.py", 10, "")

    def pre_seed(store: PerPRCommentStore):
        store.save_finding(
            platform="gitlab", pr_number=42,
            file_path="src/app.py", fingerprint=pre_fp,
            platform_comment_id="gl-old-99", line_number=10,
            comment_body="old body",
        )

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        )
    ]
    adapter = _run_gitlab(review_results, tmp_path=tmp_path, pre_seed=pre_seed)

    adapter.post_review_comment.assert_not_called()


def test_gitlab_fixed_finding_triggers_auto_resolve(tmp_path) -> None:
    """GitLab: finding in store + absent from new review → resolve_inline_comment called."""
    from revue.comments.fingerprint import fingerprint as fp_func

    old_fp = fp_func("src/app.py", 10, "")

    def pre_seed(store: PerPRCommentStore):
        store.save_finding(
            platform="gitlab", pr_number=42,
            file_path="src/app.py", fingerprint=old_fp,
            platform_comment_id="gl-comment-77", line_number=10,
            comment_body="old finding",
        )

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([]),
        )
    ]
    adapter = _run_gitlab(review_results, tmp_path=tmp_path, pre_seed=pre_seed)

    adapter.resolve_inline_comment.assert_called_once_with(
        pr_id=42,
        comment_id="gl-comment-77",
        reply_body="✅ Issue appears to be resolved in latest commit.",
    )

    store = PerPRCommentStore(tmp_path)
    unresolved = store.get_unresolved_fingerprints("gitlab", 42)
    assert old_fp not in unresolved


# =====================================================================
# Comment posting order: summary last (newest-first platforms)
# Bitbucket and GitLab display activity newest-first — summary must be
# posted LAST so it lands at the top of the thread (most visible).
# GitHub displays oldest-first — summary must be posted FIRST.
# =====================================================================

def test_bitbucket_summary_posted_after_inline_comments(tmp_path) -> None:
    """Bitbucket is newest-first: summary comment must be posted AFTER inline comments."""
    import json as _json
    finding = {"severity": "high", "issue": "SQL injection", "line": 10,
               "file_path": "app.py", "details": "x", "recommendation": "y"}
    rr = _FakeReviewResult(
        file_path="app.py",
        response=_json.dumps({"findings": [finding]}),
    )
    mock_adapter = MagicMock()
    mock_adapter.post_review_comment.return_value = "inline-id"
    mock_adapter.post_summary_comment.return_value = "summary-id"
    mock_adapter.get_existing_comments.return_value = []
    mock_adapter.update_comment.return_value = False
    mock_summary_store = MagicMock()
    mock_summary_store.get_summary_for_pr.return_value = None

    call_order = []
    mock_adapter.post_review_comment.side_effect = lambda **kw: (call_order.append("inline"), "inline-id")[1]
    mock_adapter.post_summary_comment.side_effect = lambda **kw: (call_order.append("summary"), "summary-id")[1]

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch("revue.core.bitbucket_adapter.BitbucketAdapter", return_value=mock_adapter),
        patch("revue.comments.file_store.CommentFileStore", return_value=mock_summary_store),
        patch("revue.core.diff_parser.parse_diff_file", return_value=[]),
    ):
        from revue.cli import _post_to_bitbucket
        _post_to_bitbucket(_make_args(), [rr])

    assert "inline" in call_order and "summary" in call_order
    assert call_order.index("summary") > call_order.index("inline"), (
        "Bitbucket: summary must be posted AFTER inline comments (newest-first display)"
    )


def test_gitlab_summary_posted_after_inline_comments(tmp_path) -> None:
    """GitLab is newest-first: summary comment must be posted AFTER inline comments."""
    import json as _json
    finding = {"severity": "high", "issue": "SQL injection", "line": 10,
               "file_path": "app.py", "details": "x", "recommendation": "y"}
    rr = _FakeReviewResult(
        file_path="app.py",
        response=_json.dumps({"findings": [finding]}),
    )
    mock_adapter = MagicMock()
    mock_adapter.post_review_comment.return_value = "inline-id"
    mock_adapter.post_summary_comment.return_value = "summary-id"
    mock_adapter.get_existing_comments.return_value = []
    mock_adapter.update_comment.return_value = False
    mock_summary_store = MagicMock()
    mock_summary_store.get_summary_for_pr.return_value = None

    call_order = []
    mock_adapter.post_review_comment.side_effect = lambda **kw: (call_order.append("inline"), "inline-id")[1]
    mock_adapter.post_summary_comment.side_effect = lambda **kw: (call_order.append("summary"), "summary-id")[1]

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch.dict(os.environ, {"GITLAB_TOKEN": "tok", "CI_PROJECT_PATH": "ws/repo"}, clear=False),
        patch("revue.core.gitlab_adapter.GitLabAdapter", return_value=mock_adapter),
        patch("revue.comments.file_store.CommentFileStore", return_value=mock_summary_store),
        patch("revue.core.diff_parser.parse_diff_file", return_value=[]),
    ):
        from revue.cli import _post_to_gitlab
        _post_to_gitlab(_make_args(), [rr])

    assert "inline" in call_order and "summary" in call_order
    assert call_order.index("summary") > call_order.index("inline"), (
        "GitLab: summary must be posted AFTER inline comments (newest-first display)"
    )


def test_github_summary_posted_before_inline_comments(tmp_path) -> None:
    """GitHub is oldest-first: summary comment must be posted BEFORE inline comments."""
    import json as _json
    finding = {"severity": "high", "issue": "SQL injection", "line": 10,
               "file_path": "app.py", "details": "x", "recommendation": "y"}
    rr = _FakeReviewResult(
        file_path="app.py",
        response=_json.dumps({"findings": [finding]}),
    )
    mock_adapter = MagicMock()
    mock_adapter.post_review_comment.return_value = "inline-id"
    mock_adapter.post_summary_comment.return_value = "summary-id"
    mock_adapter.get_existing_comments.return_value = []
    mock_adapter.update_comment.return_value = False
    mock_summary_store = MagicMock()
    mock_summary_store.get_summary_for_pr.return_value = None

    call_order = []
    mock_adapter.post_review_comment.side_effect = lambda **kw: (call_order.append("inline"), "inline-id")[1]
    mock_adapter.post_summary_comment.side_effect = lambda **kw: (call_order.append("summary"), "summary-id")[1]

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch.dict(os.environ, {"GITHUB_TOKEN": "tok", "GITHUB_REPOSITORY": "ws/repo"}, clear=False),
        patch("revue.core.github_adapter.GitHubAdapter", return_value=mock_adapter),
        patch("revue.comments.file_store.CommentFileStore", return_value=mock_summary_store),
        patch("revue.core.diff_parser.parse_diff_file", return_value=[]),
    ):
        from revue.cli import _post_to_github
        _post_to_github(_make_args(bb_username=None, bb_token=None, workspace=None, repo_slug=None), [rr])

    assert "inline" in call_order and "summary" in call_order
    assert call_order.index("summary") < call_order.index("inline"), (
        "GitHub: summary must be posted BEFORE inline comments (oldest-first display)"
    )


# =====================================================================
# Missing credentials → warning printed, nothing posted (Quinn #6)
# =====================================================================

def test_github_missing_token_prints_warning_and_skips(tmp_path, capsys) -> None:
    """GITHUB_TOKEN absent → stderr warning, post_review_comment never called."""
    from revue.cli import _post_to_github

    args = argparse.Namespace(pr_id="42", comment_style="per-issue", diff="/tmp/fake.diff")
    mock_adapter = MagicMock()

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch.dict(os.environ, {}, clear=True),  # no GITHUB_TOKEN
        patch("revue.core.github_adapter.GitHubAdapter", return_value=mock_adapter),
    ):
        _post_to_github(args, [])

    captured = capsys.readouterr()
    assert "GITHUB_TOKEN" in captured.err
    mock_adapter.post_review_comment.assert_not_called()


def test_gitlab_missing_token_prints_warning_and_skips(tmp_path, capsys) -> None:
    """GITLAB_TOKEN absent → stderr warning, post_review_comment never called."""
    from revue.cli import _post_to_gitlab

    args = argparse.Namespace(pr_id="42", comment_style="per-issue", diff="/tmp/fake.diff")
    mock_adapter = MagicMock()

    with (
        patch("os.getcwd", return_value=str(tmp_path)),
        patch.dict(os.environ, {}, clear=True),  # no GITLAB_TOKEN
        patch("revue.core.gitlab_adapter.GitLabAdapter", return_value=mock_adapter),
    ):
        _post_to_gitlab(args, [])

    captured = capsys.readouterr()
    assert "GITLAB_TOKEN" in captured.err
    mock_adapter.post_review_comment.assert_not_called()
