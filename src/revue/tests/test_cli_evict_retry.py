"""Unit tests for _post_or_evict_and_retry (comment-limit eviction + retry)."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from revue.cli import _post_or_evict_and_retry
from revue.core.vcs_adapter import DiffPosition


def _position() -> DiffPosition:
    return DiffPosition(file_path="src/foo.py", line_number=10, side="RIGHT", position=1)


def _adapter(*, limit_reached: bool = False) -> MagicMock:
    a = MagicMock()
    a.comment_limit_reached = limit_reached
    return a


# ---------------------------------------------------------------------------
# Happy path — post succeeds immediately
# ---------------------------------------------------------------------------

def test_returns_comment_id_on_first_attempt_success() -> None:
    """Returns the comment_id without touching eviction when post succeeds."""
    adapter = _adapter()
    adapter.post_review_comment.return_value = "42"

    result = _post_or_evict_and_retry(adapter, 85, _position(), "body", [False])

    assert result == "42"
    adapter.evict_resolved_revue_comments.assert_not_called()


# ---------------------------------------------------------------------------
# Failure — not a limit error
# ---------------------------------------------------------------------------

def test_returns_none_on_non_limit_failure() -> None:
    """Returns None without eviction when post fails for a non-limit reason."""
    adapter = _adapter(limit_reached=False)
    adapter.post_review_comment.return_value = None

    result = _post_or_evict_and_retry(adapter, 85, _position(), "body", [False])

    assert result is None
    adapter.evict_resolved_revue_comments.assert_not_called()


# ---------------------------------------------------------------------------
# Limit hit — eviction succeeds, retry succeeds
# ---------------------------------------------------------------------------

def test_evicts_and_retries_when_limit_hit(capsys) -> None:
    """Evicts resolved comments and retries the post when the limit is hit."""
    adapter = _adapter(limit_reached=True)
    adapter.post_review_comment.side_effect = [None, "99"]  # fail → succeed
    adapter.evict_resolved_revue_comments.return_value = 3

    state: list[bool] = [False]
    result = _post_or_evict_and_retry(adapter, 85, _position(), "body", state)

    assert result == "99"
    adapter.evict_resolved_revue_comments.assert_called_once_with(85)
    assert adapter.comment_limit_reached is False  # reset after eviction
    assert state[0] is True  # flag consumed

    out = capsys.readouterr().out
    assert "🗑️" in out
    assert "3" in out


# ---------------------------------------------------------------------------
# Limit hit — eviction finds nothing to delete
# ---------------------------------------------------------------------------

def test_returns_none_when_eviction_finds_no_resolved_comments() -> None:
    """Returns None when the limit is hit but there are no resolved threads to evict."""
    adapter = _adapter(limit_reached=True)
    adapter.post_review_comment.return_value = None
    adapter.evict_resolved_revue_comments.return_value = 0

    result = _post_or_evict_and_retry(adapter, 85, _position(), "body", [False])

    assert result is None
    adapter.evict_resolved_revue_comments.assert_called_once_with(85)


# ---------------------------------------------------------------------------
# Limit hit — eviction succeeds but retry still fails
# ---------------------------------------------------------------------------

def test_returns_none_when_retry_fails_after_eviction() -> None:
    """Returns None when eviction freed space but the retry post also fails."""
    adapter = _adapter(limit_reached=True)
    adapter.post_review_comment.return_value = None  # both attempts fail
    adapter.evict_resolved_revue_comments.return_value = 2

    result = _post_or_evict_and_retry(adapter, 85, _position(), "body", [False])

    assert result is None
    assert adapter.post_review_comment.call_count == 2  # initial + retry


# ---------------------------------------------------------------------------
# Eviction attempted only once across multiple failures
# ---------------------------------------------------------------------------

def test_eviction_called_only_once_across_multiple_failures() -> None:
    """evict_resolved_revue_comments is called at most once even if multiple posts fail."""
    adapter = _adapter(limit_reached=True)
    adapter.post_review_comment.return_value = None
    adapter.evict_resolved_revue_comments.return_value = 5

    state: list[bool] = [False]

    # Simulate two separate posting attempts sharing the same state
    _post_or_evict_and_retry(adapter, 85, _position(), "body1", state)
    adapter.comment_limit_reached = True  # reset as if limit persists
    _post_or_evict_and_retry(adapter, 85, _position(), "body2", state)

    adapter.evict_resolved_revue_comments.assert_called_once()
