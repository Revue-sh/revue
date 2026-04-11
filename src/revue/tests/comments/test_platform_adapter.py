"""Unit tests for BitbucketAdapter — TC9, TC10, TC11 (REVUE-112)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from revue.comments.platform_adapter import BitbucketAdapter


@pytest.fixture()
def adapter() -> BitbucketAdapter:
    return BitbucketAdapter(username="test_user", app_password="test_pass")


# ---------------------------------------------------------------------------
# TC9: get_comment_replies — returns replies filtered by parent.id
# ---------------------------------------------------------------------------

def test_get_comment_replies_filters_by_parent_id(adapter) -> None:
    """TC9: get_comment_replies returns only comments with parent.id == comment_id."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "values": [
            {"id": 201, "content": {"raw": "This is a reply"}, "parent": {"id": 100}},
            {"id": 202, "content": {"raw": "Another reply"}, "parent": {"id": 100}},
            {"id": 203, "content": {"raw": "Reply to different comment"}, "parent": {"id": 999}},
            {"id": 204, "content": {"raw": "Top-level comment"}, "parent": {}},
            {"id": 205, "content": {"raw": "No parent key"}},
        ]
    }

    with patch("httpx.get", return_value=mock_response) as mock_get:
        replies = adapter.get_comment_replies("workspace", "repo", 42, "100")

    mock_get.assert_called_once()
    call_url = mock_get.call_args[0][0]
    assert "/pullrequests/42/comments" in call_url

    assert len(replies) == 2
    reply_ids = {r["id"] for r in replies}
    assert reply_ids == {201, 202}


def test_get_comment_replies_returns_empty_when_no_replies(adapter) -> None:
    """TC9 edge: empty list when no comment has the target parent.id."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "values": [
            {"id": 10, "content": {"raw": "top-level"}, "parent": {}},
        ]
    }

    with patch("httpx.get", return_value=mock_response):
        replies = adapter.get_comment_replies("workspace", "repo", 7, "999")

    assert replies == []


# ---------------------------------------------------------------------------
# TC10: resolve_comment — POST to correct URL; True on 200, False on 400
# ---------------------------------------------------------------------------

def test_resolve_comment_returns_true_on_200(adapter) -> None:
    """TC10: resolve_comment returns True when Bitbucket responds 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = adapter.resolve_comment("workspace", "repo", 42, "100")

    assert result is True
    call_url = mock_post.call_args[0][0]
    assert "/pullrequests/42/comments/100/resolve" in call_url


def test_resolve_comment_returns_false_on_400_without_raising(adapter) -> None:
    """TC10: resolve_comment returns False on 400 (non-inline comment) — no exception."""
    mock_response = MagicMock()
    mock_response.status_code = 400

    with patch("httpx.post", return_value=mock_response):
        result = adapter.resolve_comment("workspace", "repo", 42, "100")

    assert result is False


def test_resolve_comment_uses_pr_number_in_url(adapter) -> None:
    """TC10: The URL includes the PR number at the correct position."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.post", return_value=mock_response) as mock_post:
        adapter.resolve_comment("myworkspace", "myrepo", 99, "555")

    url = mock_post.call_args[0][0]
    assert "myworkspace/myrepo/pullrequests/99/comments/555/resolve" in url


# ---------------------------------------------------------------------------
# TC11: post_reply — uses correct URL with pr_number and parent field
# ---------------------------------------------------------------------------

def test_post_reply_uses_correct_url_with_pr_number(adapter) -> None:
    """TC11: post_reply calls the correct PR-scoped comments endpoint."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"id": 300}

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = adapter.post_reply("workspace", "repo", 42, "100", None, "Great point!")

    assert result == "300"
    call_url = mock_post.call_args[0][0]
    assert "/pullrequests/42/comments" in call_url
    # Must NOT be the old broken endpoint
    assert "/pullrequests/comments/" not in call_url


def test_post_reply_includes_parent_field(adapter) -> None:
    """TC11: post_reply JSON body contains parent.id set to comment_id."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"id": 301}

    with patch("httpx.post", return_value=mock_response) as mock_post:
        adapter.post_reply("workspace", "repo", 42, "100", None, "Reply text")

    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["parent"]["id"] == 100
    assert payload["content"]["raw"] == "Reply text"


def test_post_reply_returns_new_comment_id(adapter) -> None:
    """TC11: post_reply returns the string ID of the newly created reply."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"id": 12345}

    with patch("httpx.post", return_value=mock_response):
        result = adapter.post_reply("ws", "repo", 1, "50", None, "Ack")

    assert result == "12345"
