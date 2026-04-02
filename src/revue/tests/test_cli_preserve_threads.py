#!/usr/bin/env python3
"""Tests for CLI comment thread preservation logic (REVUE-104 DoD — Gap 2).

Tests exercise the per-issue comment loop in _post_to_bitbucket() with
the preserve_comment_threads feature flag on and off.

Strategy: mock the adapter, state store, file store, and config so that
we can observe call patterns without real I/O.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call

import pytest

from revue.comments.models import CommentState, Platform


# =====================================================================
# Helpers
# =====================================================================

@dataclass
class _FakeReviewResult:
    """Lightweight stand-in for ReviewResult (avoids importing pipeline)."""
    file_path: str
    response: str
    error: str = ""


def _make_args(**overrides) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for _post_to_bitbucket."""
    defaults = dict(
        pr_id="42",
        workspace="ws",
        repo_slug="repo",
        bb_username="user",
        bb_token="tok",
        comment_style="per-issue",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_review_response(findings: list[dict]) -> str:
    """Return a JSON review response string with the given findings."""
    return json.dumps({"findings": findings, "summary": "ok"})


_FINDING_A = {"severity": "high", "issue": "SQL injection", "line": 10}
_FINDING_B = {"severity": "medium", "issue": "Unused import", "line": 20}


def _make_config(preserve: bool) -> MagicMock:
    """Return a mock AIConfig with preserve_comment_threads flag."""
    cfg = MagicMock()
    cfg.preserve_comment_threads = preserve
    return cfg


# Fake PRComment for state store returns
def _make_pr_comment(fingerprint: str, comment_id: str) -> MagicMock:
    c = MagicMock()
    c.finding_fingerprint = fingerprint
    c.platform_comment_id = comment_id
    return c


# =====================================================================
# Shared patches
# =====================================================================

# We need to patch:
# 1. revue.cli.config (module-global accessed by _post_to_bitbucket)
# 2. revue.core.bitbucket_adapter.BitbucketAdapter (adapter construction)
# 3. revue.comments.file_store.CommentFileStore (summary store)
# 4. revue.comments.state_store.CommentStateStore (thread state)
# 5. revue.comments.fingerprint.fingerprint (deterministic FPs)


def _run_post_to_bitbucket(
    args,
    review_results,
    *,
    preserve: bool,
    mock_adapter: MagicMock | None = None,
    existing_comments: list | None = None,
    fingerprint_side_effect=None,
    capsys=None,
) -> tuple[MagicMock, MagicMock]:
    """Call _post_to_bitbucket with all dependencies mocked.

    Returns (mock_adapter, mock_state_store).
    """
    from revue.cli import _post_to_bitbucket

    adapter = mock_adapter or MagicMock()
    adapter.post_review_comment.return_value = "new-comment-id"
    adapter.post_summary_comment.return_value = "summary-id"
    adapter.update_comment.return_value = False  # no existing summary
    adapter.resolve_inline_comment.return_value = True

    mock_config = _make_config(preserve)

    # Mock FileStore (summary persistence — not under test here)
    mock_file_store = MagicMock()
    mock_file_store.get_summary_for_pr.return_value = None

    # Mock StateStore
    mock_state_store = MagicMock()
    mock_state_store.get_comments_for_pr.return_value = existing_comments or []

    fp_values = fingerprint_side_effect or (lambda fp, ln, iss: f"fp_{fp}_{ln}_{iss[:8]}")

    with (
        patch("revue.cli.config", mock_config, create=True),
        patch("revue.core.bitbucket_adapter.BitbucketAdapter", return_value=adapter),
        patch("revue.comments.file_store.CommentFileStore", return_value=mock_file_store),
        patch("revue.comments.state_store.CommentStateStore", return_value=mock_state_store),
        patch("revue.comments.fingerprint.fingerprint", side_effect=fp_values),
    ):
        _post_to_bitbucket(args, review_results)

    return adapter, mock_state_store


# =====================================================================
# Flag OFF — default behaviour
# =====================================================================


def test_flag_off_posts_all_findings_no_state_store(capsys) -> None:
    """preserve_comment_threads=False: every finding posted, StateStore never used."""
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A, _FINDING_B]),
        ),
    ]

    adapter, state_store = _run_post_to_bitbucket(
        _make_args(), review_results, preserve=False, capsys=capsys,
    )

    # Both findings should be posted
    assert adapter.post_review_comment.call_count == 2

    # CommentStateStore should never have been called
    state_store.get_comments_for_pr.assert_not_called()
    state_store.save_comment.assert_not_called()


