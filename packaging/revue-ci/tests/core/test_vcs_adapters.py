#!/usr/bin/env python3
"""Tests for GitHubAdapter and GitLabAdapter (Stories 10 & 11)."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from revue_core.core.github_adapter import GitHubAdapter
from revue_core.core.gitlab_adapter import GitLabAdapter
from revue_core.core.models import FileChange, CodeFix
from revue_core.core.vcs_adapter import DiffPosition, VCSAdapter


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
    adapter = GitHubAdapter(token="tok", repo="owner/repo", webhook_secret=secret)
    assert adapter.verify_webhook_signature(payload, sig) is True


def test_github_verify_signature_invalid() -> None:
    """Tampered signature is rejected."""
    payload = b'{"action":"opened"}'
    adapter = GitHubAdapter(token="tok", repo="owner/repo", webhook_secret="s")
    assert adapter.verify_webhook_signature(payload, "sha256=badhex") is False


def test_github_verify_signature_missing_prefix() -> None:
    """Signature without sha256= prefix is rejected."""
    payload = b"{}"
    sig = hmac.new(b"s", payload, hashlib.sha256).hexdigest()
    adapter = GitHubAdapter(token="tok", repo="owner/repo", webhook_secret="s")
    assert adapter.verify_webhook_signature(payload, sig) is False


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


def _mock_urlopen_gitlab(request: object, **kwargs) -> MagicMock:
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


def test_gitlab_403_returns_empty_list() -> None:
    """GitLab 403 on get_diff is caught — returns [] rather than raising."""
    adapter = GitLabAdapter(token="bad", project_id=1)
    with patch("urllib.request.urlopen", side_effect=_make_http_error(403)):
        result = adapter.get_diff(1)
    assert result == []


# =====================================================================
# GitHub — Story 12: get_diff, post_inline_comment, post_summary_comment,
#                    get_existing_comments, binary file handling
# =====================================================================


def _make_resp(body: bytes) -> MagicMock:
    """Build a reusable context-manager mock for urlopen."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_github_get_diff_multiple_files() -> None:
    """3 files with patches → 3 FileChange objects with correct metadata."""
    payload = json.dumps(
        [
            {
                "filename": "src/app.py",
                "status": "modified",
                "additions": 2,
                "deletions": 1,
                "patch": "@@ -1,2 +1,3 @@\n context\n-old\n+new\n+extra",
            },
            {
                "filename": "lib/utils.py",
                "status": "added",
                "additions": 5,
                "deletions": 0,
                "patch": "@@ -0,0 +1,5 @@\n+a\n+b\n+c\n+d\n+e",
            },
            {
                "filename": "old_module.py",
                "status": "removed",
                "additions": 0,
                "deletions": 3,
                "patch": "@@ -1,3 +0,0 @@\n-x\n-y\n-z",
            },
        ]
    ).encode()

    adapter = GitHubAdapter(token="tok", repo="org/repo")
    with patch("urllib.request.urlopen", return_value=_make_resp(payload)):
        changes = adapter.get_diff(5)

    assert len(changes) == 3
    assert all(isinstance(c, FileChange) for c in changes)
    assert changes[0].file_path == "src/app.py"
    assert changes[0].change_type == "modified"
    assert changes[0].additions == 2
    assert changes[1].file_path == "lib/utils.py"
    assert changes[1].change_type == "added"
    assert changes[2].file_path == "old_module.py"
    assert changes[2].change_type == "deleted"


def test_github_get_diff_skips_binary_files() -> None:
    """Files without 'patch' field (binary) are skipped; text files retained."""
    payload = json.dumps(
        [
            {
                "filename": "assets/logo.png",
                "status": "added",
                "additions": 0,
                "deletions": 0,
                # no 'patch' key — binary file
            },
            {
                "filename": "src/main.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1,2 +1,2 @@\n context\n-old\n+new",
            },
        ]
    ).encode()

    adapter = GitHubAdapter(token="tok", repo="org/repo")
    with patch("urllib.request.urlopen", return_value=_make_resp(payload)):
        changes = adapter.get_diff(7)

    assert len(changes) == 1
    assert changes[0].file_path == "src/main.py"


def test_github_post_inline_comment_success() -> None:
    """post_inline_comment returns comment ID string when Review API responds successfully (REVUE-104)."""
    review_body = json.dumps({"id": 10, "state": "COMMENTED"}).encode()

    adapter = GitHubAdapter(token="tok", repo="org/repo")
    position = DiffPosition(file_path="src/app.py", line_number=5, position=3)

    with patch("urllib.request.urlopen", return_value=_make_resp(review_body)):
        result = adapter.post_inline_comment(10, position, "Looks good!")

    assert result == "10"  # Returns comment ID as string (REVUE-104)


