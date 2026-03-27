#!/usr/bin/env python3
"""Tests for GitHubAdapter and GitLabAdapter (Stories 10 & 11)."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from AIReviewer.core.github_adapter import GitHubAdapter
from AIReviewer.core.gitlab_adapter import GitLabAdapter
from AIReviewer.core.models import FileChange
from AIReviewer.core.vcs_adapter import VCSAdapter


# =====================================================================
# Protocol conformance
# =====================================================================


def test_github_adapter_satisfies_vcs_protocol() -> None:
    """GitHubAdapter is a structural subtype of VCSAdapter."""
    adapter = GitHubAdapter(token="t", repo="o/r")
    assert isinstance(adapter, VCSAdapter)


def test_gitlab_adapter_satisfies_vcs_protocol() -> None:
    """GitLabAdapter is a structural subtype of VCSAdapter."""
    adapter = GitLabAdapter(token="t", project_id=1)
    assert isinstance(adapter, VCSAdapter)


# =====================================================================
# GitHub — verify_webhook_signature
# =====================================================================


def test_github_verify_signature_valid() -> None:
    """Valid HMAC-SHA256 signature passes verification."""
    payload = b'{"action":"opened"}'
    secret = "my-secret"
    sig = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    assert GitHubAdapter.verify_webhook_signature(payload, sig, secret) is True


def test_github_verify_signature_invalid() -> None:
    """Tampered signature is rejected."""
    payload = b'{"action":"opened"}'
    assert (
        GitHubAdapter.verify_webhook_signature(payload, "sha256=badhex", "s")
        is False
    )


def test_github_verify_signature_missing_prefix() -> None:
    """Signature without sha256= prefix is rejected."""
    payload = b"{}"
    sig = hmac.new(b"s", payload, hashlib.sha256).hexdigest()
    assert GitHubAdapter.verify_webhook_signature(payload, sig, "s") is False


# =====================================================================
# GitHub — parse_webhook_event
# =====================================================================


def test_github_parse_pr_opened() -> None:
    """PR opened event is parsed correctly."""
    headers = {"X-GitHub-Event": "pull_request"}
    payload = {
        "action": "opened",
        "pull_request": {"number": 42},
        "repository": {"full_name": "org/repo"},
        "installation": {"id": 99},
    }
    result = GitHubAdapter.parse_webhook_event(headers, payload)
    assert result is not None
    assert result["event_type"] == "pull_request"
    assert result["pr_id"] == 42
    assert result["repo"] == "org/repo"
    assert result["action"] == "opened"
    assert result["installation_id"] == 99


def test_github_parse_non_pr_event() -> None:
    """Star event returns None."""
    headers = {"X-GitHub-Event": "star"}
    assert GitHubAdapter.parse_webhook_event(headers, {}) is None


def test_github_parse_pr_closed_ignored() -> None:
    """PR closed action is not in the handled set."""
    headers = {"X-GitHub-Event": "pull_request"}
    payload = {"action": "closed", "pull_request": {"number": 1}}
    assert GitHubAdapter.parse_webhook_event(headers, payload) is None


def test_github_parse_missing_fields() -> None:
    """PR event without pull_request key returns None."""
    headers = {"X-GitHub-Event": "pull_request"}
    payload = {"action": "opened"}
    assert GitHubAdapter.parse_webhook_event(headers, payload) is None


# =====================================================================
# GitLab — verify_webhook_token
# =====================================================================


def test_gitlab_verify_token_match() -> None:
    """Matching token passes."""
    assert GitLabAdapter.verify_webhook_token("secret123", "secret123") is True


def test_gitlab_verify_token_mismatch() -> None:
    """Mismatched token fails."""
    assert GitLabAdapter.verify_webhook_token("wrong", "secret123") is False


# =====================================================================
# GitLab — parse_webhook_event
# =====================================================================


def test_gitlab_parse_mr_opened() -> None:
    """MR opened event is parsed correctly."""
    headers = {"X-Gitlab-Event": "Merge Request Hook"}
    payload = {
        "object_attributes": {"iid": 7, "action": "open"},
        "project": {"id": 123},
    }
    result = GitLabAdapter.parse_webhook_event(headers, payload)
    assert result is not None
    assert result["event_type"] == "merge_request"
    assert result["pr_id"] == 7
    assert result["project_id"] == 123
    assert result["action"] == "open"


def test_gitlab_parse_mr_closed_returns_none() -> None:
    """MR close action is not handled — returns None."""
    headers = {"X-Gitlab-Event": "Merge Request Hook"}
    payload = {
        "object_attributes": {"iid": 7, "action": "close"},
        "project": {"id": 1},
    }
    assert GitLabAdapter.parse_webhook_event(headers, payload) is None


def test_gitlab_parse_push_event_returns_none() -> None:
    """Push event returns None."""
    headers = {"X-Gitlab-Event": "Push Hook"}
    assert GitLabAdapter.parse_webhook_event(headers, {"ref": "main"}) is None


# =====================================================================
# GitHub — get_diff (mocked HTTP)
# =====================================================================

_GITHUB_FILES_RESPONSE = json.dumps(
    [
        {
            "filename": "src/app.py",
            "status": "modified",
            "additions": 3,
            "deletions": 1,
            "patch": "@@ -1,3 +1,5 @@\n context\n-old\n+new\n+extra\n context2",
        },
        {
            "filename": "README.md",
            "status": "added",
            "additions": 5,
            "deletions": 0,
            "patch": "@@ -0,0 +1,5 @@\n+# Title\n+line2\n+line3\n+line4\n+line5",
        },
    ]
).encode()


def _mock_urlopen_github(request: object) -> MagicMock:
    """Return a context-manager mock that yields the files response."""
    resp = MagicMock()
    resp.read.return_value = _GITHUB_FILES_RESPONSE
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_github_get_diff_parses_files() -> None:
    """Mocked GitHub /pulls/{id}/files returns list[FileChange]."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    with patch("urllib.request.urlopen", side_effect=_mock_urlopen_github):
        changes = adapter.get_diff(1)

    assert len(changes) == 2
    assert all(isinstance(c, FileChange) for c in changes)

    assert changes[0].file_path == "src/app.py"
    assert changes[0].change_type == "modified"
    assert changes[0].additions == 3
    assert changes[0].deletions == 1

    assert changes[1].file_path == "README.md"
    assert changes[1].change_type == "added"