# =====================================================================
# Flag ON — new finding
# =====================================================================


def test_flag_on_new_finding_posts_and_saves(capsys) -> None:
    """Flag ON + fingerprint NOT in state: comment posted, then saved to state."""
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        ),
    ]

    adapter, state_store = _run_post_to_bitbucket(
        _make_args(), review_results,
        preserve=True,
        existing_comments=[],  # no prior comments
        capsys=capsys,
    )

    # Finding is new — should be posted
    assert adapter.post_review_comment.call_count == 1

    # And saved to state store
    assert state_store.save_comment.call_count == 1
    save_call = state_store.save_comment.call_args
    assert save_call.kwargs.get("platform_comment_id") == "new-comment-id"


# =====================================================================
# Flag ON — existing finding (AC1: preserve thread)
# =====================================================================


def test_flag_on_existing_finding_skips_post(capsys) -> None:
    """Flag ON + fingerprint IS in state: post_review_comment NOT called (AC1)."""
    # The fingerprint function will be called with (file_path, line, issue)
    # We need the returned fingerprint to match one in existing_comments
    fp_value = "fp_existing"

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        ),
    ]

    existing = [_make_pr_comment(fingerprint=fp_value, comment_id="old-id-999")]

    adapter, state_store = _run_post_to_bitbucket(
        _make_args(), review_results,
        preserve=True,
        existing_comments=existing,
        fingerprint_side_effect=lambda *_args: fp_value,
        capsys=capsys,
    )

    # Existing thread preserved — no new comment posted
    adapter.post_review_comment.assert_not_called()

    # Skipped count should be 1 — check output
    captured = capsys.readouterr()
    assert "preserved" in captured.out.lower() or "1 preserved" in captured.out


# =====================================================================
# Flag ON — fixed finding (AC3: auto-resolve)
# =====================================================================


def test_flag_on_fixed_finding_resolves_comment(capsys) -> None:
    """Flag ON + fingerprint in state but NOT in new review: resolve_inline_comment called (AC3)."""
    # Old finding that no longer appears in review
    old_fp = "fp_old_fixed"
    existing = [_make_pr_comment(fingerprint=old_fp, comment_id="old-comment-77")]

    # New review has NO findings (the old one is fixed)
    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([]),
        ),
    ]

    adapter, state_store = _run_post_to_bitbucket(
        _make_args(), review_results,
        preserve=True,
        existing_comments=existing,
        capsys=capsys,
    )

    # resolve_inline_comment should be called with the old comment ID
    adapter.resolve_inline_comment.assert_called_once_with(
        pr_id=42,
        comment_id="old-comment-77",
        reply_body="\u2705 Issue appears to be resolved in latest commit.",
    )

    # State store transition should record the resolution
    state_store.transition_state.assert_called_once()
    t_call = state_store.transition_state.call_args
    assert t_call.kwargs.get("fingerprint") == old_fp
    assert t_call.kwargs.get("to_state") == CommentState.RESOLVED
    assert t_call.kwargs.get("reason") == "auto-resolved"


# =====================================================================
# Flag ON — output message includes "preserved"
# =====================================================================


def test_flag_on_skipped_output_message_includes_preserved(capsys) -> None:
    """When skipped > 0, stdout includes 'preserved' in the message."""
    fp_value = "fp_dup"
    existing = [_make_pr_comment(fingerprint=fp_value, comment_id="888")]

    review_results = [
        _FakeReviewResult(
            file_path="src/app.py",
            response=_make_review_response([_FINDING_A]),
        ),
    ]

    _run_post_to_bitbucket(
        _make_args(), review_results,
        preserve=True,
        existing_comments=existing,
        fingerprint_side_effect=lambda *_args: fp_value,
        capsys=capsys,
    )

    captured = capsys.readouterr()
    assert "preserved" in captured.out.lower()