def test_github_post_summary_comment_success() -> None:
    """post_summary_comment returns comment ID string when issue comments API succeeds."""
    comment_body = json.dumps({"id": 99, "body": "summary"}).encode()

    adapter = GitHubAdapter(token="tok", repo="org/repo")

    with patch("urllib.request.urlopen", return_value=_make_resp(comment_body)):
        result = adapter.post_summary_comment(10, "Overall LGTM.")

    assert result == "99"


def test_github_get_existing_comments() -> None:
    """get_existing_comments returns the raw list of comment dicts."""
    comments_body = json.dumps(
        [
            {"id": 1, "body": "First comment", "path": "src/app.py"},
            {"id": 2, "body": "Second comment", "path": "README.md"},
        ]
    ).encode()

    adapter = GitHubAdapter(token="tok", repo="org/repo")

    with patch("urllib.request.urlopen", return_value=_make_resp(comments_body)):
        comments = adapter.get_existing_comments(10)

    assert len(comments) == 2
    assert comments[0]["id"] == 1
    assert comments[1]["body"] == "Second comment"


def test_github_get_issue_comments() -> None:
    """get_issue_comments fetches from /issues/{pr_id}/comments, not /pulls/."""
    body = json.dumps([{"id": 42, "body": "## 🤖 Revue — Code Review (Review #1)"}]).encode()
    adapter = GitHubAdapter(token="tok", repo="org/repo")

    with patch("urllib.request.urlopen", return_value=_make_resp(body)) as mock_open:
        result = adapter.get_issue_comments(7)

    assert len(result) == 1
    assert result[0]["id"] == 42
    called_url = mock_open.call_args[0][0].full_url
    assert "/issues/7/comments" in called_url
    assert "/pulls/" not in called_url


def test_github_resolve_inline_comment_uses_graphql() -> None:
    """resolve_inline_comment posts reply then resolves via GraphQL, not PATCH."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")

    threads_resp = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "PRRT_kwAB",
                                "comments": {"nodes": [{"databaseId": 101}]},
                            }
                        ]
                    }
                }
            }
        }
    }).encode()
    resolve_resp = json.dumps({
        "data": {"resolveReviewThread": {"thread": {"isResolved": True}}}
    }).encode()
    reply_resp = json.dumps({"id": 999}).encode()

    responses = [
        _make_resp(reply_resp),   # POST reply
        _make_resp(threads_resp), # GraphQL threads query
        _make_resp(resolve_resp), # GraphQL resolve mutation
    ]

    with patch("urllib.request.urlopen", side_effect=responses):
        result = adapter.resolve_inline_comment(4, "101", "Resolved.")

    assert result is True


def test_github_resolve_inline_comment_no_patch_endpoint() -> None:
    """resolve_inline_comment must NOT call PATCH /pulls/comments/{id}."""
    import urllib.request as _urllib_req

    adapter = GitHubAdapter(token="tok", repo="org/repo")

    threads_resp = json.dumps({
        "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}
    }).encode()
    reply_resp = json.dumps({"id": 1}).encode()

    called_urls: list[str] = []

    def _fake_urlopen(req):
        called_urls.append(req.full_url)
        if "graphql" in req.full_url:
            return _make_resp(threads_resp)
        return _make_resp(reply_resp)

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        adapter.resolve_inline_comment(4, "101", "Resolved.")

    patch_calls = [u for u in called_urls if "PATCH" in str(getattr(
        [r for r in _urllib_req.Request.__mro__], 'method', '')) or "pulls/comments" in u and "replies" not in u]
    # Simpler: no URL should be a PATCH to /pulls/comments without /replies
    for url in called_urls:
        assert not (url.endswith(f"/pulls/comments/101") and "graphql" not in url), (
            f"Unexpected PATCH to REST resolve endpoint: {url}"
        )


# =====================================================================
# GitLab — Story 13: get_diff, post_inline_comment, post_summary_comment,
#                    get_existing_comments (discussions), renamed file
# =====================================================================

_GITLAB_3_CHANGES_RESPONSE = json.dumps(
    {
        "changes": [
            {
                "old_path": "lib/a.rb",
                "new_path": "lib/a.rb",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@ -1,3 +1,4 @@\n context\n+added\n context2\n context3",
            },
            {
                "old_path": "lib/b.rb",
                "new_path": "lib/b.rb",
                "new_file": False,
                "deleted_file": True,
                "renamed_file": False,
                "diff": "@@ -1,2 +0,0 @@\n-line1\n-line2",
            },
            {
                "old_path": "/dev/null",
                "new_path": "lib/c.rb",
                "new_file": True,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@ -0,0 +1,3 @@\n+x\n+y\n+z",
            },
        ]
    }
).encode()

_GITLAB_RENAMED_RESPONSE = json.dumps(
    {
        "changes": [
            {
                "old_path": "lib/old_name.rb",
                "new_path": "lib/new_name.rb",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": True,
                "diff": "@@ -1,2 +1,2 @@\n context\n-old\n+new",
            }
        ]
    }
).encode()

_GITLAB_DISCUSSIONS_RESPONSE = json.dumps(
    [
        {
            "id": "d1",
            "notes": [
                {"id": 1, "body": "Note A"},
                {"id": 2, "body": "Note B"},
            ],
        },
        {
            "id": "d2",
            "notes": [
                {"id": 3, "body": "Note C"},
            ],
        },
    ]
).encode()


def test_gitlab_get_diff_multiple_changes() -> None:
    """3 change entries → 3 FileChange objects with correct types."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", return_value=_make_resp(_GITLAB_3_CHANGES_RESPONSE)):
        changes = adapter.get_diff(5)

    assert len(changes) == 3
    assert all(isinstance(c, FileChange) for c in changes)
    assert changes[0].file_path == "lib/a.rb"
    assert changes[0].change_type == "modified"
    assert changes[0].additions == 1
    assert changes[1].file_path == "lib/b.rb"
    assert changes[1].change_type == "deleted"
    assert changes[1].deletions == 2
    assert changes[2].file_path == "lib/c.rb"
    assert changes[2].change_type == "added"
    assert changes[2].additions == 3


