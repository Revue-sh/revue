"""Unit tests for BitbucketAdapter.delete_comment and evict_resolved_revue_comments."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from revue_core.comments.platform_adapter import BitbucketAdapter


@pytest.fixture()
def adapter() -> BitbucketAdapter:
    return BitbucketAdapter(
        username="user",
        app_password="pass",
        workspace="ws",
        repo_slug="repo",
    )


# ---------------------------------------------------------------------------
# delete_comment
# ---------------------------------------------------------------------------

def test_delete_comment_success(adapter: BitbucketAdapter) -> None:
    """delete_comment returns True on 204 No Content."""
    resp = MagicMock()
    resp.status_code = 204
    resp.raise_for_status = MagicMock()

    with patch("httpx.delete", return_value=resp):
        assert adapter.delete_comment(42, "100") is True


def test_delete_comment_404_is_success(adapter: BitbucketAdapter) -> None:
    """delete_comment returns True when the comment is already gone (404)."""
    resp = MagicMock()
    resp.status_code = 404

    with patch("httpx.delete", return_value=resp):
        assert adapter.delete_comment(42, "100") is True


def test_delete_comment_403_is_silent_skip(adapter: BitbucketAdapter) -> None:
    """delete_comment returns False silently on 403 — comment owned by a different token."""
    import httpx as _httpx
    resp = MagicMock(spec=_httpx.Response)
    resp.status_code = 403
    error = _httpx.HTTPStatusError("403", request=MagicMock(), response=resp)

    with patch("httpx.delete", side_effect=error), \
         patch("revue_core.core.log.Log.cli.warning") as mock_warn, \
         patch("revue_core.core.log.Log.cli.debug") as mock_debug:
        assert adapter.delete_comment(42, "100") is False

    mock_warn.assert_not_called()
    mock_debug.assert_called_once()


def test_delete_comment_non_403_http_error_warns(adapter: BitbucketAdapter) -> None:
    """delete_comment returns False and logs a warning on non-403 HTTP errors."""
    import httpx as _httpx
    resp = MagicMock(spec=_httpx.Response)
    resp.status_code = 500
    error = _httpx.HTTPStatusError("500", request=MagicMock(), response=resp)

    with patch("httpx.delete", side_effect=error), \
         patch("revue_core.core.log.Log.cli.warning") as mock_warn:
        assert adapter.delete_comment(42, "100") is False

    mock_warn.assert_called_once()


def test_delete_comment_network_error_warns(adapter: BitbucketAdapter) -> None:
    """delete_comment returns False and logs a warning on network/HTTP error."""
    with patch("httpx.delete", side_effect=Exception("timeout")), \
         patch("revue_core.core.log.Log.cli.warning") as mock_warn:
        assert adapter.delete_comment(42, "100") is False

    mock_warn.assert_called_once()
    assert "delete_comment" in mock_warn.call_args[0][0]


# ---------------------------------------------------------------------------
# evict_resolved_revue_comments
# ---------------------------------------------------------------------------

def test_evict_returns_zero_when_pr_has_no_comments(adapter: BitbucketAdapter) -> None:
    """evict returns 0 immediately when get_existing_comments returns []."""
    with patch.object(adapter, "get_existing_comments", return_value=[]):
        assert adapter.evict_resolved_revue_comments(42) == 0


def test_evict_returns_zero_when_no_revue_comments(adapter: BitbucketAdapter) -> None:
    """evict returns 0 when no comments contain the revue:fp: sentinel."""
    comments = [
        {"id": 1, "content": {"raw": "plain comment"}, "created_on": "2026-01-01"},
    ]
    with patch.object(adapter, "get_existing_comments", return_value=comments):
        assert adapter.evict_resolved_revue_comments(42) == 0


def test_evict_returns_zero_when_revue_comment_has_no_resolution_reply(adapter: BitbucketAdapter) -> None:
    """evict returns 0 when a Revue comment exists but has no ✅ reply."""
    comments = [
        {"id": 10, "content": {"raw": "bug [//]: # (revue:fp:abc)"}, "created_on": "2026-01-01"},
        {"id": 11, "content": {"raw": "developer says: will fix"}, "parent": {"id": 10}, "created_on": "2026-01-02"},
    ]
    with patch.object(adapter, "get_existing_comments", return_value=comments):
        assert adapter.evict_resolved_revue_comments(42) == 0


def test_evict_deletes_reply_before_parent(adapter: BitbucketAdapter) -> None:
    """evict deletes the resolution reply first, then the parent comment."""
    comments = [
        {"id": 10, "content": {"raw": "finding [//]: # (revue:fp:abc)"}, "created_on": "2026-01-01"},
        {"id": 11, "content": {"raw": "✅ Issue appears to be resolved in latest commit."}, "parent": {"id": 10}, "created_on": "2026-01-02"},
    ]
    call_order: list[str] = []

    def fake_delete(pr_id: int, comment_id: str) -> bool:
        call_order.append(comment_id)
        return True

    with patch.object(adapter, "get_existing_comments", return_value=comments), \
         patch.object(adapter, "delete_comment", side_effect=fake_delete):
        result = adapter.evict_resolved_revue_comments(42)

    assert result == 1
    assert call_order.index("11") < call_order.index("10"), "reply must be deleted before parent"


def test_evict_skips_unresolved_threads(adapter: BitbucketAdapter) -> None:
    """evict does not touch threads whose replies contain no resolution marker."""
    comments = [
        {"id": 10, "content": {"raw": "resolved [//]: # (revue:fp:aaa)"}, "created_on": "2026-01-01"},
        {"id": 11, "content": {"raw": "✅ resolved"}, "parent": {"id": 10}, "created_on": "2026-01-01"},
        {"id": 20, "content": {"raw": "open [//]: # (revue:fp:bbb)"}, "created_on": "2026-01-02"},
        {"id": 21, "content": {"raw": "I'll look at this"}, "parent": {"id": 20}, "created_on": "2026-01-02"},
    ]
    deleted: list[str] = []

    def fake_delete(pr_id: int, comment_id: str) -> bool:
        deleted.append(comment_id)
        return True

    with patch.object(adapter, "get_existing_comments", return_value=comments), \
         patch.object(adapter, "delete_comment", side_effect=fake_delete):
        result = adapter.evict_resolved_revue_comments(42)

    assert result == 1
    assert "10" in deleted and "11" in deleted
    assert "20" not in deleted and "21" not in deleted


def test_evict_processes_oldest_thread_first(adapter: BitbucketAdapter) -> None:
    """evict deletes threads in ascending created_on order."""
    comments = [
        {"id": 30, "content": {"raw": "newer [//]: # (revue:fp:ccc)"}, "created_on": "2026-03-01"},
        {"id": 31, "content": {"raw": "✅ resolved"}, "parent": {"id": 30}, "created_on": "2026-03-01"},
        {"id": 10, "content": {"raw": "older [//]: # (revue:fp:aaa)"}, "created_on": "2026-01-01"},
        {"id": 11, "content": {"raw": "✅ resolved"}, "parent": {"id": 10}, "created_on": "2026-01-01"},
    ]
    parent_order: list[str] = []

    def fake_delete(pr_id: int, comment_id: str) -> bool:
        if comment_id in ("10", "30"):
            parent_order.append(comment_id)
        return True

    with patch.object(adapter, "get_existing_comments", return_value=comments), \
         patch.object(adapter, "delete_comment", side_effect=fake_delete):
        adapter.evict_resolved_revue_comments(42)

    assert parent_order == ["10", "30"], "oldest thread parent must be deleted first"


def test_evict_counts_only_successfully_deleted_parents(adapter: BitbucketAdapter) -> None:
    """evict return value counts only parents where delete_comment returned True."""
    comments = [
        {"id": 10, "content": {"raw": "a [//]: # (revue:fp:aaa)"}, "created_on": "2026-01-01"},
        {"id": 11, "content": {"raw": "✅ resolved"}, "parent": {"id": 10}, "created_on": "2026-01-01"},
        {"id": 20, "content": {"raw": "b [//]: # (revue:fp:bbb)"}, "created_on": "2026-02-01"},
        {"id": 21, "content": {"raw": "✅ resolved"}, "parent": {"id": 20}, "created_on": "2026-02-01"},
    ]

    def fake_delete(pr_id: int, comment_id: str) -> bool:
        return comment_id != "20"  # parent 20 fails to delete

    with patch.object(adapter, "get_existing_comments", return_value=comments), \
         patch.object(adapter, "delete_comment", side_effect=fake_delete):
        result = adapter.evict_resolved_revue_comments(42)

    assert result == 1  # only parent 10 counted


def test_evict_recognises_issue_appears_to_be_resolved_marker(adapter: BitbucketAdapter) -> None:
    """evict treats 'Issue appears to be resolved' as a resolution marker (not just ✅)."""
    comments = [
        {"id": 10, "content": {"raw": "x [//]: # (revue:fp:abc)"}, "created_on": "2026-01-01"},
        {"id": 11, "content": {"raw": "Issue appears to be resolved in latest commit."}, "parent": {"id": 10}, "created_on": "2026-01-01"},
    ]
    deleted: list[str] = []

    with patch.object(adapter, "get_existing_comments", return_value=comments), \
         patch.object(adapter, "delete_comment", side_effect=lambda pr, cid: deleted.append(cid) or True):
        result = adapter.evict_resolved_revue_comments(42)

    assert result == 1
    assert "10" in deleted


# ---------------------------------------------------------------------------
# evict — 403 treated as silent skip (no extra scope required)
# ---------------------------------------------------------------------------

def test_evict_silently_skips_threads_where_delete_returns_false(adapter: BitbucketAdapter) -> None:
    """evict counts only successful parent deletes; 403-skipped threads don't increment the count."""
    comments = [
        {"id": 10, "content": {"raw": "a [//]: # (revue:fp:aaa)"}, "created_on": "2026-01-01"},
        {"id": 11, "content": {"raw": "✅ resolved"}, "parent": {"id": 10}, "created_on": "2026-01-01"},
        {"id": 20, "content": {"raw": "b [//]: # (revue:fp:bbb)"}, "created_on": "2026-02-01"},
        {"id": 21, "content": {"raw": "✅ resolved"}, "parent": {"id": 20}, "created_on": "2026-02-01"},
    ]

    def fake_delete(pr_id: int, comment_id: str) -> bool:
        return comment_id == "11" or comment_id == "10"  # thread 10 succeeds, thread 20 gets False

    with patch.object(adapter, "get_existing_comments", return_value=comments), \
         patch.object(adapter, "delete_comment", side_effect=fake_delete):
        result = adapter.evict_resolved_revue_comments(42)

    assert result == 1  # only thread 10 counted