# =====================================================================
# GitLab — get_diff (mocked HTTP)
# =====================================================================

_GITLAB_CHANGES_RESPONSE = json.dumps(
    {
        "changes": [
            {
                "old_path": "lib/utils.rb",
                "new_path": "lib/utils.rb",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@ -1,3 +1,4 @@\n context\n+added_line\n context2\n context3",
            },
            {
                "old_path": "/dev/null",
                "new_path": "new_file.py",
                "new_file": True,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@ -0,0 +1,2 @@\n+hello\n+world",
            },
        ]
    }
).encode()


def _mock_urlopen_gitlab(request: object) -> MagicMock:
    """Return a context-manager mock that yields the changes response."""
    resp = MagicMock()
    resp.read.return_value = _GITLAB_CHANGES_RESPONSE
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_gitlab_get_diff_parses_changes() -> None:
    """Mocked GitLab /merge_requests/{id}/changes returns list[FileChange]."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", side_effect=_mock_urlopen_gitlab):
        changes = adapter.get_diff(1)

    assert len(changes) == 2
    assert all(isinstance(c, FileChange) for c in changes)

    assert changes[0].file_path == "lib/utils.rb"
    assert changes[0].change_type == "modified"
    assert changes[0].additions == 1
    assert changes[0].deletions == 0

    assert changes[1].file_path == "new_file.py"
    assert changes[1].change_type == "added"
    assert changes[1].additions == 2


# =====================================================================
# Error handling
# =====================================================================


def _make_http_error(code: int) -> MagicMock:
    """Create a side_effect that raises urllib.error.HTTPError."""
    import urllib.error

    def raiser(request: object) -> None:
        raise urllib.error.HTTPError(
            url="http://x", code=code, msg="err", hdrs={}, fp=io.BytesIO(b"")  # type: ignore[arg-type]
        )

    return raiser


def test_github_401_raises_valueerror() -> None:
    """GitHub 401 raises ValueError."""
    adapter = GitHubAdapter(token="bad", repo="o/r")
    with patch("urllib.request.urlopen", side_effect=_make_http_error(401)):
        with pytest.raises(ValueError, match="auth error"):
            adapter.get_diff(1)


def test_github_404_raises_runtimeerror() -> None:
    """GitHub 404 raises RuntimeError."""
    adapter = GitHubAdapter(token="t", repo="o/r")
    with patch("urllib.request.urlopen", side_effect=_make_http_error(404)):
        with pytest.raises(RuntimeError, match="not found"):
            adapter.get_diff(1)


def test_github_500_raises_runtimeerror() -> None:
    """GitHub 500 raises RuntimeError."""
    adapter = GitHubAdapter(token="t", repo="o/r")
    with patch("urllib.request.urlopen", side_effect=_make_http_error(500)):
        with pytest.raises(RuntimeError, match="server error"):
            adapter.get_diff(1)


def test_gitlab_403_raises_valueerror() -> None:
    """GitLab 403 raises ValueError."""
    adapter = GitLabAdapter(token="bad", project_id=1)
    with patch("urllib.request.urlopen", side_effect=_make_http_error(403)):
        with pytest.raises(ValueError, match="auth error"):
            adapter.get_diff(1)