def test_gitlab_get_diff_handles_renamed_file() -> None:
    """Renamed file uses new_path and change_type='modified'."""
    adapter = GitLabAdapter(token="tok", project_id=1)
    with patch("urllib.request.urlopen", return_value=_make_resp(_GITLAB_RENAMED_RESPONSE)):
        changes = adapter.get_diff(1)

    assert len(changes) == 1
    assert changes[0].file_path == "lib/new_name.rb"
    assert changes[0].change_type == "modified"


def test_gitlab_post_inline_comment_with_position() -> None:
    """post_inline_comment sends correct position structure and returns discussion ID (REVUE-104)."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    pos = DiffPosition(
        file_path="src/app.py",
        line_number=10,
        commit_id="abc123def456",
        new_line=10,
    )

    version_resp = [{"base_commit_sha": "base111", "start_commit_sha": "start222", "head_commit_sha": "head333"}]
    discussion_resp = {"id": "disc-1"}
    with patch.object(adapter, "_request", side_effect=[version_resp, discussion_resp]) as mock_req:
        result = adapter.post_inline_comment(1, pos, "Fix this!")

    assert result == "disc-1"  # Returns discussion ID as string (REVUE-104)
    method, path, body = mock_req.call_args[0]
    assert method == "POST"
    assert path.endswith("/discussions")
    assert body["body"] == "Fix this!"
    pos_obj = body["position"]
    assert pos_obj["base_sha"] == "base111"
    assert pos_obj["start_sha"] == "start222"
    assert pos_obj["head_sha"] == "head333"
    assert pos_obj["new_path"] == "src/app.py"
    assert pos_obj["old_path"] == "src/app.py"
    assert pos_obj["new_line"] == 10
    assert pos_obj["position_type"] == "text"


def test_gitlab_post_summary_comment_success() -> None:
    """post_summary_comment returns comment ID string on success."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch.object(adapter, "_request", return_value={"id": 1}):
        result = adapter.post_summary_comment(1, "Overall LGTM")

    assert result == "1"


def test_gitlab_get_existing_comments_flattens_discussions() -> None:
    """get_existing_comments flattens all notes from discussions into one list."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", return_value=_make_resp(_GITLAB_DISCUSSIONS_RESPONSE)):
        comments = adapter.get_existing_comments(1)

    assert len(comments) == 3
    assert comments[0]["body"] == "Note A"
    assert comments[1]["body"] == "Note B"
    assert comments[2]["body"] == "Note C"


def test_gitlab_get_existing_comments_injects_discussion_resolved() -> None:
    """Each note receives _discussion_resolved from its parent discussion.

    Regression: summary was counting resolved-thread findings as 'requiring
    attention'. The fix needs per-note resolution state so dedup can exclude
    resolved-prior findings from the summary total.
    """
    discussions = json.dumps([
        {
            "id": "d1",
            "resolved": True,
            "notes": [{"id": 1, "body": "Note A"}, {"id": 2, "body": "Note B"}],
        },
        {
            "id": "d2",
            "resolved": False,
            "notes": [{"id": 3, "body": "Note C"}],
        },
        {
            "id": "d3",
            # no "resolved" key — absent means unresolved
            "notes": [{"id": 4, "body": "Note D"}],
        },
    ]).encode()
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", return_value=_make_resp(discussions)):
        comments = adapter.get_existing_comments(1)

    assert len(comments) == 4
    assert comments[0]["_discussion_resolved"] is True   # Note A in resolved discussion
    assert comments[1]["_discussion_resolved"] is True   # Note B in resolved discussion
    assert comments[2]["_discussion_resolved"] is False  # Note C in open discussion
    assert comments[3]["_discussion_resolved"] is False  # Note D, absent → False


def test_gitlab_get_existing_comments_injects_discussion_id() -> None:
    """Each note receives _discussion_id from its parent discussion.

    AC5 (auto-resolve) calls resolve_inline_comment with the platform_comment_id
    stored in the fingerprint map.  GitLab's resolve endpoint requires the
    *discussion* ID — not the *note* ID.  Without _discussion_id injection,
    AC5 stores note IDs and silently fails with 404 when trying to resolve.
    """
    discussions = json.dumps([
        {
            "id": "disc-abc",
            "resolved": False,
            "notes": [{"id": 10, "body": "Note A"}, {"id": 11, "body": "Note B"}],
        },
        {
            "id": "disc-xyz",
            "resolved": True,
            "notes": [{"id": 20, "body": "Note C"}],
        },
    ]).encode()
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", return_value=_make_resp(discussions)):
        comments = adapter.get_existing_comments(1)

    assert len(comments) == 3
    assert comments[0]["_discussion_id"] == "disc-abc"  # Note A
    assert comments[1]["_discussion_id"] == "disc-abc"  # Note B shares discussion
    assert comments[2]["_discussion_id"] == "disc-xyz"  # Note C


# =====================================================================
# Story 30 — GitHub Sage Suggested Change
# =====================================================================


def test_github_post_suggested_change_success() -> None:
    """post_suggested_change posts a suggestion in correct markdown format."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    position = DiffPosition(
        file_path="app/auth.py",
        line_number=10,
        position=5,
    )
    code_fix = CodeFix(
        original_lines=["    api_key = \"hardcoded\""],
        fixed_lines=['    api_key = os.getenv("API_KEY")'],
        start_line=10,
        end_line=10,
        confidence=90.0,
        explanation="Moved hardcoded secret to environment variable",
    )

    with patch("urllib.request.urlopen", return_value=_make_resp(b"{}")) as mock:
        result = adapter.post_suggested_change(42, position, code_fix)

    assert result is True
    # Verify the request payload
    call_args = mock.call_args[0][0]
    body_sent = json.loads(call_args.data.decode())
    
    assert body_sent["event"] == "COMMENT"
    assert len(body_sent["comments"]) == 1
    comment_body = body_sent["comments"][0]["body"]
    
    # Check suggestion block
    assert "```suggestion" in comment_body
    assert 'os.getenv("API_KEY")' in comment_body
    assert "Moved hardcoded secret" in comment_body
    assert "confidence: 90%" in comment_body


def test_github_post_suggested_change_multiline() -> None:
    """Multi-line suggestions should include all fixed lines."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    position = DiffPosition(
        file_path="app/auth.py",
        line_number=10,
        position=5,
    )
    code_fix = CodeFix(
        original_lines=["    # Old", "    api_key = \"bad\"", "    "],
        fixed_lines=["    # Fixed", '    api_key = os.getenv("API_KEY")', "    "],
        start_line=10,
        end_line=12,
        confidence=85.0,
        explanation="Fixed multi-line issue",
    )

    with patch("urllib.request.urlopen", return_value=_make_resp(b"{}")) as mock:
        result = adapter.post_suggested_change(42, position, code_fix)

    assert result is True
    call_args = mock.call_args[0][0]
    body_sent = json.loads(call_args.data.decode())
    comment_body = body_sent["comments"][0]["body"]
    
    # All three lines should be in suggestion
    assert "# Fixed" in comment_body
    assert 'os.getenv("API_KEY")' in comment_body


def test_github_post_suggested_change_api_error() -> None:
    """API error should return False and not raise."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    position = DiffPosition(file_path="app/auth.py", line_number=10, position=5)
    code_fix = CodeFix(
        original_lines=["old"],
        fixed_lines=["new"],
        start_line=10,
        end_line=10,
        confidence=80.0,
        explanation="Fix",
    )

    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        result = adapter.post_suggested_change(42, position, code_fix)

    assert result is False


# =====================================================================
# Story 31 — GitLab Sage Apply Suggestion
# =====================================================================


def test_gitlab_post_apply_suggestion_success() -> None:
    """post_apply_suggestion posts suggestion in GitLab syntax."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    position = DiffPosition(
        file_path="app/auth.py",
        line_number=10,
        line_code="abc123",
    )
    code_fix = CodeFix(
        original_lines=["    api_key = \"hardcoded\""],
        fixed_lines=['    api_key = os.getenv("API_KEY")'],
        start_line=10,
        end_line=10,
        confidence=92.0,
        explanation="Replaced hardcoded secret",
    )

    with patch("urllib.request.urlopen", return_value=_make_resp(b"{}")) as mock:
        result = adapter.post_apply_suggestion(5, position, code_fix)

    assert result is True
    call_args = mock.call_args[0][0]
    body_sent = json.loads(call_args.data.decode())
    
    # Check suggestion block with GitLab syntax
    comment_body = body_sent["body"]
    assert "```suggestion:-1+1" in comment_body
    assert 'os.getenv("API_KEY")' in comment_body
    assert "Replaced hardcoded secret" in comment_body
    assert "confidence: 92%" in comment_body


def test_gitlab_post_apply_suggestion_multiline() -> None:
    """Multi-line suggestions use correct :-X+Y syntax."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    position = DiffPosition(
        file_path="app/auth.py",
        line_number=10,
        line_code="abc123",
    )
    code_fix = CodeFix(
        original_lines=["    # Old", "    api_key = \"bad\""],
        fixed_lines=["    # Fixed", '    api_key = os.getenv("API_KEY")', "    # Done"],
        start_line=10,
        end_line=11,
        confidence=88.0,
        explanation="Multi-line fix",
    )

    with patch("urllib.request.urlopen", return_value=_make_resp(b"{}")) as mock:
        result = adapter.post_apply_suggestion(5, position, code_fix)

    assert result is True
    call_args = mock.call_args[0][0]
    body_sent = json.loads(call_args.data.decode())
    comment_body = body_sent["body"]
    
    # 2 lines removed, 3 lines added
    assert "```suggestion:-2+3" in comment_body
    assert "# Fixed" in comment_body
    assert 'os.getenv("API_KEY")' in comment_body
    assert "# Done" in comment_body


def test_gitlab_post_apply_suggestion_api_error() -> None:
    """API error should return False and not raise."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    position = DiffPosition(file_path="app/auth.py", line_number=10)
    code_fix = CodeFix(
        original_lines=["old"],
        fixed_lines=["new"],
        start_line=10,
        end_line=10,
        confidence=80.0,
        explanation="Fix",
    )

    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        result = adapter.post_apply_suggestion(5, position, code_fix)

    assert result is False


# =====================================================================
# [74] post_review_comment — naming fix + backward-compat alias
# =====================================================================

def test_github_post_review_comment_canonical_name() -> None:
    """post_review_comment (canonical name) returns comment ID string (REVUE-104)."""
    review_body = json.dumps({"id": 10, "state": "COMMENTED"}).encode()
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    position = DiffPosition(file_path="src/app.py", line_number=5, position=3)
    with patch("urllib.request.urlopen", return_value=_make_resp(review_body)):
        result = adapter.post_review_comment(10, position, "Looks good!")
    assert result == "10"


def test_github_post_inline_comment_alias_still_works() -> None:
    """post_inline_comment alias continues to work, returns comment ID string (REVUE-104)."""
    review_body = json.dumps({"id": 11, "state": "COMMENTED"}).encode()
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    position = DiffPosition(file_path="src/app.py", line_number=5, position=3)
    with patch("urllib.request.urlopen", return_value=_make_resp(review_body)):
        result = adapter.post_inline_comment(10, position, "Still works!")
    assert result == "11"


def test_gitlab_post_review_comment_canonical_name() -> None:
    """GitLab post_review_comment returns discussion ID string (REVUE-104)."""
    versions_resp = json.dumps([{"base_commit_sha": "b1", "start_commit_sha": "s1", "head_commit_sha": "h1"}]).encode()
    discussion_resp = json.dumps({"id": "abc123"}).encode()
    adapter = GitLabAdapter(token="tok", project_id=42)
    position = DiffPosition(file_path="lib/auth.rb", line_number=10)
    with patch("urllib.request.urlopen", side_effect=[_make_resp(versions_resp), _make_resp(discussion_resp)]):
        result = adapter.post_review_comment(1, position, "Fix this")
    assert result == "abc123"


def test_gitlab_post_inline_comment_alias_still_works() -> None:
    """GitLab post_inline_comment alias returns discussion ID string (REVUE-104)."""
    versions_resp = json.dumps([{"base_commit_sha": "b2", "start_commit_sha": "s2", "head_commit_sha": "h2"}]).encode()
    discussion_resp = json.dumps({"id": "abc124"}).encode()
    adapter = GitLabAdapter(token="tok", project_id=42)
    position = DiffPosition(file_path="lib/auth.rb", line_number=10)
    with patch("urllib.request.urlopen", side_effect=[_make_resp(versions_resp), _make_resp(discussion_resp)]):
        result = adapter.post_inline_comment(1, position, "Still works!")
    assert result == "abc124"


# =====================================================================
# [74] GitHub pagination — get_diff fetches all pages
# =====================================================================

def test_github_get_diff_single_page() -> None:
    """Single page (< 100 files) — fetches once and returns."""
    files = [
        {"filename": f"src/file_{i}.py", "status": "modified",
         "additions": 1, "deletions": 1,
         "patch": f"@@ -1,2 +1,2 @@\n context\n-old{i}\n+new{i}"}
        for i in range(5)
    ]
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    with patch("urllib.request.urlopen", return_value=_make_resp(json.dumps(files).encode())) as mock:
        changes = adapter.get_diff(1)
    assert len(changes) == 5
    assert mock.call_count == 1


def test_github_get_diff_paginates_multiple_pages() -> None:
    """When page returns 100 files, fetches next page until empty page."""
    page1 = [
        {"filename": f"src/file_{i}.py", "status": "modified",
         "additions": 1, "deletions": 0,
         "patch": f"@@ -1 +1 @@\n+line{i}"}
        for i in range(100)
    ]
    page2 = [
        {"filename": f"src/extra_{i}.py", "status": "added",
         "additions": 1, "deletions": 0,
         "patch": f"@@ -0,0 +1 @@\n+line{i}"}
        for i in range(3)
    ]
    responses = [
        _make_resp(json.dumps(page1).encode()),
        _make_resp(json.dumps(page2).encode()),
    ]
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    with patch("urllib.request.urlopen", side_effect=responses) as mock:
        changes = adapter.get_diff(1)
    assert len(changes) == 103
    assert mock.call_count == 2  # page 1 (100 files) then page 2 (3 files → stop)


def test_github_get_diff_empty_first_page() -> None:
    """Empty response on first page returns empty list."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    with patch("urllib.request.urlopen", return_value=_make_resp(b"[]")):
        changes = adapter.get_diff(1)
    assert changes == []


# =====================================================================
# [74] GitLab set_review_status (MR approval for blocking mode)
# =====================================================================

def test_gitlab_set_review_status_approved() -> None:
    """set_review_status('approved') calls approve endpoint and returns True."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", return_value=_make_resp(b"{}")) as mock:
        result = adapter.set_review_status(5, "approved")
    assert result is True
    url = mock.call_args[0][0].full_url
    assert "/approve" in url


def test_gitlab_set_review_status_unapproved() -> None:
    """set_review_status('unapproved') calls unapprove endpoint and returns True."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", return_value=_make_resp(b"{}")) as mock:
        result = adapter.set_review_status(5, "unapproved")
    assert result is True
    url = mock.call_args[0][0].full_url
    assert "/unapprove" in url


def test_gitlab_set_review_status_invalid_status() -> None:
    """Unknown status logs warning and returns False without API call."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen") as mock:
        result = adapter.set_review_status(5, "request_changes")
    assert result is False
    mock.assert_not_called()


def test_gitlab_set_review_status_api_error_returns_false() -> None:
    """API error is caught and returns False (non-fatal)."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        result = adapter.set_review_status(5, "unapproved")
    assert result is False


# =====================================================================
# [REVUE-104] GitHub — resolve_inline_comment
# =====================================================================


def test_github_resolve_inline_comment_patches_and_replies() -> None:
    """resolve_inline_comment POSTs reply then resolves via GraphQL (not PATCH)."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")

    threads_resp = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"id": "PRRT_abc", "comments": {"nodes": [{"databaseId": 55}]}}
                        ]
                    }
                }
            }
        }
    }).encode()
    resolve_resp = json.dumps({
        "data": {"resolveReviewThread": {"thread": {"isResolved": True}}}
    }).encode()
    reply_resp = json.dumps({"id": 999}).encode()

    responses = [_make_resp(reply_resp), _make_resp(threads_resp), _make_resp(resolve_resp)]
    with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
        result = adapter.resolve_inline_comment(pr_id=10, comment_id="55", reply_body="Fixed!")

    assert result is True
    # First call must be POST reply to /pulls/10/comments/55/replies
    first_req = mock_open.call_args_list[0][0][0]
    assert "/pulls/10/comments/55/replies" in first_req.full_url
    assert first_req.method == "POST"
    # Subsequent calls go to GraphQL
    graphql_calls = [c[0][0] for c in mock_open.call_args_list[1:]]
    assert all("graphql" in r.full_url for r in graphql_calls)


def test_github_resolve_inline_comment_no_reply_body() -> None:
    """resolve_inline_comment skips reply POST when reply_body is empty."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")

    threads_resp = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"id": "PRRT_abc", "comments": {"nodes": [{"databaseId": 55}]}}
                        ]
                    }
                }
            }
        }
    }).encode()
    resolve_resp = json.dumps({
        "data": {"resolveReviewThread": {"thread": {"isResolved": True}}}
    }).encode()

    responses = [_make_resp(threads_resp), _make_resp(resolve_resp)]
    with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
        result = adapter.resolve_inline_comment(pr_id=10, comment_id="55", reply_body="")

    assert result is True
    # No reply POST — only 2 GraphQL calls
    assert len(mock_open.call_args_list) == 2
    assert all("graphql" in c[0][0].full_url for c in mock_open.call_args_list)


def test_github_resolve_inline_comment_returns_false_on_error() -> None:
    """resolve_inline_comment returns False when PATCH fails."""
    adapter = GitHubAdapter(token="tok", repo="org/repo")
    with patch.object(adapter, "_request", side_effect=Exception("Network")):
        result = adapter.resolve_inline_comment(
            pr_id=10, comment_id="55", reply_body=""
        )
    assert result is False


# =====================================================================
# [REVUE-104] GitLab — resolve_inline_comment
# =====================================================================


def test_gitlab_resolve_inline_comment_puts_and_replies() -> None:
    """resolve_inline_comment calls PUT on /discussions/{id} with resolved=True
    and POSTs reply note when reply_body is non-empty."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    calls = []

    def mock_request(method, path, body=None):
        calls.append((method, path, body))
        return {}

    with patch.object(adapter, "_request", side_effect=mock_request):
        result = adapter.resolve_inline_comment(
            pr_id=5, comment_id="disc-abc", reply_body="All good now."
        )

    assert result is True
    assert len(calls) == 2

    # First call: POST reply note
    assert calls[0][0] == "POST"
    assert "/merge_requests/5/discussions/disc-abc/notes" in calls[0][1]
    assert calls[0][2] == {"body": "All good now."}

    # Second call: PUT to resolve
    assert calls[1][0] == "PUT"
    assert "/merge_requests/5/discussions/disc-abc" in calls[1][1]
    assert calls[1][2] == {"resolved": True}


def test_gitlab_resolve_inline_comment_no_reply_body() -> None:
    """resolve_inline_comment skips reply POST when reply_body is empty."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    calls = []

    def mock_request(method, path, body=None):
        calls.append((method, path, body))
        return {}

    with patch.object(adapter, "_request", side_effect=mock_request):
        result = adapter.resolve_inline_comment(
            pr_id=5, comment_id="disc-abc", reply_body=""
        )

    assert result is True
    # Only the PUT call, no reply
    assert len(calls) == 1
    assert calls[0][0] == "PUT"
    assert calls[0][2] == {"resolved": True}


def test_gitlab_resolve_inline_comment_returns_false_on_error() -> None:
    """resolve_inline_comment returns False when PUT fails."""
    adapter = GitLabAdapter(token="tok", project_id=42)
    with patch.object(adapter, "_request", side_effect=Exception("Network")):
        result = adapter.resolve_inline_comment(
            pr_id=5, comment_id="disc-abc", reply_body=""
        )
    assert result is False


# =====================================================================
# DiffPosition dataclass tests (migrated from test_vcs_adapter.py)
# =====================================================================

from revue_core.core.vcs_adapter import (
    translate_github_position,
    translate_gitlab_line_code,
    compute_gitlab_line_code,
)


def test_diff_position_defaults() -> None:
    """Created with file_path + line_number; defaults are sensible."""
    pos = DiffPosition(file_path="src/main.py", line_number=42)
    assert pos.file_path == "src/main.py"
    assert pos.line_number == 42
    assert pos.side == "RIGHT"
    assert pos.position == 0
    assert pos.line_code == ""
    assert pos.commit_id == ""
    assert pos.diff_hunk == ""
    assert pos.new_line is None
    assert pos.old_line is None


def test_diff_position_github_fields() -> None:
    """All GitHub-specific fields are stored correctly."""
    pos = DiffPosition(
        file_path="app.py",
        line_number=10,
        side="RIGHT",
        commit_id="abc123",
        diff_hunk="@@ -1,3 +1,4 @@\n context\n+added",
        position=5,
    )
    assert pos.commit_id == "abc123"
    assert pos.diff_hunk.startswith("@@")
    assert pos.position == 5


def test_diff_position_gitlab_fields() -> None:
    """All GitLab-specific fields are stored correctly."""
    pos = DiffPosition(
        file_path="lib/utils.rb",
        line_number=20,
        line_code="abc_def_20",
        new_line=20,
        old_line=18,
    )
    assert pos.line_code == "abc_def_20"
    assert pos.new_line == 20
    assert pos.old_line == 18


# =====================================================================
# VCSAdapter protocol structural check (migrated from test_vcs_adapter.py)
# =====================================================================


class _MinimalAdapter:
    """Minimal concrete class satisfying VCSAdapter structurally (all methods)."""

    def get_diff(self, pr_id: int) -> list[FileChange]:
        return []

    def post_review_comment(
        self, pr_id: int, position: DiffPosition, body: str
    ) -> str | None:
        return "1"

    def post_review_comment_with_params(
        self, pr_id: int, api_params: dict, body: str, replacement_line_count: int = 1
    ) -> str | None:
        return "1"

    def post_summary_comment(self, pr_id: int, body: str) -> str | None:
        return "1"

    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool:
        return True

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        return []

    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition:
        return DiffPosition(file_path=file_path, line_number=line_number)

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        return True

    def resolve_inline_comment(self, pr_id: int, comment_id: str, reply_body: str) -> bool:
        return True

    def get_thread_replies(self, pr_id: int, comment_id: str) -> list[dict]:
        return []

    def reply_to_comment(self, pr_id: int, comment_id: str, body: str) -> str | None:
        return "1"


class _MissingWebhookAdapter:
    """Adapter that omits verify_webhook_signature — must NOT satisfy protocol."""

    def get_diff(self, pr_id: int) -> list[FileChange]: return []
    def post_review_comment(self, pr_id, position, body) -> str | None: return "1"
    def post_summary_comment(self, pr_id, body) -> str | None: return "1"
    def update_comment(self, pr_id, comment_id, body) -> bool: return True
    def get_existing_comments(self, pr_id) -> list[dict]: return []
    def resolve_position(self, file_path, line_number, diff) -> DiffPosition:
        return DiffPosition(file_path=file_path, line_number=line_number)
    def resolve_inline_comment(self, pr_id, comment_id, reply_body) -> bool: return True


def test_vcs_adapter_protocol_structural() -> None:
    """A class implementing all methods passes isinstance check."""
    adapter = _MinimalAdapter()
    assert isinstance(adapter, VCSAdapter)


def test_vcs_adapter_protocol_missing_webhook_fails() -> None:
    """A class omitting verify_webhook_signature fails the protocol check."""
    adapter = _MissingWebhookAdapter()
    assert not isinstance(adapter, VCSAdapter)


# =====================================================================
# GitHub position translation (migrated from test_vcs_adapter.py)
# =====================================================================


def test_translate_github_position_simple() -> None:
    """Single hunk — line in the first (and only) hunk returns correct position."""
    diff = (
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        " line2\n"
        "+new_line3\n"
        " line3\n"
    )
    pos = translate_github_position("app.py", 3, diff)
    assert pos.position == 4  # @@ header(1), line1(2), line2(3), +new_line3(4)
    assert pos.file_path == "app.py"
    assert pos.line_number == 3


def test_translate_github_position_multi_hunk() -> None:
    """Two @@ headers — line in second hunk, position counts across both."""
    diff = (
        "@@ -1,2 +1,3 @@\n"
        " line1\n"
        "+added_a\n"
        " line2\n"
        "@@ -10,2 +11,3 @@\n"
        " line10\n"
        "+added_b\n"
        " line11\n"
    )
    # Second hunk starts new-file line at 11.  added_b is new-file line 12.
    pos = translate_github_position("app.py", 12, diff)
    # Hunk1: header(1) + line1(2) + added_a(3) + line2(4) = 4
    # Hunk2: header(5) + line10(6) + added_b(7)
    assert pos.position == 7
    assert pos.line_number == 12


# =====================================================================
# GitLab line_code translation (migrated from test_vcs_adapter.py)
# =====================================================================


def test_translate_gitlab_line_code_format() -> None:
    """Legacy stub: returned string matches [0-9a-f]{8}_d+_d+ format."""
    import re
    code = translate_gitlab_line_code("base123", "head456", "f.py", 42)
    assert re.match(r"[0-9a-f]{8}_\d+_\d+", code), f"invalid format: {code!r}"
    assert "42" in code


def test_translate_gitlab_line_code_deterministic() -> None:
    """Same inputs always produce the same output."""
    a = translate_gitlab_line_code("b", "h", "f.py", 7)
    b = translate_gitlab_line_code("b", "h", "f.py", 7)
    assert a == b


# =====================================================================
# compute_gitlab_line_code — real SHA-based line code + diff snapping
# =====================================================================

_SAMPLE_DIFF = """\
@@ -10,4 +12,5 @@
 context_a
+added_line
 context_b
-removed_line
 context_c
"""


def test_compute_gitlab_line_code_format() -> None:
    """line_code matches GitLab's required /[0-9a-f]{8}_\\d+_\\d+/ format."""
    import re
    lc, _, _ = compute_gitlab_line_code("src/app.py", _SAMPLE_DIFF, 13)
    assert re.match(r"[0-9a-f]{8}_\d+_\d+", lc), f"invalid format: {lc!r}"


def test_compute_gitlab_line_code_added_line() -> None:
    """Added (+) line: old_line = 0, new_line = target."""
    lc, new, old = compute_gitlab_line_code("f.py", _SAMPLE_DIFF, 13)
    assert new == 13
    assert old == 0
    assert lc.endswith("_0_13")


def test_compute_gitlab_line_code_context_line() -> None:
    """Context line: old_line is the corresponding old position."""
    # new_line=12 is "context_a" — old_line=10 from @@ -10,4 +12,5 @@
    lc, new, old = compute_gitlab_line_code("f.py", _SAMPLE_DIFF, 12)
    assert new == 12
    assert old == 10
    assert lc.endswith("_10_12")


def test_compute_gitlab_line_code_snaps_to_nearest_hunk_line() -> None:
    """Line outside all hunks is snapped to the closest valid diff position."""
    # new_line=999 is far beyond the diff — must snap to last valid line
    lc, snapped, _ = compute_gitlab_line_code("f.py", _SAMPLE_DIFF, 999)
    assert snapped != 999, "should have snapped to a hunk line"
    assert snapped > 0


def test_compute_gitlab_line_code_deterministic() -> None:
    """Same inputs always produce identical output."""
    a = compute_gitlab_line_code("app.py", _SAMPLE_DIFF, 13)
    b = compute_gitlab_line_code("app.py", _SAMPLE_DIFF, 13)
    assert a == b


def test_compute_gitlab_line_code_empty_diff_fallback() -> None:
    """Empty diff: returns best-effort code without crashing."""
    lc, new, old = compute_gitlab_line_code("f.py", "", 42)
    assert new == 42
    assert old == 0
    assert lc.endswith("_0_42")
